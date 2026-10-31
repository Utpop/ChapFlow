#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LLM 辅助切分的回归测试（不联网：全部走假客户端）"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

import llmseg                 # noqa: E402
import txtconv                # noqa: E402
import chapflow as E          # noqa: E402

_passed = 0
_failed = []


def check(cond, msg):
    global _passed
    if cond:
        _passed += 1
    else:
        _failed.append(msg)
        print("  ✗ %s" % msg)


def eq(actual, expected, msg):
    check(actual == expected, "%s（期望 %r，实际 %r）" % (msg, expected, actual))


# ---------------------------------------------------------------------------
# 假客户端：按「理想模型」的规则裁决，用来验证整条 提示词→解析→合并 链路
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, verdict=None, answer=None, boom=False):
        self.verdict = verdict or {}      # 标题文字 -> True/False
        self.answer = answer              # 直接返回的固定文本（测解析用）
        self.boom = boom
        self.calls = 0
        self.prompts = []

    def complete(self, system, user, json_mode=False, max_tokens=None):
        self.calls += 1
        self.prompts.append(user)
        if self.boom:
            raise llmseg.LLMError("模拟断网")
        if self.answer is not None:
            return self.answer
        items = []
        for line in user.splitlines():
            if not line or line[0].isdigit() is False or ". line=" not in line:
                continue
            head, _, rest = line.partition(". line=")
            try:
                idx = int(head)
            except ValueError:
                continue
            text = rest.split("text=", 1)[1] if "text=" in rest else ""
            verdict = self.verdict.get(text)
            if verdict is None:
                verdict = _looks_like_heading(text)
            if verdict:
                items.append({"i": idx, "head": True, "lv": 2, "t": text})
            else:
                items.append({"i": idx, "head": False})
        return json.dumps(items, ensure_ascii=False)


def _looks_like_heading(text):
    t = (text or "").strip()
    if not t or len(t) > 30:
        return False
    if t.startswith(("序章", "楔子", "尾声", "番外")):
        return True
    if t[:1] == "第" and len(t) <= 12:
        return True
    if t[:1] in "一二三四五六七八九十" and t[1:2] in ("、", "．", "."):
        return True
    return False


def opts(**kw):
    base = dict(enabled=True, api_key="test-key", base_url="https://example.invalid",
                model="fake-model", timeout=5, max_retries=1, batch=40)
    base.update(kw)
    return llmseg.LLMOptions(**base).normalized()


def titles(chapters):
    return [c.title for c in chapters]


# ---------------------------------------------------------------------------
# [1] 配置
# ---------------------------------------------------------------------------

def test_config(tmp):
    cfg = llmseg.LLMOptions()
    check(not cfg.enabled, "默认不启用 LLM")
    check("未启用" in cfg.problem(), "未启用时 problem 说明原因")

    cfg2 = opts(api_key="")
    check("API Key" in cfg2.problem(), "缺密钥时给出可读原因")

    # 本地 Ollama 不需要密钥
    local = llmseg.LLMOptions(enabled=True, provider="ollama",
                              base_url="http://127.0.0.1:11434", model="qwen2.5:7b")
    eq(local.problem(), "", "Ollama 只要求地址与模型")

    # 越界值被拉回
    bad = llmseg.LLMOptions(enabled=True, timeout=0, max_retries=999, batch=0,
                            scope="nonsense", protocol="weird").normalized()
    check(bad.timeout >= 5, "超时被夹到下限")
    check(bad.max_retries <= 6, "重试次数被夹到上限")
    check(bad.batch >= 1, "批量被夹到下限")
    eq(bad.scope, "auto", "非法 scope 回落到 auto")
    eq(bad.protocol, "openai_chat", "非法协议回落到 openai_chat")

    # 落盘往返，且 to_dict 默认不带密钥
    path = os.path.join(tmp, "llm.json")
    cfg3 = opts(model="qwen-plus", timeout=90, scope="refine")
    eq(cfg3.save(path) != "", True, "配置可写入")
    loaded = llmseg.LLMOptions.load(path, with_env=False)
    eq(loaded.model, "qwen-plus", "模型名往返一致")
    eq(loaded.timeout, 90, "超时往返一致")
    eq(loaded.scope, "refine", "scope 往返一致")
    eq(loaded.api_key, "test-key", "密钥随文件保存（GUI 也要能读到）")
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    check("api_key" in raw or "api_key_enc" in raw, "写文件时显式带密钥")
    if os.name == "nt":
        check("api_key_enc" in raw and "api_key" not in raw,
              "Windows 下密钥以 DPAPI 密文保存（不明文落盘）")
        check(raw.get("api_key_enc") != "test-key", "落盘的是密文而非原文")

    # 兼容旧版明文配置（历史文件仍要能读）
    legacy = os.path.join(tmp, "legacy.json")
    with open(legacy, "w", encoding="utf-8") as fh:
        json.dump({"model": "m1", "api_key": "plain-key"}, fh)
    eq(llmseg.LLMOptions.load(legacy, with_env=False).api_key, "plain-key",
       "旧版明文密钥仍可读取（向后兼容）")

    check("has_key" in llmseg.LLMOptions().to_dict(), "to_dict 用 has_key 代替密钥")

    # 文件损坏 → 回落默认，不抛异常
    broken = os.path.join(tmp, "broken.json")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write("{ 不是 JSON")
    eq(llmseg.LLMOptions.load(broken, with_env=False).model,
       llmseg.DEFAULT_MODEL, "配置文件损坏时回落默认值")


# ---------------------------------------------------------------------------
# [2] 解析
# ---------------------------------------------------------------------------

def test_parse():
    ids = list(range(1, 5))
    got = llmseg.parse_decisions('[{"i":1,"head":true,"lv":2,"t":"第一章 夜雨"},'
                                 '{"i":3,"head":false}]', ids)
    eq(got[1]["head"], True, "标准 JSON 数组可解析")
    eq(got[1]["title"], "第一章 夜雨", "标题文字被取出")
    eq(got[3]["head"], False, "否决项可解析")

    fenced = "好的：\n```json\n[{\"i\": 2, \"head\": true, \"t\": \"第二卷 风起\", \"lv\": 1}]\n```"
    got = llmseg.parse_decisions(fenced, ids)
    eq(got[2]["level"], 1, "带围栏的输出被剥掉且保留层级")

    mixed = '说明文字 [{"i":2,"head":true,"t":"第二章"}] 结束语'
    got = llmseg.parse_decisions(mixed, ids)
    eq(got[2]["title"], "第二章", "夹杂解释文字时能切出 JSON")

    wrapped = '{"items": [{"i": 1, "head": true, "t": "序章"}]}'
    got = llmseg.parse_decisions(wrapped, ids)
    eq(got[1]["title"], "序章", "{\"items\": [...]} 形状可解析")

    keyed = '{"4": "第四章 归途"}'
    got = llmseg.parse_decisions(keyed, ids)
    eq(got[4]["title"], "第四章 归途", "{\"序号\": \"标题\"} 形状可解析")

    lines = "i=1 head=true lv=2 t=第一章 夜雨\ni=2 head=false"
    got = llmseg.parse_decisions(lines, ids)
    eq(got[1]["head"], True, "逐行 i=/head= 兜底可解析")
    eq(got[2]["head"], False, "逐行否决可解析")

    eq(llmseg.parse_decisions("完全不是 JSON 的回答", ids), None, "无法解析返回 None")
    # 越界序号被丢弃，不会把别的行当成标题
    got = llmseg.parse_decisions('[{"i":99,"head":true,"t":"x"}]', ids)
    check(got is None, "越界序号被忽略")
    # 标题净化
    got = llmseg.parse_decisions('[{"i":1,"head":true,"t":"  “第一章   夜雨”  "}]', ids)
    eq(got[1]["title"], "第一章 夜雨", "标题去引号并压缩空格")


# ---------------------------------------------------------------------------
# [3] 候选生成
# ---------------------------------------------------------------------------

def test_candidates():
    lines = ("夜雨\n"
             "　　他推开门，雨还在下。\n"
             "\n"
             "第3页\n"
             "\n"
             "——\n"
             "\n"
             "旧信\n"
             "　　信封已经发黄。\n").split("\n")
    cands = llmseg.build_candidates(lines, [(3, 2, "第三章")], scope="augment", limit=10)
    texts = [c["title"] for c in cands]
    check("夜雨" in texts, "空行+缩进正文的短行被当作弱候选")
    check("旧信" in texts, "第二处短标题也被捕获")
    check("——" not in texts, "纯符号分隔线不作候选")
    check(len([c for c in cands if c["source"] == "rule"]) == 1, "基线候选标记为 rule")

    refine_only = llmseg.build_candidates(lines, [(3, 2, "第三章")], scope="refine", limit=10)
    eq(len(refine_only), 1, "refine 模式不引入弱候选")

    no_marks = llmseg.build_candidates(["只是正文，没有任何标题。"], [], scope="augment",
                                       limit=10)
    eq(no_marks, [], "普通长句不产生候选")


# ---------------------------------------------------------------------------
# [4] 裁决与护栏
# ---------------------------------------------------------------------------

TXT_FALSE_POSITIVE = (
    "第一章 夜雨\n"
    "　　他推开门，雨还在下。\n"
    "第三章的内容他至今没写完\n"
    "　　桌上摊着一沓稿纸。\n"
    "第二章 旧信\n"
    "　　信封已经发黄。\n"
)

TXT_NO_HEADINGS = (
    "夜雨\n"
    "　　他推开门，雨还在下。\n"
    "\n"
    "旧信\n"
    "　　信封已经发黄。\n"
)


def test_refine_drop(tmp):
    baseline, _ = txtconv.split_chapters(TXT_FALSE_POSITIVE, "书", mode="heading")
    eq(len(baseline), 3, "本地规则把正文行误判成标题（基线 3 章）")

    client = FakeClient(verdict={"第三章的内容他至今没写完": False})
    chapters, stats = txtconv.split_chapters(
        TXT_FALSE_POSITIVE, "书", mode="heading",
        llm=llmseg.Splitter(opts(), client=client))
    eq(titles(chapters), ["第一章 夜雨", "第二章 旧信"], "LLM 剔除了误判的正文行")
    info = stats["llm"]
    eq(info["used"], True, "stats 标记 LLM 已参与")
    eq(info["dropped"], 1, "统计里记下剔除条数")
    eq(stats["rule"], "heading+llm", "切分规则标注为 heading+llm")
    check(len(client.prompts) == 1, "两页内只发一次请求")


def test_refine_add(tmp):
    local, _ = txtconv.split_chapters(TXT_NO_HEADINGS, "书")
    eq(len(local), 1, "本地规则认不出无编号短标题（整篇一章）")

    client = FakeClient(verdict={"夜雨": True, "旧信": True})
    chapters, stats = txtconv.split_chapters(
        TXT_NO_HEADINGS, "书", llm=llmseg.Splitter(opts(), client=client))
    eq(titles(chapters), ["夜雨", "旧信"], "LLM 补出漏判的短标题")
    eq(stats["llm"]["added"], 2, "统计里记下补漏条数")
    check(all(c.char_count > 0 for c in chapters), "补漏后每章都有正文")


def test_guard(tmp):
    # 模型把所有标题都否掉 → 护栏拦下，回退基线
    client = FakeClient(verdict={t: False for t in
                                 ("第一章 夜雨", "第三章的内容他至今没写完", "第二章 旧信")})
    chapters, stats = txtconv.split_chapters(
        TXT_FALSE_POSITIVE, "书", mode="heading",
        llm=llmseg.Splitter(opts(), client=client))
    eq(len(chapters), 3, "全部否决时回退本地结果")
    eq(stats["llm"]["degraded"], True, "回退被标记为 degraded")
    check("没有确认任何标题" in stats["llm"]["reason"], "回退原因写清楚")


def test_fallback_network(tmp):
    client = FakeClient(boom=True)
    chapters, stats = txtconv.split_chapters(
        TXT_FALSE_POSITIVE, "书", mode="heading",
        llm=llmseg.Splitter(opts(), client=client))
    eq(len(chapters), 3, "断网时切分结果与本地一致")
    eq(stats["llm"]["degraded"], True, "断网被标记为 degraded")
    check("模拟断网" in stats["llm"]["reason"], "错误原因被保留")

    # fallback=False → 升级成可读的 TxtError
    try:
        txtconv.split_chapters(TXT_FALSE_POSITIVE, "书", mode="heading",
                               llm=llmseg.Splitter(opts(fallback=False), client=FakeClient(boom=True)))
    except txtconv.TxtError as exc:
        check("LLM" in str(exc), "关闭兜底时抛出可读错误")
    else:
        check(False, "关闭兜底时应抛 TxtError")


def test_parse_fail_fallback():
    client = FakeClient(answer="我不知道", verdict=None)
    chapters, stats = txtconv.split_chapters(
        TXT_FALSE_POSITIVE, "书", mode="heading",
        llm=llmseg.Splitter(opts(max_retries=2), client=client))
    eq(len(chapters), 3, "回答不可解析时回退本地结果")
    eq(stats["llm"]["degraded"], True, "解析失败被标记为 degraded")
    eq(client.calls, 2, "不可解析会重试到上限（2 次）")


def test_no_key():
    chapters, stats = txtconv.split_chapters(
        TXT_FALSE_POSITIVE, "书", mode="heading",
        llm=llmseg.LLMOptions(enabled=True, api_key=""))
    eq(len(chapters), 3, "没配密钥时走本地规则")
    eq(stats["llm"]["degraded"], False, "没配密钥不算失败（只是没参与）")
    eq(stats["llm"]["used"], False, "没配密钥时 used=False")
    check("API Key" in stats["llm"]["reason"], "原因里提示配密钥")


def test_batching():
    # 120 条候选、每批 40 → 3 次请求
    text = "\n".join("第%d章 标题%d\n　　正文。" % (i, i) for i in range(1, 121))
    client = FakeClient()
    chapters, stats = txtconv.split_chapters(
        text, "书", mode="heading",
        llm=llmseg.Splitter(opts(batch=40), client=client))
    eq(len(chapters), 120, "120 章全部保留")
    eq(client.calls, 3, "按 batch 切分成 3 次请求")
    eq(stats["llm"]["batches"], 3, "统计里记下批次数")


def test_text_untouched():
    """LLM 只把标题行提为章名，正文一个字都不能被改写。"""
    base, _ = txtconv.split_chapters(TXT_NO_HEADINGS, "书", mode="none")
    chapters, _ = txtconv.split_chapters(
        TXT_NO_HEADINGS, "书",
        llm=llmseg.Splitter(opts(), client=FakeClient(verdict={"夜雨": True, "旧信": True})))
    joined = [p for c in chapters for p in c.paragraphs]
    body = [p for p in base[0].paragraphs if p not in ("夜雨", "旧信")]
    eq(joined, body, "正文段落原样保留，只有标题行被提为章名")
    eq("\n".join(joined), "他推开门，雨还在下。\n信封已经发黄。", "正文没有增删改")


def test_mode_untouched():
    """size / none 模式是显式指定的切法，LLM 不介入。"""
    client = FakeClient()
    for mode in ("size", "none"):
        txtconv.split_chapters(TXT_FALSE_POSITIVE, "书", mode=mode, size=100,
                               llm=llmseg.Splitter(opts(), client=client))
    eq(client.calls, 0, "size / none 模式下不发起请求")

    # llm=None 时与不带该参数逐字一致
    a, _ = txtconv.split_chapters(TXT_FALSE_POSITIVE, "书")
    b, _ = txtconv.split_chapters(TXT_FALSE_POSITIVE, "书", llm=None)
    eq([(c.title, c.level, c.paragraphs) for c in a],
       [(c.title, c.level, c.paragraphs) for c in b], "llm=None 时行为完全不变")


# ---------------------------------------------------------------------------
# [5] 端到端：TXT → EPUB
# ---------------------------------------------------------------------------

def test_end_to_end(tmp):
    src = os.path.join(tmp, "无标题稿.txt")
    with open(src, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(TXT_NO_HEADINGS)
    out = os.path.join(tmp, "out.epub")

    rep = E.txt_to_epub(src, out, title="测试书",
                        llm=llmseg.Splitter(
                            opts(), client=FakeClient(verdict={"夜雨": True, "旧信": True})))
    eq(rep["chapters"], 2, "EPUB 里得到 2 章")
    eq(rep["split_rule"], "heading+llm", "报告标注 LLM 参与")
    eq(rep["llm"]["used"], True, "报告里有 LLM 统计")
    check(os.path.getsize(out) > 0, "EPUB 已写盘")
    eq([i["level"] for i in rep["issues"] if i["level"] == "error"], [],
       "LLM 参与的导出仍通过自检")

    # CLI 路径：未加 --llm 时行为不变
    args = E.build_parser().parse_args(["fromtxt", src, "-o", os.path.join(tmp, "b.epub")])
    eq(E._llm_options(args), None, "未加 --llm 时不构造 LLM 选项")
    args2 = E.build_parser().parse_args(["fromtxt", src, "-o", os.path.join(tmp, "c.epub"),
                                         "--llm", "--llm-model", "x", "--llm-scope", "augment"])
    cfg = E._llm_options(args2)
    check(cfg is not None and cfg.enabled, "--llm 打开开关")
    eq(cfg.model, "x", "--llm-model 覆盖模型名")
    eq(cfg.scope, "augment", "--llm-scope 生效")


def test_summarize():
    info = {"used": True, "degraded": False, "model": "m", "candidates": 10,
            "confirmed": 8, "added": 2, "dropped": 1, "ms": 1500}
    text = llmseg.summarize(info)
    check("m" in text and "补漏 2" in text and "剔除误判 1" in text, "摘要包含关键数字")
    check("已回退" in llmseg.summarize({"degraded": True, "reason": "断网"}), "回退摘要点明原因")
    eq(llmseg.summarize(None), "未启用", "没有 llm 统计时说未启用")


def test_timeout_message():
    """超时不能被误报成「连不上服务器」：urllib 会把 socket.timeout 包进 URLError。"""
    import socket
    import urllib.error
    import urllib.request as urlreq

    cfg = opts(enabled=True, provider="openai", base_url="https://example.invalid/v1",
               model="m", api_key="k", timeout=7)
    client = llmseg.HttpChatClient(cfg)
    original = llmseg.urllib.request.urlopen

    def boom(*args, **kwargs):
        raise urllib.error.URLError(socket.timeout("timed out"))

    llmseg.urllib.request.urlopen = boom
    try:
        try:
            client.complete("s", "u")
        except llmseg.LLMError as exc:
            msg = str(exc)
            check("超时" in msg, "超时报「超时」而不是「连不上服务器」（%s）" % msg)
            check("7s" in msg, "提示里带上配置的超时时长（%s）" % msg)
            check("连不上" not in msg, "不出现误导性的「连不上服务器」")
        else:
            check(False, "超时应抛 LLMError")
    finally:
        llmseg.urllib.request.urlopen = original

    # 真正的连接失败仍报「连不上服务器」
    def refuse(*args, **kwargs):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    llmseg.urllib.request.urlopen = refuse
    try:
        try:
            client.complete("s", "u")
        except llmseg.LLMError as exc:
            check("连不上服务器" in str(exc), "连接被拒仍报「连不上服务器」")
        else:
            check(False, "连接失败应抛 LLMError")
    finally:
        llmseg.urllib.request.urlopen = original
    check(urlreq is not None, "网络层测试未污染全局 urlopen")


def main():
    tmp = tempfile.mkdtemp(prefix="chapflow-llm-")
    try:
        print("[1] 配置")
        test_config(tmp)
        print("[2] 输出解析")
        test_parse()
        print("[3] 候选生成")
        test_candidates()
        print("[4] 裁决与护栏")
        test_refine_drop(tmp)
        test_refine_add(tmp)
        test_guard(tmp)
        test_fallback_network(tmp)
        test_timeout_message()
        test_parse_fail_fallback()
        test_no_key()
        test_batching()
        test_text_untouched()
        test_mode_untouched()
        print("[5] 端到端")
        test_end_to_end(tmp)
        test_summarize()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    print("通过 %d 项，失败 %d 项" % (_passed, len(_failed)))
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
