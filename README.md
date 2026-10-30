# ChapFlow

EPUB 目录编辑器。引擎（解析、编辑、导出、校验）为纯 Python 标准库实现，图形界面基于 PySide6。

---

## 1. 核心功能

| 功能 | 具体行为 |
|---|---|
| 目录编辑 | 改名、拖动或按序号移动、删除（仅移出目录或连正文文件一起删）、新增大标题、把选中条目打包成大标题 |
| 追加章节 | 把 TXT / DOCX 按标题切分后并入当前书，图片落到正文目录下的 `media/` 并登记 manifest |
| 格式转换 | 把 TXT / DOCX 直接转成一本新的 EPUB3（nav + NCX + 基础 CSS） |
| 合并书籍 | 多本 EPUB 合并，资源整树搬入 `merged/bN/`，路径重映射、id 去重 |
| 章节编号 | 扫描编号缺号 / 乱序 / 重复，可按目录顺序重排；可为无编号章节补号 |
| 校验 | 结构自检（ZIP / manifest / spine / nav / NCX / 元数据），可选调用 EPUBCheck |
| 版本升级 | 诊断页可把 EPUB2 另存为 EPUB3：改写版本号、补 nav 与 dcterms:modified、统一文档头 |
| 修复 | 清理目录中指向缺失文件的条目与空大标题；失效引用在导出时自动清理 |
| 阅读 | 双击章节打开只读阅读器，图片从 EPUB 包内直接读取 |
| 导出 | 只重写被改动的内容，未改动文件按原字节复制；`tmp` + `os.replace` 原子写盘 |

所有编辑操作进入撤销栈（上限 100 步），可撤销与重做。

---

## 2. 实现方式

### 2.1 整体架构

```
CLI (chapflow.py::main)          GUI (src/ui/)
        │                              │
        └──────────┬───────────────────┘
                   ▼
        Session（编辑会话：目录模型 + 撤销栈 + 新增资源）
                   │
      ┌────────────┼─────────────┬──────────────┐
      ▼            ▼             ▼              ▼
   EpubBook     Node 树      export_epub    validate_bytes
（ZIP + XML）  （目录模型）  （重建 nav/NCX/OPF） （结构自检）
                   │
                   ▼
            xmldom（保真 XML DOM）
```

界面层只做三件事：渲染 `Session` 状态、收集用户操作、调用引擎方法；不直接修改 XML。

### 2.2 保真读写

- **XML**：`xmldom` 保留注释、DOCTYPE、处理指令、原始空白与属性顺序；只改动目标节点，其余按原样序列化。
- **ZIP**：`mimetype` 必须为首条且 `STORE` 存储；未修改的文件沿用原始字节，只有被改动的条目重新压缩。
- **写盘**：先写 `out.tmp`，成功后 `os.replace` 覆盖目标，避免产生半成品文件。

### 2.3 目录模型与三处同步

目录是一棵 `Node` 树，路径用 1 基下标表示（如 `"1.2.3"`）：

```python
Node(kind="doc"|"group", title, file, anchor, children)
```

一次改动会同步到三处结构：

| 目标 | 规则 |
|---|---|
| `nav.xhtml` | `doc` → `<li><a href>`；`group` → `<li><span>标题</span><ol>…</ol>` |
| `toc.ncx` | 每个 `navPoint` 必须有 `content`；大标题指向其首个子项 |
| `spine` | 按目录顺序重排 `itemref`（`sync_spine=True`，默认开启） |

`playOrder` 分配规则：**指向同一目标的 navPoint 共用同一个序号**，其余递增。
因此目录里同一章出现两次时，序列会出现 `1,2,3,1` 这样的回跳——这是规范要求。

### 2.4 规范约束与相应处理

| 约束 | 不处理的后果 | 本项目的处理 |
|---|---|---|
| `nav` 中 `<span>` 必须跟非空 `<ol>` | `RSC-005: li incomplete` | 空大标题导出时丢弃，并写入 `report["warnings"]` |
| 同一目标的 navPoint 必须共用 `playOrder` | `different playOrder for same target` | 按目标缓存序号，序号集合保持 1..N 连续 |
| 新建的 nav 不应挂进 spine | `OPF-096: non-linear content must be reachable` | 只登记 manifest，不写 `itemref` |
| EPUB2 / EPUB3 混并需统一文档头 | `HTM-004: irregular DOCTYPE` | 跨版本合并时重写 `DOCTYPE` 与 `charset` 声明 |
| `unique-identifier` 必须指向存在的 id | `NCX-001` / 元数据不一致 | 导出时修正为第一个 `dc:identifier` 的 id |

### 2.5 TXT / DOCX 解析

- **TXT**：按 BOM 与探测顺序（UTF-8 → GB18030 → BIG5）解码并归一化换行；正则识别「第X章 / 卷 / 序章 / Chapter N」等标题；支持四种切分模式（自动 / 按标题 / 按字数 / 整段）与三种段落模式（每行 / 空行 / 缩进）。
- **DOCX**：把 `.docx` 当作 ZIP 读 `word/document.xml`，不依赖第三方库。标题层级取自 `w:pStyle`（沿 `basedOn` 链匹配 `Heading N` / `标题 N`）或 `w:outlineLvl`；列表读 `numbering.xml` 的 `numFmt` 判断 ul/ol；图片通过 `a:blip r:embed` 找到关系目标后抽出，尺寸按 `wp:extent`（EMU / 9525）换算成像素。

### 2.6 合并

被并入书籍的所有条目整体搬到 `merged/bN/` 下，因此书内相对链接无需改写即可继续工作。
跨书引用（`../` 跳出本节子树的部分）在复制时按新旧路径映射重写；manifest id 冲突时加 `bN-` 前缀。
主书的元数据、封面与语言保持不变。

### 2.7 校验与修复

`validate_bytes(data)` 对**导出结果**做结构检查，返回 `[{"level": "ok"|"warn"|"error", "text": ...}]`：

- `mimetype` 是否首条、是否 `STORE`、有无 extra field
- manifest 引用的文件是否存在、id 是否重复
- spine 的 `idref` 是否可解析、顺序是否与目录一致
- `nav.xhtml` 是否存在、`toc` 导航链接是否可达
- `toc.ncx` 节点是否都有 `content`、`playOrder` 是否连续、链接是否可达
- EPUB3 的 `dcterms:modified` 格式、`unique-identifier` 是否指向存在的 `dc:identifier`

修复分两层：

| 层级 | 内容 | 入口 |
|---|---|---|
| 模型层 | 移除悬空目录条目、移除空大标题（可撤销） | `Session.repair_plan()` / `repair()` |
| 导出层 | 清理失效 manifest 项与 spine 引用、修正唯一标识 | `export(prune_broken=True)`（默认） |

可选规范级复核：`run_epubcheck(path)` 调用本地 `tools/epubcheck-*/epubcheck.jar`（需 Java 11+），返回结构与自检一致。

### 2.8 章节编号

编号识别只匹配标题开头：`第X章 / 第X节 / 第X回 / 第X篇`（中文数字或阿拉伯数字）与 `Chapter N`。
解析结果保留「编号 + 单位 + 剩余文字」三段，重排时只替换编号部分，标题文字不动。

| 规则 | 说明 |
|---|---|
| 范围 | 整本书，或指定大标题下的所有叶子章节（按目录顺序） |
| 特殊章节 | 序章 / 前言 / 楔子 / 后记 / 附录 / 番外 等开头者不参与编号，既不占号也不加号 |
| 缺号 | 相邻两个带编号章节，后一个编号 > 前一个 + 1；中间的号即为缺号 |
| 乱序 / 重复 | 后一个编号 ≤ 前一个，分别记为乱序或重复 |
| 重排 | 范围内常规章节按目录顺序编号 1..N；编号风格默认沿用出现最多的写法，可强制指定 |
| 补号 | 仅在显式开启时执行：无编号章节按位置补成 `第N章 原标题`，分隔符沿用书中已有写法 |
| 同步范围 | 只改目录标题（nav / NCX）；正文首个标题是否同步由导出选项 `sync_heading` 决定；正文里的交叉引用（如"见第 3 章"）不做改动 |

### 2.9 版本升级（EPUB2 → EPUB3）

升级不是"转换器"，而是**以 EPUB3 为目标规格重新导出**：复用同一条 `export_epub` 管线，
把 `target_version="3.0"` 传下去即可，其余流程（目录双写、spine 同步、断言自检）完全复用。

| 改动 | 说明 |
|---|---|
| `package@version` | `2.0` → `3.0` |
| `nav.xhtml` | EPUB2 没有 nav，按当前目录重新生成并标记 `properties="nav"` |
| `dcterms:modified` | 缺失则新增，存在则刷新为当前时间（EPUB3 必需） |
| 正文文档头 | XHTML 1.1 DOCTYPE 与 `http-equiv` charset 统一改写为 HTML5 |
| 封面 | `<meta name="cover">` 指向的图片补 `properties="cover-image"` |
| `toc.ncx` | 保留，兼容只认 NCX 的旧阅读器（EPUB3 允许） |

升级结果另存为新文件，原文件与当前会话不受影响；升级后可立即在编辑器里继续修改。

### 2.10 界面层

PySide6 + Qt Widgets + QSS。样式集中在 `ui/tokens.py`（颜色 / 尺寸 / 字体）与 `ui/theme.py`（QSS 生成）；
图标为 `assets/icons/*.svg`，运行时替换颜色后用 `QSvgRenderer` 渲染。

- 目录树用 `QAbstractItemModel` 映射 `Node` 树，`QStyledItemDelegate` 自绘选中 / 悬停 / 数量徽标；拖放走 `dropMimeData`，最终调用 `Session.move`。
- 打开、合并、自检、导出在 `QThread` 中执行，不阻塞 UI。
- 阅读器用 `QTextBrowser` 渲染章节，`loadResource` 以 `epub:/<包内路径>` 直接返回图片字节，不落临时文件；`<script>` 一律剥离。
- Windows 任务栏图标依赖进程级 AppUserModelID 与 16/32/48/64/256 多尺寸图标，否则显示的是 Python 解释器图标。

---

## 3. 代码说明

### 3.1 目录结构

```
src/
├── chapflow.py        引擎与 CLI（约 3.0k 行）：ZIP/XML 读写、目录模型、导出、校验、修复、合并
├── xmldom.py          轻量 XML DOM（保真解析与序列化）
├── txtconv.py         TXT 解析：编码嗅探、标题识别、切分、段落重建
├── docxconv.py        DOCX(OOXML) 解析与 XHTML 渲染
├── chapflow_gui.py    GUI 入口
└── ui/
    ├── main_window.py 主窗口与交互
    ├── models.py      ChapterTreeModel（QAbstractItemModel）+ 过滤代理
    ├── widgets.py     卡片、统计行、封面、日志、空状态、两个委托
    ├── dialogs.py     追加章节 / 格式转换 / 合并 / 自检报告 / 关于
    ├── reader.py      只读阅读器
    ├── theme.py       浅色 QSS
    ├── tokens.py      颜色、尺寸、字体令牌
    ├── icons.py       SVG 图标加载与绘制回退
    └── fonts.py       字体解析与中文回退
tests/                 样本生成、引擎断言、界面断言、截图脚本
assets/                应用图标（多尺寸 PNG / ICO）与 SVG 图标
docs/                  功能清单、GUI 设计对照、GUI 接口契约
```

### 3.2 主要文件职责

| 文件 | 职责 |
|---|---|
| `chapflow.py` | `EpubBook`（ZIP + OPF + 目录来源）、`Node`（目录节点）、`Session`（编辑会话）、`export_epub`、`validate_bytes`、`merge_epubs` / `txt_to_epub` / `docx_to_epub` 与 CLI |
| `xmldom.py` | `X` 节点、`parse_xml`、`serialize_xml`、`get_loose`（忽略命名空间前缀取属性）等 |
| `txtconv.py` | `read_text_file`、`split_chapters`、`Chapter`（标题 / 段落 / 字数） |
| `docxconv.py` | `read_docx`、`render_blocks`、`wrap_xhtml` |
| `ui/main_window.py` | 菜单、工具栏、目录树、卡片、状态栏；把用户操作映射到 `Session` 方法 |
| `ui/dialogs.py` | 四个业务对话框与报告对话框（含一键修复入口） |

### 3.3 关键接口

```python
import chapflow as CF

book = CF.EpubBook("book.epub")        # 只读解析：metadata / manifest / spine / toc
session = CF.Session("book.epub")      # 编辑会话：toc / new_files / 撤销栈

# 目录编辑（path 为 1 基下标，如 "1.2"）
session.rename("1.1", "新标题")
session.move("1.2", "end")             # "3" | "2.1" | "end" | "end:1"
session.delete("1.3", mode="auto", physical=True)   # mode: auto | promote | cascade
session.add_group("第二卷", at="end")
session.wrap_group(["1.1", "1.2"], "第一卷")

# 内容导入
session.append_txt("a.txt", group_title="番外", at="end",
                   split="auto", para_mode="line", encoding=None)
session.append_docx("b.docx", split_level=1, at="end", link_css=True)
session.merge_epubs(["b1.epub", "b2.epub"], group_each=True)

# 导出与校验
report = session.export("out.epub", sync_spine=True, sync_heading=False,
                        touch_modified=True, prune_broken=True)
issues = report["issues"]              # 与 validate_bytes 同构

# 修复
plan = session.repair_plan()           # 只读，列出可自动修复项
session.repair()                       # 仅修模型层，可撤销

# 直接转书
CF.txt_to_epub("a.txt", "a.epub", title="书名", author="作者")
CF.docx_to_epub("b.docx", "b.epub", split_level=1)
```

`session.export()` 返回的报告字段：

```python
{"bytes", "removed_files", "added_files", "created_nav", "created_ncx",
 "reordered_spine", "synced_headings", "pruned_manifest", "pruned_spine",
 "fixed_uid", "warnings", "issues"}
```

### 3.4 命令行

```bash
python src/chapflow.py info      book.epub                 # 书籍信息与统计
python src/chapflow.py tree      book.epub                 # 打印目录树
python src/chapflow.py edit      book.epub --ops ops.json -o out.epub
python src/chapflow.py validate  book.epub [--epubcheck]
python src/chapflow.py append    book.epub a.txt --group 番外 -o out.epub
python src/chapflow.py fromtxt   a.txt -o a.epub [--split heading] [--size 4000]
python src/chapflow.py fromdocx  b.docx -o b.epub [--split-level 2]
python src/chapflow.py merge     main.epub b1.epub b2.epub -o all.epub
python src/chapflow.py repair    book.epub -o fixed.epub
python src/chapflow.py numbering book.epub [--scope 2] [--add-missing] [--apply] -o out.epub
python src/chapflow.py upgrade   old.epub -o new.epub      # EPUB2 → EPUB3
```

`edit` 的 `ops.json` 是按顺序执行的原子操作，任一失败即整批中止：

```json
[{"op": "rename",       "path": "1.1", "title": "新标题"},
 {"op": "move",         "path": "1.2", "to": "3"},
 {"op": "group_add",    "title": "第二卷", "at": "end"},
 {"op": "group_wrap",   "title": "第一卷", "paths": ["1.1", "1.2"]},
 {"op": "group_remove", "path": "3", "mode": "promote"},
 {"op": "delete",       "path": "2", "mode": "promote", "physical": false}]
```

单条操作也可用命令行简写：`--op "rename 1.2 新标题"`、`--op "delete 2 --keep"`、`--op "wrap 第一卷 1.1 1.2"`。
`info` 支持 `--json` 输出，便于脚本消费。

### 3.5 运行与测试

```bash
# 引擎仅需标准库；界面需 PySide6；EPUBCheck 需 Java 11+（可选）
py -3 -m pip install PySide6-Essentials
py -3 src/chapflow_gui.py [book.epub]      # 或双击 ChapFlow.bat
```

```bash
python tests/make_fixtures.py    # 生成 EPUB / DOCX / TXT 样本（含脏数据样本）
python tests/run_tests.py        # 引擎断言 261 项
python tests/test_gui.py         # 界面断言 66 项（离屏运行）
```

测试覆盖：目录增删改移、nav / NCX / spine 三处一致性、TXT 与 DOCX 两种导入、合并与资源隔离、
跨版本合并、校验与修复、NCX 重复目标、异常输入提示；界面侧覆盖打开、选择联动、改名、
撤销 / 重做、上下移、搜索、新建大标题、追加 / 转换 / 合并对话框、阅读器、导出落盘与空状态。

环境：Windows 10/11；引擎 Python 3.8+，界面 Python 3.9+（本机在 3.15 上验证）。
