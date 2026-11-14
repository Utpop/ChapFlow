# agent.md — ChapFlow 项目导览（给后续 agent 的说明书）

> 面向对象：接手本项目的 AI agent / 人类维护者。
> 目标：**读这一份就能安全改代码**，不必先通读 2.3 万行源码。
> 版本基线：`src/chapflow.py` `__version__ = "1.3.1"`；本文对应 2026-11-14 的代码状态。
> 若代码与本文冲突，**以代码与测试为准**，并回来更新本文。

---

## 0. 三十秒速览

| 项 | 值 |
|---|---|
| 是什么 | EPUB **目录（TOC）编辑器**：打开一本书 → 改目录 / 加正文 / 改元数据 → 原样写回 |
| 语言/运行时 | Python 3.9+（本机 3.15.1 验证）；引擎**纯标准库**，界面需 `PySide6-Essentials` |
| 入口 | GUI：`src/chapflow_gui.py`（或双击 `ChapFlow.bat`）；CLI：`src/chapflow.py` |
| 核心不变量 | **只重写被改动的内容**：未改条目按原始字节复制，XML 只动目标节点 |
| 规模 | 引擎 8.4k 行、GUI 8.1k 行、测试 5.9k 行（4 个套件，1324 项断言） |
| 目录 | `src/`（引擎+界面）、`tests/`（4 套件+样本生成）、`docs/`（审查报告）、`assets/`、`tools/`（epubcheck） |
| 状态 | 四套件全绿；EPUBCheck 5.4.0 复核 10 条产线均 **0 error 0 warn** |

---

## 1. 五分钟建立心智模型

### 1.1 数据流

```
        ┌──────────────┐        ┌──────────────┐
        │  CLI         │        │  GUI         │
        │ chapflow.py  │        │ src/ui/*     │
        └──────┬───────┘        └──────┬───────┘
               │   都只调 Session 的方法 │
               └───────────┬───────────┘
                           ▼
                  ┌─────────────────┐
                  │    Session      │  编辑会话：目录模型 + 撤销栈 + 新增/删除登记
                  └────────┬────────┘
        ┌──────────┬───────┼────────┬───────────┐
        ▼          ▼       ▼        ▼           ▼
    EpubBook    Node 树  export_epub  validate_bytes  repair/numbering
   (ZIP+XML)  (目录模型) (重建三处)   (结构自检)      (修复/编号)
        │          │
        ▼          ▼
      mmap        xmldom（保真 XML DOM：保留注释/DOCTYPE/PI/空白/属性顺序）
```

### 1.2 三个必须记住的概念

1. **`Session` 是唯一的状态容器。** GUI 与 CLI 都不许直接改 XML；它们调 `Session` 的方法，
   由 `Session` 改 `Node` 树 / `book.pkg` / `new_files`，并把改动包进撤销栈。
2. **`Node` 树是目录的唯一真相。** 节点路径是 **1 基**下标字符串（`"1.2.3"`）。
   `kind` 只有 `"doc"`（指向正文）与 `"group"`（无链接大标题）两种，
   但**带子项的 doc 也当大标题用**（`Node.is_group_like`，兼容别的软件导出的「带链接的卷」）。
3. **一次改动要同步三处**：`nav.xhtml`、`toc.ncx`、`spine`。任何目录类改动都必须
   在 `export_epub()` 里同时照顾到这三处，否则 EPUBCheck 会报 RSC-005 / NCX-001。

### 1.3 「导出」是唯一的写盘路径

`Session.export()` → `export_epub()` 返回**完整字节流** → `validate_bytes()` 自检 →
写入 `out.tmp` → `_atomic_replace()` 覆盖目标。

- `dry_run=True` 时**不写文件**，但仍会跑完整管线（含自检），用于「校验」按钮与保存前试运行。
- **导出绝不允许改动会话状态**（这是 2026-11-14 修复的不变量，见 §6.2）。
  卷标题页在 `export_epub` 内部对 `clone_toc(toc)` 的副本补齐，不回写 `self.toc`。

---

## 2. 文件地图（按「你要改什么」索引）

### 2.1 引擎 `src/`

| 文件 | 行数 | 职责 | 什么时候动它 |
|---|---|---|---|
| `chapflow.py` | 5.5k | `EpubBook`、`Node`、`Session`、`export_epub`、`validate_bytes`、`repair`、编号、合并、CLI | 绝大多数功能改动 |
| `xmldom.py` | 356 | 保真 XML DOM：`X`、`parse_xml`、`serialize_xml`、`el/tx/detach`、`get_loose` | 解析/序列化行为、实体处理 |
| `txtconv.py` | 576 | TXT：编码嗅探、标题识别、切分、段落、XHTML 生成、`SplitRules` | TXT 导入、分章规则 |
| `docxconv.py` | 770 | DOCX(OOXML)：样式/大纲层级、列表、表格、图片、渲染 | DOCX 导入 |
| `llmseg.py` | 1.18k | LLM 辅助切分：配置、候选、裁决、护栏、HTTP 客户端 | LLM 相关（默认关闭，不影响主流程） |
| `chapflow_gui.py` | 104 | GUI 入口、`--selftest` | 启动参数、打包自检 |

`chapflow.py` 内部区段（按文件里的 `# ====` 分隔注释）：

```
1  模块头 / 常量
2  路径工具        dirname basename split_anchor resolve_href relative_path encode_href
3  TOC 模型        Node clone_toc count_nodes first_file_of needs_volume_page
                   make_volume_pages flatten_files collect_files
                   parse_path locate parse_target move_node _detach _order_key
4  EPUB 读取       EpubBook（mmap + ZipFile，_load 解析 container/OPF/nav/NCX/spine）
5  nav / ncx       parse_nav parse_ncx href_of _build_nav_ol _build_ncx_points
                   write_nav_text build_nav_text write_ncx_text build_ncx_text
6  （保留）
7  导出            export_epub _drop_empty_groups _BookFiles _atomic_replace
                   _ensure_manifest_item _reorder_spine _set_dc _set_meta ...
8  自检            validate_bytes _strict_parse_error clean_xhtml_text
8.5 正文编辑      apply_chapter_text chapter_text_of _rewrite_block_text
9  会话            Session（编辑 API、撤销、追加、合并、元数据、修复、导出）
9.5 合并辅助      guess_media_type _rewrite_external_refs
9.10 编号         cn_to_int int_to_cn parse_chapter_number scan_numbering _bucketize
10 直接转书        _pack_epub txt_to_epub docx_to_epub
11 epubcheck      find_java find_epubcheck run_epubcheck
12 CLI            build_parser cmd_* apply_ops main
```

### 2.2 界面 `src/ui/`

| 文件 | 行数 | 职责 |
|---|---|---|
| `main_window.py` | 1.84k | 主窗口：菜单/工具栏/目录树/预览/日志/状态栏；`Worker`(QRunnable) 后台任务；保存与导出 |
| `dialogs.py` | 2.85k | 全部业务对话框：追加、新建章节、格式转换、合并、校验报告、编号、书籍信息、LLM 设置、关于 |
| `models.py` | 284 | `ChapterTreeModel`(QAbstractItemModel) + `ChapterFilterProxy`（搜索过滤 + 拖放行号映射） |
| `widgets.py` | 1.26k | 卡片、统计行、封面、日志、空状态、`ChapterDelegate` 自绘 |
| `reader.py` | 545 | `EpubView`（`epub:/` 资源加载）、`ReaderWindow`（阅读+改正文）、`ChapterPreview` |
| `theme.py` / `tokens.py` | 567 / 46 | QSS 生成 / 颜色尺寸字体令牌 |
| `icons.py` / `fonts.py` / `paths.py` | 251 / 51 / 32 | SVG 图标（运行时染色）、字体回退、资源路径（兼容 PyInstaller） |
| `splitmem.py` | 302 | 分章规则预设与记忆（QSettings） |
| `jobs.py` | 102 | `run_blocking()`：把阻塞任务丢进 QThread + 局部事件循环，UI 可重绘可取消 |

### 2.3 测试 `tests/`

| 套件 | 断言数 | 覆盖 |
|---|---|---|
| `run_tests.py` | 608 | 引擎全功能：增删改移、三处同步、TXT/DOCX 导入、合并、校验修复、编号、升级、元数据、IO |
| `test_gui.py` | 417 | 界面串联（离屏）：打开/选择/改名/撤销/移动/搜索/对话框/阅读器/导出/空状态 |
| `test_audit.py` | 202 | 边界与异常：XML 健壮性、路径、回滚、资源释放、TXT 边界、CLI 退出码、图标 |
| `test_llmseg.py` | 97 | LLM 切分（全走假客户端，**不联网**） |

```bash
python tests/make_fixtures.py   # 首次：生成 fixtures/（已入库，通常不用跑）
python tests/run_tests.py       # 引擎
python tests/test_llmseg.py     # LLM
python tests/test_audit.py      # 边界
QT_QPA_PLATFORM=offscreen python tests/test_gui.py   # 界面（必须离屏）
```

**契约**：每个套件 `exit 0` 表示全通过；失败项会打印 `✗` 并以 `1` 退出。

---

## 3. 关键 API 速查

```python
import chapflow as CF

book = CF.EpubBook("book.epub")     # 只读：metadata/manifest/spine/toc/toc_source/warnings
book.info()                         # 汇总字典；book.close() 释放 mmap（可重复调用）

s = CF.Session("book.epub")         # 编辑会话
s.toc                               # List[Node]；s.tree() 转成 dict

# —— 目录编辑（全部返回一句中文说明，全部可撤销）——
s.rename("1.1", "新标题")
s.move("1.2", "3")                  # 目标语法："3" | "2.1" | "end" | "end:2"
s.move_many(["1.1", "1.2"], "end:3")# 成组移动：一步撤销、相对顺序不变
s.delete("1.3", mode="auto", physical=True)   # mode: auto|promote|cascade
s.add_group("第二卷", at="end")
s.wrap_group(["1.1", "1.2"], "第一卷")
s.promote_to_top("1.1.2")

# —— 内容导入 ——
s.append_txt("a.txt", group_title="番外", split="auto", para_mode="line")
s.append_docx("b.docx", split_level=2)        # 2 级标题保留层级
s.add_chapter("新章", "正文……")               # 直接输入正文
s.edit_chapter_text("OEBPS/Text/ch01.xhtml", "改过的正文")
s.merge_epubs(["b1.epub", "b2.epub"], group_each=True)

# —— 元数据 ——
s.metadata()                                   # 当前值快照
s.update_metadata({"creators": "张三"})         # 只提交要改的键
s.set_cover(png_bytes)

# —— 导出 / 校验 / 修复 ——
rep = s.export("out.epub", sync_spine=True, prune_broken=True)
rep = s.export(os.devnull, dry_run=True)        # 只自检，不写盘、不改会话
plan = s.repair_plan()                          # 只读，列出可修项
logs = s.repair()                               # 一次修完，整批一步撤销

# —— 编号 / 升级 ——
s.numbering_plan(scope="all", per_group=False)
s.renumber(add_missing=True)
s.upgrade_to_epub3("out3.epub")                 # EPUB2 → EPUB3（另存为）

# —— 撤销 ——
s.can_undo / s.undo() / s.redo() / s.undo_depth     # 上限 100 步
```

`Session.export()` 报告字段：
`bytes, removed_files, added_files, created_nav, created_ncx, reordered_spine,
synced_headings, pruned_manifest, pruned_spine, fixed_uid, warnings, issues,
volume_pages, version_from, version_to, upgraded, upgraded_docs, spine_count`

`validate_bytes(data)` 返回 `[{"level": "ok"|"warn"|"error", "text": ...}]`。
**error 即「不该保存」**：GUI 保存前会 dry-run，有 error 就一个字节都不写。

---

## 4. 必须遵守的工程约定

### 4.1 保真读写

- **XML**：只用 `xmldom`。它会保留注释 / DOCTYPE / 处理指令 / 原始空白 / 属性顺序。
  不要用 `xml.etree` 改写文档（它丢注释、改属性顺序）。`_strict_parse_error()` 里用
  etree 只是**判定合法性**，不参与回写。
- **ZIP**：`mimetype` 必须首条且 `STORE`、无 extra field。未改条目走
  `zf.open()` + `shutil.copyfileobj()` 流式复制，**不要**把整本读进 dict。
- **写盘**：一律 `out.tmp` → `_atomic_replace()`（内含 5 次退避重试，应对 Windows
  杀毒/索引器瞬时占用）。

### 4.2 编码

- 读：`utf-8-sig → utf-8 → gb18030` 嗅探（`EpubBook.text` / `_decode_xml_text`）。
  **禁止** `decode("utf-8", "replace")` 后回写——替换符会永久写进目录。
- 写：字节恒为 UTF-8，且必须 `_fix_xml_decl()` 把声明里的 `encoding="gb2312"` 改掉。

### 4.3 撤销与事务

- 每个用户可见操作 = **恰好一步撤销**。`push()` 前先做参数校验，失败不留撤销步。
- 多写操作（合并、追加、修复）用 `with self._atomic():` 包住；异常时自动回滚到进入前
  并弹出这次快照，撤销栈回到原深度。

### 4.4 目录模型的坑

- `Node.is_group_like` 而不是 `kind == "group"`：带子项的 doc 也算大标题。
- `_drop_empty_groups()` 只丢**真正空**的组；`kind="doc"` 且无 file **但有子章**的
  节点要**降级成 group**，绝不能连子章一起丢（§6.1）。
- `playOrder` 规则：**指向同一目标的 navPoint 共用同一序号**，序列允许回跳（`1,2,3,1`）。
- 卷标题页：`needs_volume_page()` 判定「无正文」或「与首章同址」→ `make_volume_pages()`
  在导出时补 `partNN.xhtml`。这是为了避免手机阅读器把「卷名 + 第一章」当重复项吞掉。

### 4.5 界面层纪律

- 界面只做三件事：**渲染 Session 状态、收集用户操作、调 Session 方法**。不改 XML。
- 耗时操作（打开/导出/合并/校验/LLM）必须走后台：主窗口用 `_run()`（QThreadPool），
  对话框用 `jobs.run_blocking()`。**不要**在 UI 线程里跑网络或整本读写。
- 拖放行号必须经 `ChapterFilterProxy.dropMimeData()` 映射（过滤态下代理行 ≠ 源行）。
- 阅读器资源走 `epub:/<包内路径>`，`loadResource` 直接返回字节，不落临时文件；脚本一律剥离。

---

## 5. 常见改动怎么做（配方）

### 5.1 加一个 Session 编辑方法

```python
def my_edit(self, path: str, value: str) -> str:
    node, parent, idx = locate(self.toc, path)      # 1. 先校验（失败不占撤销步）
    if not value.strip():
        raise ValueError("值不能为空")               # 2. 中文、可展示的错误
    self.push()                                     # 3. 再 push
    node.title = value.strip()                      # 4. 改模型
    self.dirty = True
    self.log.append("...")                          # 5. 记日志
    return "已……"                                   # 6. 返回一句人话
```

配套：CLI 加 `cmd_*` 子命令 + `build_parser()` 注册；GUI 加按钮并调该方法；
测试加进 `run_tests.py` 的一个 `test_*` 并在 `main()` 里调用。

### 5.2 加一条校验规则

在 `validate_bytes()` 里用 `ok/warn/fail` 三选一追加。注意：
**判 error 的项必须真的能拦住保存**（GUI 保存前 dry-run 会看 error 数），
且要能被 `repair()` 修掉，否则用户会卡在「无法保存且无法修复」。

### 5.3 加一条修复规则

1. `repair_plan()` 里加一条 `{"key","label","count"}`（只读试算）；
2. `repair()` 里落实，**必须已 `push()` 过**（`repair()` 开头统一 push，整批一步撤销）；
3. 若涉及包层，把逻辑放进 `_apply_opf_fixes()`——`repair_plan()` 在序列化克隆上
   调同一函数，保证「计划说能修」与「实际修掉」一致。

### 5.4 改分章规则 / 标题识别

- 内置规则在 `txtconv._HEADING_RULES`；用户规则在 `SplitRules` + `build_matcher()`。
- 用户自定义正则一律走 `compile_user_regex()`（拦嵌套量词）+ 匹配前截到
  `MAX_TITLE_LEN`（45 字）。**不要**直接 `re.compile(用户输入)`。
- 「特殊章节」名单只有一份：`txtconv.SPECIAL_HEADS`（`chapflow.is_special_chapter` 引用它）。

---

## 6. 已知陷阱与近期修复（务必不要改回去）

### 6.1 卷正文页失效时不能连子章一起丢（2026-11-14 修）

`repair()` 会把「卷自身正文页不存在、但有子章」的节点降级为无链接分组（`kind` 仍是
`doc`、`file` 已清空）。`_drop_empty_groups()` 必须把这种节点**改成 group 并保留**，
否则整卷连同全部子章在导出时消失——用户只看到「1 个条目」的修复提示，实际丢掉几十章。

```python
if n.kind == "doc" and not n.file and not n.children:
    counter[0] += 1; continue          # 真空壳才丢
if n.kind == "doc" and not n.file:
    n.kind = "group"                   # 有子章：降级保留
```

### 6.2 导出必须是只读操作（2026-11-14 修）

以前 `Session.export()` 先调 `fix_swallowed_groups()`，把卷页塞进 `new_files` /
`new_manifest` / `spine_append` 并置 `dirty=True`，**且不占撤销步**。
后果：点一次「校验」或「另存为」，用户凭空多出无法撤回的未保存改动。
现在卷页只在 `export_epub()` 内部对 `clone_toc()` 副本补齐。

**任何让 `export()` 修改 `self.*` 的改动都是回归。**

### 6.3 `repair()` 不能删合法远程资源（2026-11-14 修）

EPUB3 允许 `href="https://cdn…/a.css"`（`properties="remote-resources"`）。
这类 manifest 项的 `resolve_href()` 返回 `None`，**必须跳过存在性检查**，
否则 `_apply_opf_fixes()` 会把它们当「指向缺失文件」删掉。

### 6.4 覆盖保存后必须重建 ZIP 索引（2026-11-14 修）

`EpubBook.reopen()` 曾经用 `rebuild_index=False`。覆盖写盘后包内条目已变，
旧 `infos`/`order` 会让 `has()` 永远查不到新章节、下次导出按旧 `order` 打包。
现在 `reopen()` / 按需重开（`read()`）都 `rebuild_index=True`。
**目录模型在 `Session.toc` 里，不在 `EpubBook`，重建索引不会丢编辑状态。**

### 6.5 外链判断大小写不敏感（2026-11-14 修）

`_is_external_href()` 按「协议名」判定，不枚举小写字面量。`HTTP://`、`MAILTO:`、
`FILE:` 都是外链；`Chapter 1: 夜雨.xhtml` 这种带冒号的文件名仍是包内路径。

### 6.6 用户正则的灾难性回溯（2026-11-14 修）

`(a+)+$` 配 40 个 `a` 就能让进程卡死几分钟（UI 直接「未响应」）。
`compile_user_regex()` 拒绝嵌套量词，`build_matcher()` 匹配前截到 45 字。
正则来自 GUI 输入框与 QSettings，可能由第三方提供，必须当不可信输入处理。

### 6.7 LLM 配置的布尔与地址（2026-11-14 修）

- `bool("false") is True`：手改配置写 `"enabled": "false"` 曾会**打开**联网。
  统一走 `_to_bool()`。
- `base_url` 只接受 `http(s)://`。`file://` 会让 urllib 直接读本机文件，
  内容片段又经错误信息回显——等于把本地文件当「模型回复」。
- 响应体读取上限 `MAX_RESPONSE_BYTES`（2 MB）。

### 6.8 GUI 测试的退出码（2026-11-14 修）

`tests/test_gui.py` 的 `main()` 返回后若把局部 `QApplication` 交给解释器回收，
而 100+ 顶层控件仍活着，进程会以 access violation 崩掉（断言全绿、退出码非 0）。
现在 `main()` 末尾显式 `close()` + `deleteLater()` 全部顶层控件、关掉 session 的
`book`、删掉测试用 QSettings 目录，然后 `sys.exit(main())`。

> 反面教材：曾试过 `os._exit(rc)` 跳过解释器收尾，反而**必然崩溃**。不要再用。

### 6.9 其它仍需注意的点

- `EpubBook` 用 mmap：Windows 上**覆盖原文件前必须 `close()`**。`Session.export()`
  已用 `_LIVE_BOOKS` 收集同路径的活 book，替换前后自动松手/重开。
- `ChapterFilterProxy.dropMimeData()` 的行号映射不要删，否则搜索过滤时拖放会落错位置。
- `models.py` 的 `dropMimeData` 里 `except Exception: return False` 是**故意的**
  （业务层已校验），但失败没有用户反馈——若要做提示，改成信号回调，别改成抛异常。
- `_read_metadata` 里 `calibre:series_index` 只判断一次（历史重复项已清理）。
- `merge_epubs` 里非 UTF-8 正文（GBK 章节）解码失败时**跳过链接改写**，
  只会在 `warnings` 里体现为「没有可映射的目录条目」这类间接信号——这是已知局限。
- 编号重排**只替换数字**，不改单位（`第X节` 不会被改成 `第X章`）；
  `_bucketize` 按目录顺序分桶，不按「第一个含叶子的层」截断。

---

## 7. 环境与排障

```bash
# 依赖
py -3 -m pip install PySide6-Essentials      # 界面
py -3 -m pip install pyinstaller             # 打包（可选）

# 运行
py -3 src/chapflow_gui.py [book.epub]
py -3 src/chapflow.py tree book.epub
py -3 src/chapflow.py validate book.epub --epubcheck     # 需要 Java 11+

# 打包
build_exe.bat                                 # → dist\ChapFlow.exe（含 --selftest 验证）
set QT_QPA_PLATFORM=offscreen && dist\ChapFlow.exe --selftest
```

| 症状 | 排查方向 |
|---|---|
| `ChapFlow.bat` 报「找不到 Python with PySide6」 | 脚本会依次试 `py -3`、`python`、`%LOCALAPPDATA%\Python\pythoncore-*`；手动跑 `python src\chapflow_gui.py` 看真实报错 |
| GUI 测试在 CI 里退出码非 0 但断言全绿 | 见 §6.8：检查是否又有人删掉了收尾清理 |
| 导出后阅读器乱码 | XML 声明编码是否被 `_fix_xml_decl()` 改写；有没有用 `replace` 解码 |
| 保存报「目标文件正被其它程序占用」 | `_atomic_replace()` 重试 5 次仍失败；关掉占用程序（杀毒/预览） |
| EPUBCheck 报 RSC-005 | `nav` 里 `<span>` 必须跟非空 `<ol>`；空大标题应在导出时丢弃并写 `warnings` |
| EPUBCheck 报 NCX-001 | `unique-identifier` 没指向存在的 `dc:identifier`；`export_epub` 有 `_fix_unique_identifier()` |

日志位置：`logs/launcher.txt`（启动器记录的 pythonw 路径）。

---

## 8. 改代码的检查清单

动手前：

- [ ] 读 §4 的约定，确认改动落在正确层（引擎 / 界面 / 测试）
- [ ] `grep` 目标函数的所有调用点（CLI、GUI、测试三处都要看）

改完必须：

- [ ] `python tests/run_tests.py` → exit 0
- [ ] `python tests/test_audit.py` → exit 0
- [ ] `python tests/test_llmseg.py` → exit 0
- [ ] `QT_QPA_PLATFORM=offscreen python tests/test_gui.py` → exit 0（**看退出码，不只看"通过 N 项"**）
- [ ] 若改了导出/修复：用 `chapflow.run_epubcheck()` 复核产物 **0 error 0 warn**
- [ ] 新行为有对应断言（加在哪个套件见 §2.3）
- [ ] 同步更新 `README.md` / `docs/功能清单.md` 里被改变的行为描述与断言数

**判定「修好了」的标准**：不是「代码看起来对」，而是「有一条断言在旧代码上失败、
在新代码上通过」。本项目的四份审查报告（`docs/代码审查报告.md`、
`docs/功能审查报告-2026-11-03.md` 等）就是按这个标准写的，可以照抄格式。

---

## 9. 文档索引

| 文档 | 内容 |
|---|---|
| `README.md` | 面向用户的功能说明 + 架构 + 规范约束表（**最详细**） |
| `docs/功能清单.md` | 逐条功能 → 实现入口对照表 |
| `docs/代码审查报告.md` | 2026-11-01 代码质量与安全审查（P0/P1/P2 + 修复记录） |
| `docs/功能审查报告-2026-11-03.md` | 功能有效性与一致性审查（D1–D12 + C1–C6 + 修复记录） |
| `docs/GUI-开发接口.md` | 界面层与引擎的接口契约 |
| `docs/GUI-设计对照.md` | 设计稿与实现的对照 |
| `docs/文案规范.md` | 界面中文文案规范 |
| `docs/性能分析-打开书籍.md` | 大书打开性能的测量与优化记录 |
| `tools/bench_open.py` / `bench_numbering.py` | 性能基准脚本 |

> 文档里的断言数会漂移。**以实际运行输出为准**，发现不一致就顺手改掉——
> 这是维护本项目的常态工作之一。
