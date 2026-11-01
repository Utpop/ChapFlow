# 功能清单与 GUI 开发接口

> 面向 GUI 开发者。描述**已经能跑通**的能力、可直接调用的 API、返回值/异常契约，
> 以及把它们接到界面上的建议。不含尚未实现的功能。

- 版本：`ChapFlow 1.3.0`
- 模块：`src/chapflow.py`（引擎 + Session + CLI）、`src/txtconv.py`（TXT 解析）
- 依赖：Python 3.8+ 标准库，无第三方包
- 现状：功能层完成，**GUI 未开发**；`Session` 即为 GUI 而设计

---

## 1. 架构

```
┌──────────────┐   ┌──────────────────────────────┐
│  CLI (argparse) │──▶│           Session            │
└──────────────┘   │  book: EpubBook（只读原书）   │
                   │  toc: List[Node]（可编辑副本）│
                   │  removed / new_files /        │
                   │  new_manifest / spine_append  │
                   │  _history（撤销快照）         │
                   └───────────┬──────────────────┘
                               │ export()
                               ▼
                   ┌──────────────────────────────┐
                   │ export_epub() 重建 nav/ncx/opf│
                   │ validate_bytes() 校验         │
                   └──────────────────────────────┘
```

设计要点：**打开即快照，编辑只在内存模型上做，导出时一次性重建目录并重新打包**。
原书除 `nav.xhtml` / `toc.ncx` / `content.opf` / 新增文件外，其余资源按原字节拷贝。

---

## 2. 数据模型

### 2.1 目录节点 `Node`

```python
Node(kind="group"|"doc", title="", file=None, anchor="", href_raw="")
    .uid        # 稳定 ID（"n12"/"g3"），移动后不变，可作 Treeview iid
    .children   # List[Node]，任意层级
```

| 字段 | 含义 | 说明 |
|---|---|---|
| `kind` | `group` / `doc` | `group` = 大标题（无链接）；`doc` = 章节（指向正文），**也可带 children**（即"可点击的卷"） |
| `title` | 显示标题 | 原样保留全角空格等排版 |
| `file` | ZIP 内绝对路径 | 如 `OEBPS/Text/ch01.xhtml`；`group` 为 `None` |
| `anchor` | 锚点（不含 `#`） | 如 `sig1` |
| `href_raw` | 原始 href 字符串 | 未改动时原样写回，避免无意义的链接改写 |

`node.to_dict()` 输出 `{id, kind, title, file, anchor, children}`，可直接喂给 Treeview 构建。

### 2.2 路径寻址（1 基）

| 写法 | 含义 |
|---|---|
| `1` | 第 1 个顶级条目 |
| `1.2` | 第 1 个条目下的第 2 个子条目 |
| `3.2.1` | 三级，任意深度 |
| 移动目标 `3` | 根层级第 3 个**位置**（插到当前第 3 项之前） |
| 移动目标 `2.1` | 第 2 个条目内部的第 1 个位置 |
| 移动目标 `end` | 根层级末尾 |
| 移动目标 `end:2` | 第 2 个条目内部末尾 |

> ⚠️ **路径是位置快照，任何结构性编辑后都会变**。GUI 请以 `uid` 为准，
> 渲染树时顺手生成 `uid → path` 映射，操作时再查表取 path。

### 2.3 会话状态

| 属性 | 类型 | 用途 |
|---|---|---|
| `book` | `EpubBook` | 原书只读信息（元数据、manifest、spine、warnings） |
| `toc` | `List[Node]` | 编辑中的目录树 |
| `dirty` | `bool` | 是否有未导出改动（用于"关闭前提示"） |
| `removed` | `List[{path,item_id}]` | 待物理删除的正文文件 |
| `new_files` | `Dict[path, bytes]` | 待写入的新文件（TXT 章节、合并资源） |
| `new_manifest` | `List[{id,path,media_type,properties?}]` | 待追加的 manifest 项 |
| `spine_append` | `List[id]` | 待追加的 spine idref |
| `log` | `List[str]` | 人类可读的操作记录（可做状态栏日志） |

---

## 3. API 参考

### 3.1 打开书籍 `EpubBook(path)`

同步读全文件到内存并解析。抛出 `ValueError`（消息可直接展示）：

| 情况 | 消息 |
|---|---|
| 空文件 | `文件为空，不是有效的 EPUB：<路径>` |
| 非 ZIP / 损坏 | `不是有效的 EPUB 文件（无法按 ZIP 解包，可能已损坏或并非 EPUB）：<路径>` |
| 缺 container | `不是有效的 EPUB：缺少 META-INF/container.xml` |

常用只读字段：`version`（`"3.0"`/`"2.0"`）、`metadata{title,creators,language,identifier,publisher}`、
`toc`（原始目录副本）、`nav_path`、`ncx_path`、`opf_path`、`toc_source`（`nav`/`ncx`/`spine`）、`warnings`。

`book.info()` 返回给"书籍信息"面板用：

```python
{"file","size","version","title","creators","language","identifier",
 "opf","nav","ncx","toc_source","counts":{"groups","docs","total"},
 "spine","manifest","warnings":[{"level":"info|warn","text":...}]}
```

### 3.2 编辑操作（均返回可直接显示的中文结果字符串）

| 方法 | 参数 | 行为 | 失败 |
|---|---|---|---|
| `rename(path, title)` | 标题不可为空 | 只改目录标题；不动正文（见 `export` 的 `sync_heading`） | 空标题 / 路径越界 |
| `move(path, target)` | target 见 2.2 | 跨层级、同层移动；禁止移进自己的后代 | `不能把条目移动到它自己的内部` |
| `delete(path, mode="auto", physical=True)` | mode：`auto`/`promote`/`cascade` | `doc` 直接删；`group` 有子项时**必须**显式指定 mode | `《X》是一个大标题，含 N 个子条目，请指定 mode=promote 或 cascade` |
| `add_group(title, at="end")` | at 为插入位置 | 新建空大标题 | 位置非法 |
| `wrap_group(paths, title)` | 同层且连续 | 把选中条目包进新大标题 | `被分组的条目必须处于同一层级` / `必须是连续的一段` |
| `promote_to_top(path)` | 任意下级条目 | 挪到它所属一级条目的后面，成为一级条目；自带子级与正文链接一并带走 | `《X》已经是一级条目，不需要升级` |
| `root_of(node)` | — | 返回 node 所属的一级条目（自己就是一级时返回自己），供界面显示“所属一级标题” | — |

- `delete(physical=True)` 只是登记到 `removed`，**导出时才真正删除文件**，撤销可恢复。
- `delete(mode="promote")` 会把子项提升到父层，保留内容。

### 3.3 导入内容

**TXT → 章节**

```python
info = s.append_txt(
    "新章节.txt",
    group_title=None,      # 传书名则包一层大标题
    at="end",              # "end" / "3" / "end:2" / "2.1"
    split="auto",          # auto | heading | size | none
    size=4000,             # split=size 时每章字数
    para_mode="line",      # line | blank | indent
    encoding=None,         # 强制编码；None 自动嗅探
    link_css=True,         # 继承书内第一个样式表
    drop_page_numbers=False,
)
# → {"encoding","chapters","groups","files","where","split_rule","headings","chars"}
```

`read_docx(path, split_level=1, rules=None, size=4000)` 支持三种切法：

- `split_level>0`：按「标题 N」样式切；
- `split_level=0`：不切分，整篇一章；
- `rules=txtconv.SplitRules(...)`：按正文文本规则切（simple / regex / size / none），
  与 TXT 完全同一套规则，命中规则的普通段落被提升为章节标题。

文档**完全没有「标题」样式**时，会按正文里的章节行（第一章 / 序章 / Chapter 3…）兜底切分。
`doc.split_source` 取 `style` / `pattern` / `rules` / `none`，界面据此给出切分说明。

**直接输入正文 → 章节**（不需要 TXT / DOCX 文件）

```python
info = s.add_chapter(
    "番外 归途之后",        # 留空 → 自动命名（新章节 / 新章节 2 …）
    "第一段。\n\n第二段。", # 只有空白字符会报错，且不写任何文件
    at="end",              # 位置语法同 append_txt
    para_mode="auto",      # auto | blank | line | indent；auto 时有空行按空行，否则每行一段
    link_css=True,
)
# → {"title","path","uid","chars","paragraphs","para_mode","where"}
```

`CF.typed_paragraphs(text, para_mode)` 返回 `(段落列表, 实际模式)`，界面用它做字数/段数统计，
保证与落盘时切出的段落完全一致。新增文件命名为 `chap-001.xhtml`（重名自动跳过）。

**EPUB 合并**

```python
info = s.merge_epubs(
    ["外传.epub", "番外.epub"],
    group_each=True,       # 每本书包一层书名大标题
    prefix="merged",       # 资源落到 <OPF目录>/merged/bN/
)
# → {"books":[{"title","path","docs","groups","files","dir"}],
#    "chapters","groups","files","prefix","warnings"}
```

合并会拒绝：合并自身、含 `META-INF/encryption.xml`（DRM/字体混淆）的书、非 EPUB 文件。

**DOCX → 章节追加**

```python
info = s.append_docx(
    "新章节.docx",      # rules=… 时同样按正文文本规则切
    group_title=None,   # 包一层大标题
    at="end",           # 位置语法同 append_txt
    split_level=1,      # 每章从哪级标题开始（1 = “标题 1 / Heading 1”，多数书就是「第一章」；0 = 不切分）
    link_css=True,
    skip=[2, 5],        # 可选：跳过这些 0 基章节（界面上手动删掉的错误章节）
    rules=None,         # 可选：txtconv.SplitRules；传了就按正文文本规则切（与 TXT 同一套）
)
# → {"title","author","chapters","images","split_level","where","files"}
```

保留加粗/斜体、列表（ul/ol 嵌套）、表格、图片；图片落到 `<正文目录>/media/docx-00N-*.png`
并自动登记 manifest。撤销会一并回滚章节与图片。

**DOCX → 新 EPUB（不是追加）**

```python
from chapflow import docx_to_epub

rep = docx_to_epub("手稿.docx", "手稿.epub",
                   title=None,       # 默认取 DOCX 属性或文件名
                   author=None, language=None,
                   split_level=1)
# → {"bytes","title","author","language","chapters","images","split_level","issues"}
```

生成完整 EPUB3（`OEBPS/content.opf`、`nav.xhtml`、`toc.ncx`、`Styles/style.css`、
`Text/chapterNNN.xhtml`、`Images/`），`issues` 与 `export()` 同构。

### 3.4 导出 `export(...)`

```python
report = s.export(
    "out.epub",
    sync_spine=True,       # 按目录顺序重排 spine 阅读顺序
    sync_heading=False,    # 同时改写正文 <title> 与首个 h1-h6
    dry_run=False,         # True 则只计算不落盘
    touch_modified=True,   # 刷新 dcterms:modified（EPUB3 规范建议）
)
```

写盘方式：先写 `out.epub.tmp`，成功后 `os.replace` 原子替换，不会产生半截文件。

`report` 字段：

| 键 | 说明 |
|---|---|
| `bytes` | 产物字节数 |
| `spine_count` | 导出后 spine 项数 |
| `removed_files` | 实际删除的文件 |
| `added_files` | 新增文件（TXT/合并） |
| `created_nav` / `created_ncx` | 是否补建目录文件 |
| `reordered_spine` | 阅读顺序是否被调整 |
| `synced_headings` | 同步了多少个正文标题 |
| `warnings` | 字符串数组（非阻断） |
| `issues` | 校验结果，见下 |

### 3.5 校验结果 `issues`

```python
[{"level": "ok" | "warn" | "error", "text": "..."}]   # 顺序即展示顺序
```

包含：mimetype 位置/存储方式/extra field、manifest 完整性、spine 引用、nav 链接可达性、
NCX 节点数与 `playOrder` 连续性、NCX 链接可达性。**有 error 时 CLI 退出码为 1**，GUI 应标红并禁止"视为成功"。

也可对任意既有文件单独校验：

```python
from chapflow import validate_bytes
issues = validate_bytes(open("book.epub","rb").read())
```

### 3.6 规范级校验（EPUBCheck，可选）

```python
from chapflow import run_epubcheck, find_epubcheck, find_java

run_epubcheck("out.epub")   # → [{"level": "ok|warn|error|info", "text": "EPUBCheck ERROR(...)..."}]
```

- 自动查找 `tools/epubcheck-*/epubcheck.jar`（或环境变量 `EPUBCHECK_JAR`）与 `java`（`JAVA_HOME` → `PATH` → Windows 常见目录）。
- 找不到工具/运行时**不报错**，返回一条 `warn`，GUI 可直接展示为"未安装，已跳过"。
- 与内部自检的 `issues` 同构，可追加到同一列表展示；EPUBCheck 的 ERROR 应视为阻断。

### 3.7 撤销

```python
s.undo()   # True = 成功，False = 没有可撤销的操作
```

快照包含 `toc / removed / new_files / new_manifest / spine_append`，栈深 100。
**没有 redo**，也没有事件回调——GUI 若需"重做"，需自行保存 `export` 前的状态快照或重放操作。

---

## 4. 错误约定

- 全部抛 `ValueError` 子类；**消息为中文、面向用户、可直接放进对话框**。
- `txtconv.TxtError` 继承 `ValueError`，专用于 TXT 相关问题：

| 场景 | 消息片段 |
|---|---|
| 0 字节 | `TXT 文件为空（0 字节），无法生成章节` |
| 全空白 | `TXT 内容为空（只有空白字符）` |
| 编码识别失败 | `无法识别文本编码（已尝试：…）。请用 --encoding 指定` |
| 识别不到标题（`split=heading`） | `未识别到任何章节标题` |
| 只有标题没有正文 | `TXT 内容为空（只有标题、没有正文），已中止` |

GUI 建议：编辑类 API 用 `try/except ValueError` 包一层，`messagebox.showerror` 直接显示 `str(exc)`；
`export` 的 error 级 issue 用列表展示，不要只弹一个"失败"。

> DOCX 导入是"语义转换"：只保留标题层级、加粗斜体、列表、表格、图片，
> 脚注/批注/页眉页脚/公式/文本框/分栏/字体嵌入都会丢弃。GUI 建议在导入前用一行说明告知用户。

> 兼容性提醒：新建/移动后的**空大标题不会写进 nav/NCX**（EPUB 规范无合法写法），
> 导出时丢弃并计入 `report["warnings"]`。GUI 建议在保存前就提示"该大标题下还没有章节"。

---

## 5. GUI 交互约束（重要）

| 时机 | 必须做的事 |
|---|---|
| 打开书籍 | 用后台线程读取；期间禁用菜单；失败弹错误原文 |
| 任何编辑后 | 重新渲染树（顺序已变），刷新 `dirty`、条目计数 |
| 删除非空大标题 | 必须弹三选一：**提升子项 / 一并删除 / 取消**（对应 `promote`/`cascade`） |
| 存在空大标题时导出 | 导出会丢弃它们（规范要求分组至少含一个子项），需在 `warnings` 中提示 |
| 删除章节 | 询问"同时删除正文文件？"→ `physical` |
| `dirty=True` 时关闭 | 提示"有未导出的修改" |
| 导出前 | 展示"将同步 spine / 改写正文标题"两个选项的当前值，避免用户不知情 |
| 导出后 | 展示 `issues`：error 标红、warn 黄色；并显示 `removed_files` / `added_files` 摘要 |

禁用规则建议：

- `undo` 无历史 → 灰；
- 删除/移动/改名在无选中项时 → 灰；
- `wrap_group` 仅在"同层连续多选"时可用；
- 合并/导入过程中禁用导出。

---

## 6. 控件映射

| UI 动作 | 调用 |
|---|---|
| 双击标题改名 | 行内编辑 → `rename(path, new_title)`（空标题则还原） |
| 拖到某行**上方/下方** | 计算目标位置的 1 基序号 → `move(path, "N")` |
| 拖到某**大标题中间** | `move(path, "<组path>.<子项数+1>")`，或 `end:<组path>` |
| 工具栏 上移/下移 | 同层内 `move(path, str(index ± 1))` |
| "移动到第 N 位"输入框 | `move(path, str(N))` |
| 删除 | `delete(path, mode=..., physical=...)` |
| 新建大标题 | `add_group(title, at="end")`，随后直接进入行内改名 |
| 选中多项 → 打包成大标题 | `wrap_group(paths, title)`（先校验同层连续，否则提示） |
| 导入 TXT | 对话框收集 `split/paragraphs/encoding/group_title/at` → `append_txt` |
| 导入 DOCX | 对话框收集 `split_level/group_title/at` → `append_docx`（无需选编码） |
| DOCX 转新书 | 文件对话框 → `docx_to_epub`，完成后提示"已生成新书"而非修改当前书 |
| 合并 EPUB | 文件多选 → 主书 = 当前书 → `merge_epubs` |
| 导出 | 另存为对话框 → `export`，进度条走不确定态 |
| 状态栏 | `s.book.metadata["title"]`、条目数、`s.dirty`、`s.log[-1]` |

Treeview 建议列：`标题`（含层级缩进/图标）、`类型`（大标题/章节）、`文件`（可选，`file#anchor`）。
`iid` 直接用 `node["id"]`（uid 稳定），展开状态可按 uid 记忆。

拖拽判定（`<B1-Motion>` + `identify_row(y)`）：行高上 30% → 插到前面；下 30% → 插到后面；
中间 40% 且目标是大标题 → 放进组内。放下前先做一次本地校验（目标是否位于被拖节点的子树内，是则直接忽略），
再调 `move`——引擎也会拒绝，但 UI 层先拦掉体验更好。

---

## 7. 线程与性能

- **打开 / 导入 / 合并 / 导出都应放后台线程**（`threading.Thread` + `root.after` 回主线程刷新）。
  目前无进度回调、无取消机制（`export` 内部不可中断），界面只能用不确定态进度条。
- 耗时主要在"整本解压 + 逐个 XML 解析 + 重新压缩"，与文件体积成正比；建议先用真实书籍实测再定超时提示。
- 内存约为书籍大小的 2 倍（原 ZIP + 新 ZIP 同时在内存），合并多本时按总和估算。
- 目前只测过十余个文件的样本；**数千章的大书建议先做一次"打开→直接导出"基线测试**，
  再决定是否需要虚拟化列表（Treeview 上万行会明显卡顿）。
- 不支持 ZIP64（>4 GB 或条目数 > 65535）。

---

## 8. GUI 还需补的能力（引擎侧缺口）

| 缺口 | 影响 | 建议 |
|---|---|---|
| 无 redo | 只能撤销不能重做 | GUI 侧维护快照栈，或在 `Session` 增加 `redo()` |
| 无进度回调 / 取消 | 大书导出时界面只能转圈 | 给 `export` / `append_docx` / `docx_to_epub` 加 `progress=callable`、`cancel=Event` |
| 无事件通知 | 无法做"自动保存草稿" | 增加 `on_change` 回调或 `version` 计数器 |
| 无 `path_of(uid)` | GUI 需自己维护 uid→path | 渲染时建映射即可，暂不必改引擎 |
| 无"另存为源文件"保护 | 覆盖原文件需用户确认 | `export` 支持 `out == book.path`（已可，但 GUI 应先备份） |

---

## 9. 附：CLI 与 API 对应

| CLI | 等价 API |
|---|---|
| `info book.epub` | `EpubBook(p).info()` |
| `tree book.epub --files` | `EpubBook(p).toc` 递归打印 |
| `edit --op "rename 1.2 X"` | `Session.rename` |
| `edit --op "move 1.5 3"` | `Session.move` |
| `edit --op "delete 1.4 --keep"` | `Session.delete(physical=False)` |
| `edit --op "group-add X@3"` | `Session.add_group` |
| `edit --op "group-wrap X 3 4"` | `Session.wrap_group` |
| `edit --op "group-remove 2 promote"` | `Session.delete(mode="promote")` |
| `edit --ops ops.json` | 按 JSON 顺序调用上述方法（协议见下） |
| `append book.epub a.txt` | `Session.append_txt` |
| `merge a.epub b.epub c.epub` | `Session.merge_epubs` |
| `validate out.epub` | `validate_bytes` |

`ops.json`：

```json
[
  {"op": "rename",       "path": "1.2", "title": "新标题"},
  {"op": "move",         "path": "1.5", "to": "3"},
  {"op": "delete",       "path": "1.4", "physical": true},
  {"op": "group_add",    "title": "第二卷", "at": "end"},
  {"op": "group_wrap",   "title": "第二卷", "paths": ["3", "4"]},
  {"op": "group_remove", "path": "2", "mode": "promote"}
]
```

---

## 10. 最小可用示例

```python
import sys
sys.path.insert(0, "src")
from chapflow import Session

s = Session("book.epub")
print(s.book.metadata["title"], s.book.version)

s.rename("1.2", "第二章　迷雾（修订）")
s.move("1.5", "2.1")                 # 移进第 2 个大标题
s.add_group("第二卷 归途", "end")
s.delete("1.3", mode="auto", physical=True)

report = s.export("book.out.epub")
for it in report["issues"]:
    print(it["level"], it["text"])
```
