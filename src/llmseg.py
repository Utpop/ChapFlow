#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM 辅助章节切分
=================

定位
    本地规则（txtconv.match_heading / SplitRules）擅长识别**写法固定**的标题，
    但有两种情况它没办法：
        · 误判：正文里引用「第三卷写到一半就断了」这种句子被当成标题；
        · 漏判：没有「第X章」字样、只靠排版区分的短标题（「一、初见」「夜雨」）。
    这两类判断是语义问题，交给大模型；**但只让它裁决候选行**，不让它读全文。

为什么只裁决候选行
    1. 小说动辄几十万字，全文进模型既贵又必然截断；
    2. 模型只输出「行号 → 是不是标题」，正文一个字都不经过它，
       所以不存在改写、丢字、幻觉内容混进正文的风险；
    3. 候选行数量与章节数同阶，成本可预期。

与既有切分逻辑的关系
    txtconv.split_chapters() 是「先扫出 marks，再按 marks 切段」。
    本模块只对 **marks** 做增删，然后原样交回去 —— 切段、段落归并、
    字数兜底全走原来的代码，接口与输出结构不变。

绝不抛异常（默认）
    没配密钥 / 断网 / 超时 / 返回不可解析 → 一律回退本地规则结果，
    并在 stats["llm"] 里写明原因。fallback=False 时才升级成 TxtError。

纯标准库：只用 urllib / json，和 txtconv 一样不引入第三方依赖。
"""

from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# 默认值
# ---------------------------------------------------------------------------

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
DEFAULT_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 2
DEFAULT_BATCH = 40          # 一次请求带多少条候选行
DEFAULT_MAX_CANDIDATES = 300  # 弱候选（规则没认出来的短行）最多取多少条
DEFAULT_MAX_CHARS = 6000    # 单次请求里候选区的字面上限
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 1200
DEFAULT_GUARD_RATIO = 3.0   # 裁决后的标题数偏离基线超过这个倍数就整体回退

MAX_RESPONSE_BYTES = 2_000_000   # 单次响应的读取上限（模型回复只该是几 KB）

# scope：refine=只复核本地已认出的标题；augment=同时寻找漏判；auto=本地没认出任何标题时才找漏判
SCOPES = ("auto", "refine", "augment")

PROTOCOLS = ("openai_chat", "openai_responses", "anthropic_messages")

CONFIG_ENV = "CHAPFLOW_LLM_CONFIG"
CONFIG_NAME = "llm.json"

SYSTEM_PROMPT = (
    "你是中文图书的排版编辑。用户会给你一本书里若干「候选行」，"
    "请判断每一行是不是章节标题。只输出 JSON，不要解释。"
)

DEFAULT_INSTRUCTION = """\
判定为标题：
· 独立成行、很短（一般不超过 30 字），不以句号/逗号/问号/省略号等标点结尾；
· 形如「第一章 夜雨」「第二卷 风起」「序章」「楔子」「尾声」「番外 一」「Chapter 5」；
· 或以「一、」「（二）」「1.」这类编号起头，且编号后面紧跟的是标题文字。

判定为正文（不是标题）：
· 完整的句子、对话、心理描写；
· 只是在句子里提到「第X章/第X卷」的行；
· 页码、目录页条目、作者署名、版权信息、网站广告行。

层级：卷 / 部 / 篇 / 集 → lv=1；章 / 回 / 节 / 话 / 序章 / 番外 → lv=2。
标题文字 t：去掉首尾空白与多余空格，保留原文写法，不要改写、不要翻译。
"""

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def config_path(custom: str = "") -> str:
    """配置文件位置：显式路径 > CHAPFLOW_LLM_CONFIG > %APPDATA%\\ChapFlow\\llm.json"""
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    env = os.environ.get(CONFIG_ENV)
    if env:
        return os.path.abspath(os.path.expanduser(env))
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "ChapFlow", CONFIG_NAME)


def _pick(env: Dict[str, str], *names, default: str = "") -> str:
    for n in names:
        v = env.get(n)
        if v:
            return v
    return default


def _to_bool(value: Any, fallback: bool = False) -> bool:
    """宽松布尔解析：手改配置写成 "false" / "off" / "0" 时不能当成 True。

    bool("false") 是 True，早期实现直接 bool() 会把用户的「关掉」读成「打开」，
    于是本该完全不联网的配置照样发请求。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("0", "false", "off", "no", "n", "none", ""):
        return False
    if text in ("1", "true", "on", "yes", "y"):
        return True
    return fallback


def _to_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _to_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


# ---------------------------------------------------------------------------
# 密钥落盘保护（Windows DPAPI，绑定当前用户；不可用时回退明文并保持可用性）
# ---------------------------------------------------------------------------


def _protect_secret(text: str) -> Optional[str]:
    """用 DPAPI 加密文本，返回 base64 密文；平台不支持或失败返回 None。"""
    if not text or os.name != "nt":
        return None
    try:
        import base64
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = text.encode("utf-8")
        arr = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
        src = DATA_BLOB(len(raw), ctypes.cast(arr, ctypes.POINTER(ctypes.c_byte)))
        dst = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        if not crypt32.CryptProtectData(ctypes.byref(src), u"ChapFlow LLM API Key",
                                        None, None, None, 0, ctypes.byref(dst)):
            return None
        try:
            blob = ctypes.string_at(dst.pbData, dst.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(dst.pbData)
        return base64.b64encode(blob).decode("ascii")
    except Exception:  # pragma: no cover - 环境缺 crypt32 等极端情况
        return None


def _unprotect_secret(token: str) -> Optional[str]:
    """解开 _protect_secret 的密文；失败返回 None。"""
    if not token or os.name != "nt":
        return None
    try:
        import base64
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_byte))]

        raw = base64.b64decode(token)
        arr = (ctypes.c_char * len(raw)).from_buffer_copy(raw)
        src = DATA_BLOB(len(raw), ctypes.cast(arr, ctypes.POINTER(ctypes.c_byte)))
        dst = DATA_BLOB()
        crypt32 = ctypes.windll.crypt32
        # CRYPTPROTECT_UI_FORBIDDEN：绝不弹系统对话框
        if not crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None,
                                          0x1, ctypes.byref(dst)):
            return None
        try:
            blob = ctypes.string_at(dst.pbData, dst.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(dst.pbData)
        return blob.decode("utf-8")
    except Exception:  # pragma: no cover
        return None


@dataclass
class LLMOptions:
    """LLM 切分选项。全部可来自配置文件 / 环境变量 / 显式传参。"""

    enabled: bool = False          # 总开关：False 时完全不联网
    provider: str = "openai"       # openai（HTTP 兼容）/ ollama
    protocol: str = "openai_chat"  # openai_chat / openai_responses / anthropic_messages
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    api_key: str = ""
    timeout: int = DEFAULT_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    batch: int = DEFAULT_BATCH
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    max_chars: int = DEFAULT_MAX_CHARS
    temperature: float = DEFAULT_TEMPERATURE
    max_tokens: int = DEFAULT_MAX_TOKENS
    scope: str = "auto"
    fallback: bool = True          # 失败时回退本地规则；False 则抛 TxtError
    guard_ratio: float = DEFAULT_GUARD_RATIO
    instruction: str = ""          # 空串 = 用 DEFAULT_INSTRUCTION
    headers: Dict[str, str] = field(default_factory=dict)

    # -- 校验 ------------------------------------------------------------
    def problem(self) -> str:
        """返回一句「为什么用不了」，空串表示可用。"""
        if not self.enabled:
            return "未启用 LLM 辅助切分"
        if self.provider == "ollama":
            if not (self.base_url and self.model):
                return "本地 Ollama 需要服务地址与模型名"
            if not re.match(r"^https?://", self.base_url, re.I):
                return "服务地址必须以 http:// 或 https:// 开头（当前：%s）" % self.base_url
            return ""
        if not self.base_url:
            return "未配置服务地址（base_url）"
        # urllib 对 file:// 的 POST 会直接读本地文件，内容片段还会经错误信息
        # 回显到界面，等于把本机文件当成「模型回复」——只接受 http(s)。
        if not re.match(r"^https?://", self.base_url, re.I):
            return "服务地址必须以 http:// 或 https:// 开头（当前：%s）" % self.base_url
        if not self.api_key:
            return ("未配置 API Key：设置环境变量 CHAPFLOW_LLM_API_KEY"
                    "（或 OPENAI_API_KEY / DEEPSEEK_API_KEY），"
                    "或在界面的「LLM 设置」里填写")
        if not self.model:
            return "未配置模型名（model）"
        return ""

    def normalized(self) -> "LLMOptions":
        """把明显越界的值拉回合法范围（配置是给人手改的，不能假定它正确）。"""
        cfg = LLMOptions(**{f.name: getattr(self, f.name) for f in fields(self)})
        cfg.timeout = max(5, min(600, _to_int(cfg.timeout, DEFAULT_TIMEOUT)))
        cfg.max_retries = max(1, min(6, _to_int(cfg.max_retries, DEFAULT_MAX_RETRIES)))
        cfg.batch = max(1, min(200, _to_int(cfg.batch, DEFAULT_BATCH)))
        cfg.max_candidates = max(0, min(5000, _to_int(cfg.max_candidates, DEFAULT_MAX_CANDIDATES)))
        cfg.max_chars = max(500, min(60000, _to_int(cfg.max_chars, DEFAULT_MAX_CHARS)))
        cfg.max_tokens = max(64, min(32000, _to_int(cfg.max_tokens, DEFAULT_MAX_TOKENS)))
        cfg.guard_ratio = max(1.2, min(20.0, _to_float(cfg.guard_ratio, DEFAULT_GUARD_RATIO)))
        cfg.temperature = max(0.0, min(2.0, _to_float(cfg.temperature, DEFAULT_TEMPERATURE)))
        if cfg.scope not in SCOPES:
            cfg.scope = "auto"
        if cfg.protocol not in PROTOCOLS:
            cfg.protocol = "openai_chat"
        cfg.base_url = (cfg.base_url or "").strip()
        cfg.model = (cfg.model or "").strip()
        cfg.api_key = (cfg.api_key or "").strip()
        return cfg

    # -- 序列化 ----------------------------------------------------------
    def to_dict(self, with_key: bool = False) -> Dict[str, Any]:
        data = {
            "enabled": bool(self.enabled),
            "provider": self.provider,
            "protocol": self.protocol,
            "base_url": self.base_url,
            "model": self.model,
            "timeout": int(self.timeout),
            "max_retries": int(self.max_retries),
            "batch": int(self.batch),
            "max_candidates": int(self.max_candidates),
            "max_chars": int(self.max_chars),
            "temperature": float(self.temperature),
            "max_tokens": int(self.max_tokens),
            "scope": self.scope,
            "fallback": bool(self.fallback),
            "guard_ratio": float(self.guard_ratio),
            "instruction": self.instruction or "",
            "headers": dict(self.headers or {}),
            "has_key": bool(self.api_key),
        }
        if with_key and self.api_key:
            enc = _protect_secret(self.api_key)
            if enc:
                data["api_key_enc"] = enc      # Windows DPAPI 密文，不明文落盘
            else:
                data["api_key"] = self.api_key  # 平台无 DPAPI 时回退明文，保证可用
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "LLMOptions":
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        for key in ("provider", "protocol", "base_url", "model", "api_key",
                    "scope", "instruction"):
            val = data.get(key)
            if isinstance(val, str):
                setattr(cfg, key, val)
        enc = data.get("api_key_enc")
        if isinstance(enc, str) and enc.strip() and not cfg.api_key:
            opened = _unprotect_secret(enc.strip())
            if opened:
                cfg.api_key = opened
        for key, cast in (("timeout", _to_int), ("max_retries", _to_int),
                          ("batch", _to_int), ("max_candidates", _to_int),
                          ("max_chars", _to_int), ("max_tokens", _to_int)):
            if data.get(key) is not None:
                setattr(cfg, key, cast(data.get(key), getattr(cfg, key)))
        if data.get("temperature") is not None:
            cfg.temperature = _to_float(data.get("temperature"), cfg.temperature)
        if data.get("guard_ratio") is not None:
            cfg.guard_ratio = _to_float(data.get("guard_ratio"), cfg.guard_ratio)
        for key in ("enabled", "fallback"):
            if data.get(key) is not None:
                setattr(cfg, key, _to_bool(data.get(key), getattr(cfg, key)))
        headers = data.get("headers")
        if isinstance(headers, dict):
            cfg.headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
        return cfg.normalized()

    @classmethod
    def from_env(cls, **override) -> "LLMOptions":
        """环境变量：CHAPFLOW_LLM_* → OPENAI_* / DEEPSEEK_*（兼容已有习惯）。"""
        env = os.environ
        cfg = cls(
            provider=_pick(env, "CHAPFLOW_LLM_PROVIDER", "LLM_PROVIDER", default="openai"),
            protocol=_pick(env, "CHAPFLOW_LLM_PROTOCOL", "LLM_PROTOCOL",
                           default="openai_chat"),
            base_url=_pick(env, "CHAPFLOW_LLM_BASE_URL", "OPENAI_BASE_URL",
                           "DEEPSEEK_BASE_URL", default=DEFAULT_BASE_URL),
            model=_pick(env, "CHAPFLOW_LLM_MODEL", "OPENAI_MODEL", "DEEPSEEK_MODEL",
                        default=DEFAULT_MODEL),
            api_key=_pick(env, "CHAPFLOW_LLM_API_KEY", "OPENAI_API_KEY",
                          "DEEPSEEK_API_KEY", default=""),
            timeout=_to_int(_pick(env, "CHAPFLOW_LLM_TIMEOUT", default=""), DEFAULT_TIMEOUT),
            max_retries=_to_int(_pick(env, "CHAPFLOW_LLM_RETRIES", default=""),
                                DEFAULT_MAX_RETRIES),
            batch=_to_int(_pick(env, "CHAPFLOW_LLM_BATCH", default=""), DEFAULT_BATCH),
            scope=_pick(env, "CHAPFLOW_LLM_SCOPE", default="auto"),
        )
        switch = _pick(env, "CHAPFLOW_LLM", "CHAPFLOW_LLM_ENABLED", default="")
        if switch:
            cfg.enabled = _to_bool(switch, False)
        raw_headers = _pick(env, "CHAPFLOW_LLM_HEADERS", default="")
        if raw_headers:
            try:
                loaded = json.loads(raw_headers)
            except (ValueError, TypeError):
                loaded = None
            if isinstance(loaded, dict):
                cfg.headers = {str(k): str(v) for k, v in loaded.items() if str(k).strip()}
        for k, v in override.items():
            if v is not None and hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg.normalized()

    # -- 落盘 ------------------------------------------------------------
    def save(self, path: str = "", with_key: bool = True) -> str:
        """写配置文件（自动建目录）。返回路径；失败返回空串，不抛异常。"""
        target = config_path(path)
        try:
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(self.to_dict(with_key=with_key), fh,
                          ensure_ascii=False, indent=2)
        except OSError:
            return ""
        return target

    @classmethod
    def load(cls, path: str = "", with_env: bool = True) -> "LLMOptions":
        """读配置：文件里的非空字段覆盖环境变量，环境变量覆盖内置默认。"""
        cfg = cls.from_env() if with_env else cls()
        try:
            with open(config_path(path), "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            return cfg.normalized()
        if not isinstance(data, dict):
            return cfg.normalized()
        saved = cls.from_dict(data)
        for f in fields(cls):
            val = getattr(saved, f.name)
            if f.name in ("enabled", "fallback"):
                if f.name in data:
                    setattr(cfg, f.name, _to_bool(val, False))
            elif val not in ("", 0, 0.0, {}, None) or f.name in data:
                setattr(cfg, f.name, val)
        return cfg.normalized()


def as_options(llm: Any) -> Optional[LLMOptions]:
    """把外部传进来的 llm 参数统一成 LLMOptions（None 表示不启用）。"""
    if llm is None or llm is False:
        return None
    if isinstance(llm, LLMOptions):
        return llm.normalized()
    if isinstance(llm, dict):
        cfg = LLMOptions.from_dict(llm)
        if "enabled" not in llm:
            cfg.enabled = True
        return cfg
    if isinstance(llm, str):
        cfg = LLMOptions.load(llm)
        cfg.enabled = True
        return cfg
    if hasattr(llm, "judge") or hasattr(llm, "complete"):
        return None  # 客户端对象由调用方自己包装成 Splitter
    return None


# ---------------------------------------------------------------------------
# HTTP 客户端
# ---------------------------------------------------------------------------


class LLMError(RuntimeError):
    """LLM 调用失败（网络 / 鉴权 / 格式），消息可直接展示给用户。"""


def _http_reason(code: int, body: str) -> str:
    detail = ""
    if body:
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            data = None
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                detail = str(err.get("message") or "")
            elif isinstance(err, str):
                detail = err
            if not detail:
                detail = str(data.get("message") or "")
        if not detail:
            detail = body[:160].replace("\n", " ")
    base = {
        400: "请求被拒绝（参数或权限不对）",
        401: "API Key 无效或已过期",
        403: "没有权限访问该模型",
        404: "地址不对（404）：检查服务地址与模型名",
        408: "服务端超时",
        413: "请求内容过大",
        422: "参数不被接受",
        429: "触发限流或额度用尽",
        500: "服务端内部错误",
        502: "网关错误，服务端暂时不可用",
        503: "服务暂时不可用",
    }.get(code, "请求失败")
    return "%s（HTTP %d）%s" % (base, code, ("：" + detail) if detail else "")


class HttpChatClient:
    """OpenAI 兼容 / Anthropic 兼容 / Ollama 三种协议的薄封装。

    三种协议最后都归一成「一段文本」，上层解析不用区分。
    """

    name = "http"

    def __init__(self, opts: LLMOptions):
        self.opts = opts.normalized()

    # -- 端点 ------------------------------------------------------------
    def endpoint(self) -> str:
        base = (self.opts.base_url or "").rstrip("/")
        if self.opts.provider == "ollama" or "/api/chat" in base:
            if base.endswith("/api/chat"):
                return base
            return base + "/api/chat"
        tail = {  # 协议 → 路径尾巴
            "anthropic_messages": "/messages",
            "openai_responses": "/responses",
        }.get(self.opts.protocol, "/chat/completions")
        if base.endswith(tail):
            return base
        if re.search(r"/v\d+$", base):
            return base + tail
        return base + "/v1" + tail

    def headers(self) -> Dict[str, str]:
        head = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.opts.provider == "ollama":
            pass
        elif self.opts.protocol == "anthropic_messages":
            if self.opts.api_key:
                head["x-api-key"] = self.opts.api_key
                head["anthropic-version"] = "2023-06-01"
        elif self.opts.api_key:
            head["Authorization"] = "Bearer %s" % self.opts.api_key
        for key, value in (self.opts.headers or {}).items():
            if str(key).strip():
                head[str(key).strip()] = str(value)
        return head

    # -- 请求体 ----------------------------------------------------------
    def payload(self, system: str, user: str, json_mode: bool = False,
                max_tokens: Optional[int] = None) -> Dict[str, Any]:
        limit = int(max_tokens or self.opts.max_tokens)
        if self.opts.provider == "ollama":
            body: Dict[str, Any] = {
                "model": self.opts.model, "stream": False,
                "options": {"temperature": self.opts.temperature, "num_predict": limit},
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}],
            }
            if json_mode:
                body["format"] = "json"
            return body
        if self.opts.protocol == "anthropic_messages":
            body = {"model": self.opts.model, "max_tokens": limit,
                    "temperature": self.opts.temperature,
                    "messages": [{"role": "user", "content": user}]}
            if system:
                body["system"] = system
            if json_mode:
                body["messages"][0]["content"] += "\n只输出 JSON，不要任何解释文字。"
            return body
        if self.opts.protocol == "openai_responses":
            body = {"model": self.opts.model, "input": user,
                    "max_output_tokens": limit}
            if system:
                body["instructions"] = system
            if json_mode:
                body["text"] = {"format": {"type": "json_object"}}
            return body
        body = {"model": self.opts.model, "temperature": self.opts.temperature,
                "max_tokens": limit,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": user}]}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        return body

    # -- 回复解析 --------------------------------------------------------
    @staticmethod
    def extract(data: Any, protocol: str, provider: str = "openai") -> str:
        if not isinstance(data, dict):
            raise LLMError("回复格式异常：不是 JSON 对象")
        if provider == "ollama":
            msg = data.get("message") or {}
            if isinstance(msg, dict) and msg.get("content") is not None:
                return str(msg.get("content") or "")
            if data.get("response") is not None:
                return str(data.get("response") or "")
            raise LLMError("回复格式异常：%s" % json.dumps(data, ensure_ascii=False)[:160])
        if protocol == "anthropic_messages":
            parts = data.get("content")
            if isinstance(parts, list):
                return "".join(str(p.get("text") or "") for p in parts
                               if isinstance(p, dict))
            raise LLMError("回复格式异常：%s" % json.dumps(data, ensure_ascii=False)[:160])
        if protocol == "openai_responses":
            if isinstance(data.get("output_text"), str):
                return data["output_text"]
            chunks: List[str] = []
            for item in (data.get("output") or []):
                if not isinstance(item, dict):
                    continue
                for part in (item.get("content") or []):
                    if isinstance(part, dict) and part.get("text"):
                        chunks.append(str(part["text"]))
            if chunks:
                return "".join(chunks)
            raise LLMError("回复格式异常：%s" % json.dumps(data, ensure_ascii=False)[:160])
        try:
            return str(data["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            raise LLMError("回复格式异常：%s" % json.dumps(data, ensure_ascii=False)[:160])

    # -- 对外 ------------------------------------------------------------
    def complete(self, system: str, user: str, json_mode: bool = False,
                 max_tokens: Optional[int] = None) -> str:
        body = json.dumps(self.payload(system, user, json_mode, max_tokens),
                          ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.endpoint(), data=body, method="POST",
                                     headers=self.headers())
        try:
            with urllib.request.urlopen(req, timeout=self.opts.timeout) as fh:
                # 加一层读取上限：恶意/失控服务端返回超大响应会把内存撑爆，
                # 而这里本来只解析一个小 JSON。
                raw = fh.read(MAX_RESPONSE_BYTES + 1).decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read().decode("utf-8", "replace")
            except Exception:  # pragma: no cover - 读流失败
                text = ""
            raise LLMError(_http_reason(exc.code, text))
        except urllib.error.URLError as exc:
            # 连接/读取超时都会被 urllib 包成 URLError(socket.timeout) 抛出，
            # 不能一律报「连不上服务器」——先看 reason 是不是超时
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise LLMError("超时（%ds）" % int(self.opts.timeout))
            raise LLMError("连不上服务器：%s" % (reason,))
        except (TimeoutError, socket.timeout):
            raise LLMError("超时（%ds）" % int(self.opts.timeout))
        except OSError as exc:
            raise LLMError("网络错误：%s" % (exc,))
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError("响应过大（超过 %d 字节），已拒绝解析" % MAX_RESPONSE_BYTES)
        try:
            data = json.loads(raw)
        except ValueError:
            raise LLMError("回复不是合法 JSON：%s" % raw[:160].replace("\n", " "))
        return self.extract(data, self.opts.protocol, self.opts.provider)


def make_client(opts: Optional[LLMOptions]) -> Optional[HttpChatClient]:
    """按配置造客户端；配置不全时返回 None（调用方按「未启用」处理，不抛异常）。"""
    if opts is None or opts.problem():
        return None
    return HttpChatClient(opts)


def test_connection(opts: LLMOptions) -> Dict[str, Any]:
    """真发一次最小请求，验证地址 / 密钥 / 模型是否可用。"""
    cfg = opts.normalized()
    cfg.enabled = True
    result: Dict[str, Any] = {"ok": False, "message": "", "latency_ms": 0}
    problem = cfg.problem()
    if problem:
        result["message"] = problem
        return result
    client = HttpChatClient(cfg)
    started = time.time()
    try:
        reply = client.complete("你是一个测试助手。", "回复 OK 两个字母即可。",
                                max_tokens=16)
    except LLMError as exc:
        result["message"] = str(exc)
        result["latency_ms"] = int((time.time() - started) * 1000)
        return result
    result["ok"] = True
    result["latency_ms"] = int((time.time() - started) * 1000)
    head = (reply or "").strip().splitlines()[0][:40] if (reply or "").strip() else ""
    result["message"] = "连接成功（%d ms）%s" % (
        result["latency_ms"], ("· 回复：" + head) if head else "")
    return result


# ---------------------------------------------------------------------------
# 输出解析（模型输出一律不可信）
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.S)
_KV_NUM = re.compile(r'"?i"?\s*[:=]\s*"?(\d+)"?')
_HEAD_TRUE = ("true", "1", "是", "h", "head", "heading", "title", "y", "yes")
_HEAD_FALSE = ("false", "0", "否", "b", "body", "no", "n", "text")


def _slice_json(text: str) -> str:
    """从夹杂解释文字的回复里切出第一个完整 JSON 数组/对象。"""
    for opener, closer in (("[", "]"), ("{", "}")):
        i, j = text.find(opener), text.rfind(closer)
        if 0 <= i < j:
            return text[i:j + 1]
    return ""


def _clean_title(text: str) -> str:
    t = str(text or "").replace("\r", " ").replace("\n", " ").replace("\t", " ")
    t = t.strip().strip("\"'`“”‘’")
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t[:120]


def _decision_from_item(item: Any, ids: Dict[int, int]) -> Optional[Tuple[int, Dict[str, Any]]]:
    """把一项（dict / list / str）变成 (候选序号, 决策)。"""
    idx = None
    head: Optional[bool] = None
    level = 2
    title = ""

    if isinstance(item, dict):
        for key in ("i", "index", "id", "no", "idx", "line"):
            if key in item:
                try:
                    idx = int(item[key])
                    break
                except (TypeError, ValueError):
                    continue
        for key in ("head", "is_head", "isHeading", "k", "kind", "verdict"):
            if key in item:
                val = item[key]
                if isinstance(val, bool):
                    head = val
                else:
                    low = str(val).strip().lower()
                    if low in _HEAD_TRUE:
                        head = True
                    elif low in _HEAD_FALSE:
                        head = False
                break
        for key in ("lv", "level", "l"):
            if key in item:
                try:
                    level = int(item[key])
                    break
                except (TypeError, ValueError):
                    continue
        for key in ("t", "title", "text", "name", "value"):
            if key in item and isinstance(item[key], str):
                title = item[key]
                break
        if head is None and title:
            head = True  # 只给了标题文字，视为「是标题」
    elif isinstance(item, (list, tuple)) and item:
        try:
            idx = int(item[0])
        except (TypeError, ValueError):
            return None
        rest = list(item[1:])
        for val in rest:
            if isinstance(val, bool) and head is None:
                head = val
            elif isinstance(val, (int, float)) and not isinstance(val, bool) and level == 2:
                level = int(val)
            elif isinstance(val, str) and val.strip():
                low = val.strip().lower()
                if head is None and low in _HEAD_TRUE:
                    head = True
                elif head is None and low in _HEAD_FALSE:
                    head = False
                elif not title:
                    title = val
        if head is None and title:
            head = True
    else:
        return None

    if idx is None or idx not in ids:
        return None
    level = 1 if level == 1 else 2
    if head is False:
        return idx, {"head": False, "level": 2, "title": ""}
    return idx, {"head": True, "level": level, "title": _clean_title(title)}


def parse_decisions(raw: str, ids: Iterable[int]) -> Optional[Dict[int, Dict[str, Any]]]:
    """从模型回复里抠出 {候选序号: 决策}。完全解析不了返回 None（触发严格重试）。"""
    allowed = {int(i): 1 for i in ids}
    if not raw:
        return None
    text = raw.strip()
    fence = _FENCE.search(text)
    if fence:
        text = fence.group(1).strip()

    out: Dict[int, Dict[str, Any]] = {}

    def absorb(data: Any) -> None:
        if isinstance(data, dict):
            for key in ("items", "results", "decisions", "data", "list"):
                if isinstance(data.get(key), list):
                    absorb(data[key])
                    return
            # {"3": {"head": true, ...}} / {"3": "第一章"}
            for k, v in data.items():
                try:
                    idx = int(k)
                except (TypeError, ValueError):
                    continue
                if idx not in allowed:
                    continue
                if isinstance(v, str):
                    out[idx] = {"head": True, "level": 2, "title": _clean_title(v)}
                elif isinstance(v, dict):
                    got = _decision_from_item(dict(v, i=idx), allowed)
                    if got:
                        out[got[0]] = got[1]
            return
        if isinstance(data, list):
            for item in data:
                got = _decision_from_item(item, allowed)
                if got:
                    out[got[0]] = got[1]

    for cand in (text, _slice_json(text)):
        if not cand:
            continue
        try:
            absorb(json.loads(cand))
        except (ValueError, TypeError):
            continue
    if out:
        return out

    # 兜底：逐行 "i=3 head=true lv=2 t=第一章" / "3. 第一章" / "3 0"
    for line in text.splitlines():
        line = line.strip().strip(",").strip()
        if not line or line.startswith(("#", "//")):
            continue
        m = _KV_NUM.search(line)
        if not m:
            continue
        idx = int(m.group(1))
        if idx not in allowed:
            continue
        low = line.lower()
        head: Optional[bool] = None
        if re.search(r'(head|is_head|k)\s*[:=]\s*"?(true|1|是|y(?:es)?|h)\b', low):
            head = True
        elif re.search(r'(head|is_head|k)\s*[:=]\s*"?(false|0|否|no|n|b)\b', low):
            head = False
        tm = re.search(r'(?:^|\s)(?:t|title|text)\s*[:=]\s*"?(.+?)"?\s*$', line)
        title = _clean_title(tm.group(1)) if tm else ""
        if head is None:
            head = bool(title)
        lm = re.search(r"\blv\s*[:=]\s*([12])\b", low)
        level = int(lm.group(1)) if lm else 2
        out[idx] = {"head": bool(head), "level": level, "title": title}
    return out or None


# ---------------------------------------------------------------------------
# 候选行
# ---------------------------------------------------------------------------

_WEAK_NUM = re.compile(
    r"^(?:[零〇一二三四五六七八九十百千]{1,4}\s*[、.．]"
    r"|[(（]\s*[零〇一二三四五六七八九十百千\d]{1,4}\s*[)）]"
    r"|\d{1,3}\s*[、.．]\s*\S"
    r"|(?:序|楔子|引子|尾声|终章|后记|番外))"
)
_PUNCT_TAIL = ("。", "，", "！", "？", "；", "…", ".", ",", ";", "!", "：", ":")
_PAGE_ONLY = re.compile(r"^[-—\s]*\d{1,4}[-—\s]*$")


def _weak_candidate(lines: List[str], i: int, s: str) -> bool:
    """本地规则没认出来、但排版上像标题的短行。"""
    if not s or len(s) > 30:
        return False
    if s.endswith(_PUNCT_TAIL):
        return False
    if _PAGE_ONLY.match(s):
        return False
    if re.fullmatch(r"[\W_]{1,8}", s):
        return False          # 纯符号分隔线
    if re.fullmatch(r"[\d\s.、．-]{1,10}", s):
        return False          # 纯数字
    # 中文排版常见形态：标题行前面是空行，后面紧跟缩进正文（或也是空行）
    prev_blank = i == 0 or not lines[i - 1].strip()
    nxt = lines[i + 1] if i + 1 < len(lines) else ""
    next_ok = (not nxt.strip()) or nxt.startswith(("　", "    ", "\t", "  "))
    if prev_blank and next_ok:
        return True
    # 没有空行分隔时，只认明显的编号式短行
    return bool(_WEAK_NUM.match(s)) and len(s) <= 24


def build_candidates(lines: List[str], marks: Sequence[Tuple[int, int, str]],
                     *, scope: str, limit: int) -> List[Dict[str, Any]]:
    """构造候选：基线（本地已认出）+ 弱候选（可能漏判的短行）。"""
    cands: List[Dict[str, Any]] = []
    used = set()
    for line_no, level, title in marks:
        if line_no in used:
            continue
        used.add(line_no)
        cands.append({"line": line_no, "level": int(level), "title": title,
                      "source": "rule"})
    if scope == "refine" or limit <= 0:
        return cands
    extra = 0
    for i, raw in enumerate(lines):
        if extra >= limit:
            break
        if i in used:
            continue
        s = raw.strip()
        if not _weak_candidate(lines, i, s):
            continue
        used.add(i)
        extra += 1
        cands.append({"line": i, "level": 2, "title": s, "source": "weak"})
    return cands


# ---------------------------------------------------------------------------
# 提示词
# ---------------------------------------------------------------------------


def _clip(text: str, limit: int = 50) -> str:
    t = str(text or "").replace("\n", " ").strip()
    return t if len(t) <= limit else t[:limit] + "…"


def build_prompt(cands: Sequence[Dict[str, Any]], opts: LLMOptions,
                 strict: bool = False) -> str:
    instruction = (opts.instruction or "").strip() or DEFAULT_INSTRUCTION.strip()
    rule_count = sum(1 for c in cands if c.get("source") == "rule")
    weak_count = len(cands) - rule_count
    lines = [
        "## 判定标准（优先遵守）",
        instruction,
        "",
        "## 本次候选构成",
        "· 规则初判 %d 条（src=rule）：本地正则认为是标题，可能有误判，需要你确认或否决"
        % rule_count,
        "· 疑似漏判 %d 条（src=weak）：本地规则没认出来，请你判断是不是标题" % weak_count,
        "",
        "## 候选行（共 %d 条）" % len(cands),
    ]
    for pos, cand in enumerate(cands, 1):
        lines.append("%d. line=%d src=%s lv=%d text=%s"
                     % (pos, cand["line"] + 1, cand.get("source", "rule"),
                        int(cand.get("level") or 2), _clip(cand.get("title", ""))))
    lines += [
        "",
        "## 输出",
        '只输出一个 JSON 数组，不要任何解释文字。元素形如：',
        '[{"i": 1, "head": true, "lv": 2, "t": "第一章 夜雨"},'
        ' {"i": 4, "head": false}]',
        "· i = 上面候选的序号（不是行号）；head=true 是标题，false 不是标题；",
        "· lv 只在 head=true 时有意义（1=卷/部/篇，2=章/回/节）；",
        "· t 是标题文字，照抄原文，不要改写、不要加序号以外的内容；",
        "· 拿不准的行**不要出现在输出里**，不要为了凑数而猜。",
    ]
    if strict:
        lines += ["", "重申：输出必须是从 [ 开始、到 ] 结束的合法 JSON，无注释、无 Markdown 围栏、"
                      "无前后说明文字。"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 裁决流程
# ---------------------------------------------------------------------------

_BATCH_TOKEN_BUDGET = 220  # 每条候选大约消耗的 token，用于估算 max_tokens


class Splitter:
    """一次切分过程中的 LLM 裁决器。

    client 可注入（测试用假客户端，只需实现 complete(system, user, json_mode)）。
    """

    def __init__(self, opts: LLMOptions, client: Any = None):
        self.opts = (opts or LLMOptions()).normalized()
        self._client = client

    # -- 内部 ------------------------------------------------------------
    def client(self) -> Any:
        if self._client is not None:
            return self._client
        return make_client(self.opts)

    def _judge_batch(self, cands: List[Dict[str, Any]]) -> Tuple[Dict[int, Dict[str, Any]], int]:
        """一批候选问一次模型。返回 ({序号: 决策}, 重试次数)。失败抛 LLMError。"""
        client = self.client()
        if client is None:
            raise LLMError(self.opts.problem() or "LLM 客户端不可用")
        budget = min(self.opts.max_tokens, 200 + _BATCH_TOKEN_BUDGET * len(cands))
        last_err: Optional[Exception] = None
        tries = max(1, int(self.opts.max_retries))
        for attempt in range(tries):
            strict = attempt > 0
            try:
                text = client.complete(
                    SYSTEM_PROMPT,
                    build_prompt(cands, self.opts, strict=strict),
                    json_mode=strict,
                    max_tokens=budget)
            except Exception as exc:  # 网络 / 鉴权 / 超时 / 格式，一律降级
                last_err = exc
                if attempt + 1 < tries:
                    time.sleep(min(2.0, 0.4 * (attempt + 1)))
                    continue
                raise LLMError(str(exc))
            ids = list(range(1, len(cands) + 1))
            got = parse_decisions(text, ids)
            if got:
                return got, attempt
            last_err = LLMError("模型回答无法解析")
        raise LLMError(str(last_err or "模型回答无法解析"))

    # -- 对外 ------------------------------------------------------------
    def refine(self, text: str, marks: Sequence[Tuple[int, int, str]]
               ) -> Tuple[List[Tuple[int, int, str]], Dict[str, Any]]:
        """对 marks 做 LLM 复核。返回 (新 marks, info)。任何失败都回退原 marks。"""
        baseline = [(int(a), int(b), str(c)) for a, b, c in marks]
        info: Dict[str, Any] = {
            "used": False, "degraded": False, "reason": "", "model": self.opts.model,
            "candidates": 0, "confirmed": 0, "added": 0, "dropped": 0,
            "batches": 0, "retries": 0, "ms": 0, "scope": self.opts.scope,
        }
        problem = self.opts.problem()
        if problem:
            info["reason"] = problem
            return baseline, info
        started = time.time()

        lines = text.split("\n")
        scope = self.opts.scope
        if scope == "auto":
            scope = "augment" if not baseline else "refine"
        info["scope"] = scope
        cands = build_candidates(lines, baseline, scope=scope,
                                 limit=self.opts.max_candidates)
        info["candidates"] = len(cands)
        if not cands:
            info["reason"] = "没有可裁决的候选行"
            return baseline, info

        decisions: Dict[int, Dict[str, Any]] = {}
        try:
            for chunk in _make_batches(cands, self.opts.batch, self.opts.max_chars):
                got, retries = self._judge_batch(chunk)
                info["batches"] += 1
                info["retries"] += retries
                for idx, decision in got.items():
                    if 1 <= idx <= len(chunk):
                        decisions[chunk[idx - 1]["line"]] = decision
        except LLMError as exc:
            info["ms"] = int((time.time() - started) * 1000)
            if not self.opts.fallback:
                raise
            info["degraded"] = True
            info["reason"] = str(exc)
            return baseline, info

        # 合并：基线默认保留（除被否决），弱候选默认丢弃（除被确认）
        final: List[Tuple[int, int, str]] = []
        confirmed = added = dropped = 0
        for line_no, level, title in baseline:
            decision = decisions.get(line_no)
            if decision is not None and decision["head"] is False:
                dropped += 1
                continue
            if decision is not None and decision["head"]:
                confirmed += 1
                final.append((line_no, decision["level"] or level,
                              decision["title"] or title))
            else:
                final.append((line_no, level, title))
        for cand in cands:
            if cand.get("source") != "weak":
                continue
            decision = decisions.get(cand["line"])
            if decision is None or not decision["head"]:
                continue
            title = decision.get("title") or cand["title"]
            if not _clean_title(title):
                continue
            added += 1
            final.append((cand["line"], decision.get("level") or 2, _clean_title(title)))

        final.sort(key=lambda x: x[0])
        deduped: List[Tuple[int, int, str]] = []
        seen = set()
        for item in final:
            if item[0] in seen:
                continue
            seen.add(item[0])
            deduped.append(item)

        info["confirmed"] = confirmed
        info["added"] = added
        info["dropped"] = dropped
        info["ms"] = int((time.time() - started) * 1000)

        ok, why = _guard(baseline, deduped, self.opts.guard_ratio)
        if not ok:
            info["degraded"] = True
            info["reason"] = why
            return baseline, info
        info["used"] = True
        return deduped, info


def _make_batches(cands: Sequence[Dict[str, Any]], batch: int,
                  max_chars: int) -> List[List[Dict[str, Any]]]:
    """按「条数」与「字面量」双重上限切批；每批至少一条。"""
    out: List[List[Dict[str, Any]]] = []
    size = max(1, int(batch))
    cur: List[Dict[str, Any]] = []
    chars = 0
    for cand in cands:
        cost = len(str(cand.get("title", ""))) + 24
        if cur and (len(cur) >= size or chars + cost > max_chars):
            out.append(cur)
            cur, chars = [], 0
        cur.append(cand)
        chars += cost
    if cur:
        out.append(cur)
    return out


def _guard(baseline: Sequence[Any], final: Sequence[Any], ratio: float) -> Tuple[bool, str]:
    """结果护栏：空结果或数量暴涨暴跌都视为模型跑偏，整体回退。"""
    if not final:
        return False, "模型没有确认任何标题，已回退本地规则"
    if len(baseline) >= 3:
        if len(final) > len(baseline) * ratio + 2:
            return False, ("模型新增标题过多（%d → %d），已回退本地规则"
                           % (len(baseline), len(final)))
        if len(final) < len(baseline) / ratio - 1:
            return False, ("模型删掉标题过多（%d → %d），已回退本地规则"
                           % (len(baseline), len(final)))
    return True, ""


def refine_marks(text: str, marks: Sequence[Tuple[int, int, str]], llm: Any,
                 **kwargs) -> Tuple[List[Tuple[int, int, str]], Dict[str, Any]]:
    """供 txtconv.split_chapters 调用的统一入口。

    llm 可以是：LLMOptions / dict / 配置文件路径 / 自带 refine() 的裁决器。
    """
    if llm is None or llm is False:
        return list(marks), {}
    if isinstance(llm, Splitter):
        return llm.refine(text, marks)
    if hasattr(llm, "refine") and not isinstance(llm, LLMOptions):
        return llm.refine(text, marks)
    if hasattr(llm, "complete") and not isinstance(llm, LLMOptions):
        # 只接受 LLMOptions 的字段名：hasattr(LLMOptions, k) 对方法（normalized /
        # to_dict / problem …）同样成立，透传进去会 TypeError。
        allowed = {f.name for f in fields(LLMOptions)}
        opts = LLMOptions(**{k: v for k, v in kwargs.items() if k in allowed})
        opts.enabled = True
        return Splitter(opts, client=llm).refine(text, marks)
    opts = as_options(llm)
    if opts is None:
        return list(marks), {}
    return Splitter(opts).refine(text, marks)


def summarize(info: Optional[Dict[str, Any]]) -> str:
    """把 stats["llm"] 说成一句人话（CLI / GUI 都用它）。"""
    if not info:
        return "未启用"
    if info.get("degraded"):
        return "已回退本地规则（%s）" % (info.get("reason") or "原因未知")
    if not info.get("used"):
        return "未参与（%s）" % (info.get("reason") or "没有候选行")
    bits = ["模型 %s" % (info.get("model") or "?"),
            "候选 %d" % info.get("candidates", 0),
            "确认 %d" % info.get("confirmed", 0)]
    if info.get("added"):
        bits.append("补漏 %d" % info["added"])
    if info.get("dropped"):
        bits.append("剔除误判 %d" % info["dropped"])
    if info.get("ms"):
        bits.append("%.1fs" % (info["ms"] / 1000.0))
    return "、".join(bits)


__all__ = ["DEFAULT_INSTRUCTION", "LLMOptions", "LLMError", "HttpChatClient",
           "Splitter", "make_client", "test_connection", "build_candidates",
           "build_prompt", "parse_decisions", "refine_marks", "as_options",
           "config_path", "summarize", "SCOPES", "PROTOCOLS"]
