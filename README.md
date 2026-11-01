# ChapFlow

**EPUB 目录编辑器** —— 打开一本书，把目录改对，再原样打包回去。

引擎（解析 / 编辑 / 导出 / 校验）为**纯 Python 标准库**实现，零第三方依赖；
图形界面基于 PySide6。当前版本 **v1.3.0**。

核心取舍：**只重写被改动的内容**。未改动的条目按原始字节复制，XML 只改动目标节点，
注释、DOCTYPE、处理指令、属性顺序与原始缩进一律保留——改完的书不会"被工具重新格式化一遍"。

---

## 1. 快速开始

```bash
# 界面需要 PySide6；引擎与 CLI 不需要任何依赖
py -3 -m pip install PySide6-Essentials

py -3 src/chapflow_gui.py [book.epub]     # Windows 也可双击 ChapFlow.bat
```

打包成单个 exe（约 40 MB，无控制台窗口）：

```bash
build_exe.bat        # 产出 dist\ChapFlow.exe，并自动跑一次校验

# 也可手动调用
py -3 -m pip install pyinstaller
set QT_QPA_PLATFORM=offscreen && dist\ChapFlow.exe --selftest   # 验证资源随包分发
```

`--selftest` 是打包自检开关：检查图标与 QSS 资源是否随包分发、界面能否起来，
退出码 0 表示通过；正常启动不会走到这里。
打包后资源路径由 `ui/paths.py` 经 `sys._MEIPASS` 解析，不再依赖源码树层级。

几条最常用的命令：

```bash
python src/chapflow.py tree     book.epub                  # 看目录
python src/chapflow.py edit     book.epub --op "rename 1.2 新标题" -o out.epub
python src/chapflow.py append   book.epub a.txt --group 番外 -o out.epub
python src/chapflow.py repair   book.epub                  # 清掉失效条目
python src/chapflow.py validate book.epub --epubcheck      # 规范级复核（需 Java）
```

---

## 2. 核心功能

| 功能 | 具体行为 |
|---|---|
| 目录编辑 | 改名、拖动或按序号移动、删除（仅移出目录 / 连正文文件一起删）、新增大标题、把选中条目打包成大标题、把二级条目一键升为一级；**多选可批量操作**——框选若干章一次拖进大标题、整块上移下移、或右键「移入大标题…」选定目标，一步撤销、相对顺序不变 |
| 追加章节 | 把 TXT / DOCX 按标题切分后并入当前书，图片落到正文目录下的 `media/` 并登记 manifest |
| 新建章节 | 在对话框里直接输入或粘贴正文，确认后作为一个新章节写入目录、manifest 与阅读顺序，不需要先有 TXT / DOCX（空内容会拦截并提示，可撤销） |
| 格式转换 | 把 TXT / DOCX 直接转成一本新的 EPUB3（nav + NCX + 基础 CSS）；DOCX 可选「按 Word 标题样式」或「按正文文本规则」（与 TXT 同一套：简易规则 / 正则 / 字数 / 整段），没有标题样式时还会按正文里的「第 X 章」兜底切章 |
| 合并书籍 | 多本 EPUB 合并：左列主书 + 合并后的目录结构树（`merged/bN/`），右列待合并书籍可排序、可移除，行内显示封面/章数/目录来源；合并选项用日常说法（分组 / 翻页顺序 / 修改时间），含 DRM 的书会被拒绝并说明原因，底部实时给出冲突检查与预计输出体积 |
| 章节编号 | 扫描缺号 / 乱序 / 重复，按目录顺序重排；可给无编号章节补号；可只规范分隔符 |
| 版本升级 | 把 EPUB2 另存为 EPUB3：改写版本号、补 nav 与 `dcterms:modified`、统一文档头 |
| 打开速度 | 目录几百上千章也不卡：大书只展开第一层并记住每本书的展开状态，正文预览推迟到界面出来之后；书籍用 mmap 打开，10 MB 的图文书打开后几乎不涨内存 |
| 校验与修复 | 结构校验 + 可选 EPUBCheck；清理悬空条目、空大标题与失效引用；**给会被阅读器「吞掉第一章」的大标题自动补一页卷标题页**（导出时也会自动补） |
| 元数据 | 书名 / 排序名 / 作者 / 语言 / 标识符 / 出版方 / 日期 / 简介 / 标签 / 丛书 / 评分，可替换封面 |
| LLM 辅助切分 | 可选：把候选标题行交给大模型复核；**正文不经过模型**，失败自动回退本地规则 |
| 阅读 | 双击章节打开阅读器：上一章 / 下一章、字号调整、内部链接跳转；**可直接在窗口里改正文**（Ctrl+S 保存，可撤销）；主界面左侧即时预览，图片直接从包内读取 |

所有编辑进入撤销栈（上限 100 步），可撤销与重做。

---

## 3. 实现方式

### 3.1 架构

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
（ZIP + XML）  （目录模型）  （重建 nav/NCX/OPF） （结构校验）
                   │
                   ▼
            xmldom（保真 XML DOM）
```

界面层只做三件事：渲染 `Session` 状态、收集用户操作、调用引擎方法；不直接修改 XML。

### 3.2 保真读写

- **XML**：`xmldom` 保留注释、DOCTYPE、处理指令、原始空白与属性顺序；只改动目标节点，其余按原样序列化。
- **ZIP**：`mimetype` 必须为首条且 `STORE` 存储；未修改的文件沿用原始字节。
- **写盘**：先写 `out.tmp`，成功后 `os.replace` 覆盖目标，避免产生半成品文件。
- **畸形输入**：标签名不合法时（如正文里的裸 `<`、数学式 `1<2`）按普通文本转义保留，不凭空造元素，也不会卡死解析器。

### 3.3 目录模型与三处同步

目录是一棵 `Node` 树，路径用 1 基下标表示（如 `"1.2.3"`）：

```python
Node(kind="doc"|"group", title, file, anchor, children)
```

一次改动同步到三处：

| 目标 | 规则 |
|---|---|
| `nav.xhtml` | `doc` → `<li><a href>`；`group` → `<li><span>标题</span><ol>…</ol>` |
| `toc.ncx` | 每个 `navPoint` 必须有 `content`；大标题指向其首个子项 |
| `spine` | 按目录顺序重排 `itemref`（`sync_spine=True`，默认开启） |

`playOrder` 分配规则：**指向同一目标的 navPoint 共用同一个序号**，其余递增。
因此同一章在目录出现两次时序列会回跳（`1,2,3,1`）——这是规范要求。

### 3.4 规范约束与相应处理

| 约束 | 不处理的后果 | 本项目的处理 |
|---|---|---|
| `nav` 中 `<span>` 必须跟非空 `<ol>` | `RSC-005: li incomplete` | 空大标题导出时丢弃，并写入 `report["warnings"]` |
| 同一目标的 navPoint 必须共用 `playOrder` | `different playOrder for same target` | 按目标缓存序号，序号集合保持 1..N 连续 |
| 新建的 nav 不应挂进 spine | `OPF-096` | 只登记 manifest，不写 `itemref` |
| EPUB2 / EPUB3 混并需统一文档头 | `HTM-004` | 跨版本合并时重写 `DOCTYPE` 与 `charset` |
| `unique-identifier` 必须指向存在的 id | `NCX-001` | 导出时修正为第一个 `dc:identifier` 的 id |
| spine / navMap / nav 不得为空 | 阅读器打不开 | 校验直接判为 error，不再把「0 项」标成 ok |

### 3.5 TXT / DOCX 解析

- **TXT**：按 BOM 与探测顺序（UTF-8 → UTF-16 → GB18030 → BIG5）解码并归一化换行；三种段落模式（每行 / 空行 / 缩进）独立于分章。
  分章规则可配置（`txtconv.SplitRules`）：简易规则（前缀 + 数字类型 + 单位 + 可选行首缩进 + 附加正则）、
  自定义正则、按字数均分、整段不切分。**界面默认规则是「中文标题通杀」正则**：行首锚定的 `第X章/回/节/话/折/幕/篇/集/部/卷`、
  `卷一`、`1. / 1、`、`序章/楔子/番外/后记/终章`、`Chapter N / Prologue` 一次全收；想收紧可切回简易规则
  （前缀 + 数字类型 + 单位 + 可选行首缩进 + 附加正则，默认叠加内置写法，可关闭）。
- **DOCX**：把 `.docx` 当作 ZIP 读 `word/document.xml`，不依赖第三方库。标题层级取自 `w:pStyle`
  （沿 `basedOn` 链匹配 `Heading N` / `标题 N`）或 `w:outlineLvl`；列表读 `numbering.xml` 的 `numFmt`
  判断 ul/ol；图片通过 `a:blip r:embed` 找到关系目标后抽出，尺寸按 `wp:extent`（EMU / 9525）换算成像素。

### 3.6 LLM 辅助切分（可选）

本地规则的两种失误无法靠正则解决：**误判**（正文里「第三章的内容他至今没写完」被当成标题）与
**漏判**（只靠排版区分的短标题「一、初见」）。这部分交给大模型，但**不让它读全文**：

1. 本地规则先跑一遍，得到基线标题集合 `marks`；
2. `llmseg.build_candidates` 生成候选：基线全部 + 弱候选（空行包围、后接缩进正文的短行，或中文序号起头的短行）；
   `scope=auto` 时只在本地一个标题都没认出时才找弱候选；
3. 按 `batch`（默认 40 条）与字面上限分批请求，模型只回答 `[{"i":3,"head":true,"lv":2,"t":"第一章 夜雨"}]`；
4. `parse_decisions` 容错解析（去 ``` 围栏 / 切首个 JSON / 兼容 `{序号:标题}` / 逐行兜底）；
5. 合并回 `marks`，然后**原样交回 `txtconv`** 做切段与段落归并 —— 正文一个字都不经过模型；
6. 护栏：空结果、标题数偏离基线超过 `guard_ratio`（默认 3 倍）→ 整体回退；
   任何异常（无密钥 / 超时 / 5xx / 不可解析）→ 回退本地结果，并在 `stats["llm"]` 写明原因。

接口向后兼容：`split_chapters(..., llm=None)`，`llm=None` 时行为与本功能不存在时完全一致。

```python
import llmseg, txtconv
cfg = llmseg.LLMOptions.load()          # 读 %APPDATA%\ChapFlow\llm.json
cfg.enabled = True
chapters, stats = txtconv.split_chapters(text, "书名", llm=cfg)
print(llmseg.summarize(stats.get("llm")))   # 模型 deepseek-chat、候选 128、确认 120、补漏 6
```

配置项（JSON 或环境变量，环境变量前缀 `CHAPFLOW_LLM_`，兼容 `OPENAI_*` / `DEEPSEEK_*`）：

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关；关掉时完全不联网 |
| `provider` / `protocol` | `openai` / `openai_chat` | 本地 Ollama 填 `ollama`；也支持 `openai_responses` / `anthropic_messages` |
| `base_url` | `https://api.deepseek.com` | 服务地址（自动补 `/v1/chat/completions`） |
| `model` | `deepseek-chat` | 模型名 |
| `api_key` | 空 | 也可用 `CHAPFLOW_LLM_API_KEY` / `OPENAI_API_KEY` / `DEEPSEEK_API_KEY` |
| `timeout` / `max_retries` | `60` / `2` | 单次请求超时；每批最多尝试次数（第 2 次起切严格 JSON 模式） |
| `batch` / `max_candidates` | `40` / `300` | 每批候选条数；弱候选上限 |
| `scope` | `auto` | `auto` / `refine`（只复核）/ `augment`（同时找漏判） |
| `fallback` | `true` | 失败回退本地规则；关掉则直接报错 |
| `guard_ratio` | `3.0` | 标题数偏离基线的容忍倍数 |

### 3.7 合并

被并入书籍的所有条目整体搬到 `merged/bN/` 下，因此书内相对链接无需改写即可继续工作。
跨书引用（`../` 跳出本节子树的部分）在复制时按新旧路径映射重写；manifest id 冲突时加 `bN-` 前缀。
主书的元数据、封面与语言保持不变。

### 3.8 校验与修复

`validate_bytes(data)` 对**导出结果**做结构检查，返回 `[{"level": "ok"|"warn"|"error", "text": ...}]`：

- `mimetype` 是否首条、是否 `STORE`、有无 extra field
- manifest 引用的文件是否存在、id 是否重复
- spine 的 `idref` 是否可解析、**是否为空**
- `nav.xhtml` 是否存在、`toc` 导航链接是否可达、**是否至少有一条**
- `toc.ncx` 节点是否都有 `content`、`playOrder` 是否连续、链接是否可达、**navMap 是否为空**
- EPUB3 的 `dcterms:modified` 格式、`unique-identifier` 是否指向存在的 `dc:identifier`

修复分两层：

| 层级 | 内容 | 入口 |
|---|---|---|
| 模型层 | 移除悬空目录条目、移除空大标题（可撤销） | `Session.repair_plan()` / `repair()` |
| 导出层 | 清理失效 manifest 项与 spine 引用、修正唯一标识 | `export(prune_broken=True)`（默认） |

可选规范级复核：`run_epubcheck(path)` 调用本地 `tools/epubcheck-*/epubcheck.jar`（需 Java 11+），返回结构与校验一致。

### 3.9 章节编号

编号识别只匹配标题开头：`第X章 / 第X节 / 第X回 / 第X篇`（中文或阿拉伯数字）与 `Chapter N`。
解析结果保留「编号 + 单位 + 剩余文字」三段，重排时只替换编号部分，标题文字不动。

**标题里的分隔符会被一并规范**：`第二章.俾斯麦` → `第二章 俾斯麦`（`.` `．` `·` `、` `，` `：` `-` 等
一律换成**一个空格**）。原本就用空格分开的保持原样，全角空格的书不会被改写。

| 规则 | 说明 |
|---|---|
| 范围 | 整书连续编号 / 每个大标题独立编号 / 只看某个大标题 |
| 统计对象 | 只统计**叶子章节**。「有正文 + 子章」的可点击卷一律当卷处理，不会把自己算成一章 |
| 特殊章节 | 序章 / 前言 / 楔子 / 后记 / 附录 / 番外 等开头者不参与编号，既不占号也不加号 |
| 缺号 | 相邻两个带编号章节，后一个编号 > 前一个 + 1；中间的号即为缺号 |
| 乱序 / 重复 | 后一个编号 ≤ 前一个，分别记为乱序或重复 |
| 重排 | 范围内常规章节按目录顺序编号 1..N；编号风格默认沿用出现最多的写法，可强制指定 |
| 独立编号 | 编号单元按大标题切分（部→卷→章按卷分），各卷 1..N 互不影响；散章按出现位置单独成一段 |
| 补号 | 仅在显式开启时执行：无编号章节按位置补成 `第N章 原标题`，分隔符沿用书中已有写法 |

### 3.10 版本升级（EPUB2 → EPUB3）

升级不是"转换器"，而是**以 EPUB3 为目标规格重新导出**：复用同一条 `export_epub` 管线，
把 `target_version="3.0"` 传下去即可。

| 改动 | 说明 |
|---|---|
| `package@version` | `2.0` → `3.0` |
| `nav.xhtml` | EPUB2 没有 nav，按当前目录重新生成并标记 `properties="nav"` |
| `dcterms:modified` | 缺失则新增，存在则刷新为当前时间 |
| 正文文档头 | XHTML 1.1 DOCTYPE 与 `http-equiv` charset 统一改写为 HTML5 |
| 封面 | `<meta name="cover">` 指向的图片补 `properties="cover-image"` |
| `toc.ncx` | 保留，兼容只认 NCX 的旧阅读器 |

升级结果另存为新文件，原文件与当前会话不受影响。

### 3.11 元数据编辑

`Session.metadata()` 返回当前值，`update_metadata(values, cover_bytes=...)` 写回 OPF，改动进入撤销栈。

| 字段 | 写入位置 |
|---|---|
| 书名 / 作者 / 语言 / 出版方 / 日期 / 简介 | 对应 `dc:*` 元素 |
| 排序名 / 排序作者 | EPUB3：`<meta refines="#title" property="file-as">`；EPUB2：`opf:file-as` 属性 |
| 标识符 | `unique-identifier` 指向的 `dc:identifier` |
| 标签 | 多个 `dc:subject`（顿号或逗号分隔） |
| 丛书 / 丛书编号 / 评分 | `calibre:series` / `calibre:series_index` / `calibre:rating` |
| 封面 | 同扩展名直接覆盖；换格式则新建文件、移除旧项并更新 `cover-image` 与 `meta name="cover"` |

注意：导出时 OPF 是从 `book.opf_text` 重新解析的，写入后必须同步 `book.opf_text`；
撤销快照保存的也是该文本，恢复时重新解析。

### 3.12 界面层

PySide6 + Qt Widgets + QSS。样式集中在 `ui/tokens.py`（颜色 / 尺寸 / 字体）与 `ui/theme.py`（QSS 生成）；
图标为 `assets/icons/*.svg`，运行时替换颜色后用 `QSvgRenderer` 渲染。

- 目录树用 `QAbstractItemModel` 映射 `Node` 树，`QStyledItemDelegate` 自绘选中 / 悬停 / 数量徽标；
  拖放走 `dropMimeData`，最终调用 `Session.move`。
- 打开、合并、校验、导出在 `QThread` 中执行，不阻塞 UI；线程对象用完即回收。
- 阅读器用 `QTextBrowser` 渲染章节，`loadResource` 以 `epub:/<包内路径>` 直接返回图片字节，不落临时文件；`<script>` 一律剥离。
- 阅读器里点「编辑正文」即可改正文：本章正文按段落展开成纯文本（段间空行分隔），保存时
  段数不变就逐段替换（段内加粗/斜体/图片原位保留），段数变了则纯文字章节重建 `<body>`、
  含图片或表格的章节拒绝并说明原因；改动写进会话的 `new_files`，与其它编辑共用同一条撤销栈。
- 分章规则面板会记住上次设置，并提供六条内置预设（第X章 / 第X回 / 第X卷 / 章回節集卷混用 / Chapter N / 番外特典短篇），
  也可把调好的整套规则存成自己的预设。「附加规则」与「正则表达式」有输入补全。
- 转换 / 追加对话框的「章节预览」可以多选后按 Delete 删除识别错误的章节（右键可恢复全部），
  删除只影响本次生成，原文件不动。
- 主界面左侧是「预览」列：选中章节即在原地渲染正文，标题栏显示字数并可一键切到阅读器；
  字数气泡只在这一章有正文时出现，空状态不留占位。
- 「打开」在开过书之后会退成灰色次要样式，但仍可点击再打开另一本（读盘期间才临时禁用）。
- 目录面板标题行右侧有「展开 / 收起」：一键展开全部层级或收回只看一级条目（只改展开状态，不动数据）。
- 工具栏按「打开 ｜ 生成/合并书 ｜ 加正文 ｜ 整理目录」四段排布，段间有细竖线，
  左半部分对应“从哪来 → 往里放 → 调结构”的操作顺序。
- 书籍信息（封面、元数据、结构统计）不常驻主界面，点工具栏右侧「书籍信息」弹出。
  该页面默认只读；点底部「编辑」后数据就地变为可编辑、按钮变「保存」，保存即写回会话
  （可撤销）并自动退回只读，避免“看统计”和“改元数据”在两个窗口之间来回。
- **保存到原文件**前先做一次干跑校验：有问题就一个字节都不动，只提示"已取消，原文件未改动"。

---

## 4. 代码说明

### 4.1 目录结构

```
src/
├── chapflow.py        引擎与 CLI（约 4.1k 行）：ZIP/XML 读写、目录模型、导出、校验、修复、合并
├── xmldom.py          轻量 XML DOM（保真解析与序列化，约 350 行）
├── txtconv.py         TXT 解析：编码嗅探、标题识别、切分、段落重建
├── docxconv.py        DOCX(OOXML) 解析与 XHTML 渲染
├── llmseg.py          LLM 辅助切分：配置、候选生成、裁决、护栏（纯标准库）
├── chapflow_gui.py    GUI 入口
└── ui/
    ├── main_window.py 主窗口与交互
    ├── models.py      ChapterTreeModel（QAbstractItemModel）+ 过滤代理
    ├── widgets.py     卡片、统计行、封面、日志、空状态、委托
    ├── dialogs.py     追加章节 / 新建章节 / 格式转换 / 合并 / 校验报告 / 编号 / 书籍信息 / 关于
    ├── reader.py      内置阅读器（可编辑正文）
    ├── theme.py       浅色 QSS
    ├── tokens.py      颜色、尺寸、字体令牌
    ├── icons.py       SVG 图标加载与绘制回退
    ├── splitmem.py    分章规则预设与记忆
    └── fonts.py       字体解析与中文回退
tests/                 样本生成、引擎断言、界面断言、审计断言、截图脚本
assets/                应用图标（多尺寸 PNG / ICO）与 SVG 图标
docs/                  功能清单、GUI 设计对照、GUI 接口契约、界面文案规范
```

### 4.2 主要文件职责

| 文件 | 职责 |
|---|---|
| `chapflow.py` | `EpubBook`、`Node`、`Session`、`export_epub`、`validate_bytes`、`txt_to_epub` / `docx_to_epub` 与 CLI |
| `xmldom.py` | `X` 节点、`parse_xml`、`serialize_xml`、`get_loose`（忽略命名空间前缀取属性） |
| `txtconv.py` | `read_text_file`、`split_chapters`、`SplitRules`、`Chapter` |
| `llmseg.py` | `LLMOptions`、`build_candidates`、`refine_marks`、`parse_decisions`、`test_connection` |
| `docxconv.py` | `read_docx`、`render_blocks`、`wrap_xhtml` |
| `ui/main_window.py` | 菜单、工具栏、目录树、卡片、状态栏；把用户操作映射到 `Session` 方法 |
| `ui/dialogs.py` | 各业务对话框与报告对话框（含一键修复入口） |

### 4.3 关键接口

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

# 编号与修复
plan = session.numbering_plan(scope="all", add_missing=False, per_group=False)
session.renumber(add_missing=True)
session.repair_plan()                  # 只读，列出可自动修复项
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

### 4.4 命令行

```bash
python src/chapflow.py info      book.epub [--json]
python src/chapflow.py tree      book.epub
python src/chapflow.py edit      book.epub --ops ops.json -o out.epub
python src/chapflow.py validate  book.epub [--epubcheck]
python src/chapflow.py append    book.epub a.txt --group 番外 -o out.epub
python src/chapflow.py fromtxt   a.txt -o a.epub [--split heading] [--size 4000]
python src/chapflow.py fromtxt   a.txt -o a.epub --pattern "^\\s*第[一二三四五六七八九十\\d]+章"
python src/chapflow.py fromdocx  b.docx -o b.epub [--split-level 2]
python src/chapflow.py merge     main.epub b1.epub b2.epub -o all.epub
python src/chapflow.py repair    book.epub -o fixed.epub
python src/chapflow.py numbering book.epub [--scope 2] [--add-missing] [--apply] -o out.epub
python src/chapflow.py upgrade   old.epub -o new.epub      # EPUB2 → EPUB3

# LLM 辅助切分（可选，失败自动回退本地规则）
python src/chapflow.py llm                                 # 查看当前接入配置
python src/chapflow.py llm --test                          # 真发一次请求验证连通性
python src/chapflow.py fromtxt  a.txt -o a.epub --llm --llm-model deepseek-chat
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

---

## 5. 测试

```bash
python tests/make_fixtures.py    # 生成 EPUB / DOCX / TXT 样本（含脏数据样本）
python tests/run_tests.py        # 引擎断言 445 项
python tests/test_gui.py         # 界面断言 336 项（离屏运行）
python tests/test_llmseg.py      # LLM 切分断言 86 项（全走假客户端，不联网）
python tests/test_audit.py       # 边界与异常输入断言 171 项
```

| 套件 | 覆盖重点 |
|---|---|
| `run_tests.py` | 目录增删改移、nav / NCX / spine 三处一致性、TXT 与 DOCX 导入、合并与资源隔离、跨版本合并、校验与修复、NCX 重复目标、章节编号、EPUB2→3、元数据 |
| `test_gui.py` | 打开、选择联动、改名、撤销 / 重做、上下移、搜索、新建大标题、追加 / 转换 / 合并对话框、阅读器、导出落盘与空状态 |
| `test_audit.py` | XML 解析器健壮性（含畸形输入不死循环）、路径工具、失败操作的回滚、资源释放、空目录导出、TXT 边界、编号往返、合并异常、CLI 错误码 |

`test_audit.py` 里可能导致死循环的用例用**子进程 + 超时**隔离，避免拖垮整个套件。

另外可用真实 `epubcheck-5.4.0` 复核产物：

```python
import chapflow
chapflow.run_epubcheck("out.epub")     # 需要 Java 11+
```

在 v1.0 上，改名 / 删除 / 追加 TXT / 追加 DOCX / TXT 转书 / DOCX 转书 / 合并 / EPUB2→3 / 修复
九条产线经 EPUBCheck 校验均为 **0 error 0 warn**。

---

## 6. 环境与边界

- **环境**：Windows 10/11；引擎 Python 3.8+，界面 Python 3.9+（本机在 3.15 上验证）。
- **依赖**：引擎与 CLI 零依赖；界面需 `PySide6-Essentials`；EPUBCheck 需 Java 11+（可选）。
- **已知边界**：
  - 含 DRM / 字体混淆（有 `META-INF/encryption.xml`）的书可打开编辑，但拒绝参与合并。
  - 目录全部删空后无法凭空补齐正文，此类导出会被校验判为 error（保存时直接取消，不动原文件）。
  - 阅读器基于 `QTextBrowser`，不执行脚本，复杂 CSS 布局可能与浏览器渲染有差异。
  - 正文里的交叉引用（如「见第 3 章」）不会随编号重排一起改写。

---

## 7. 版本

当前 **v1.3.0**。版本号见 `src/chapflow.py` 的 `__version__`，
也可用 `python src/chapflow.py --version` 查看。
