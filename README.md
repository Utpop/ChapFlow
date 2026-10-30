# ChapFlow · EPUB 目录编辑器（桌面端 · 核心功能层）

<img src="assets/chapflow-256.png" width="110" alt="ChapFlow">

一个**免安装依赖**的 EPUB 目录编辑引擎：改名、拖动/按序号移动、删除章节、增删大标题（章节分组），
并把改动同步回 `nav.xhtml`、`toc.ncx`、OPF 的 manifest / spine / guide，保证读者端目录与阅读顺序一致。
另含 **TXT / DOCX → EPUB 章节追加**、**DOCX 直接转 EPUB** 与 **多本 EPUB 合并** 能力。

当前阶段：**只做功能层（引擎 + 命令行），GUI 尚未开发**。GUI 将直接复用 `Session` 接口，
接口契约见 [`docs/GUI-开发接口.md`](docs/GUI-开发接口.md)。

```
src/chapflow.py        核心引擎 + 命令行（ZIP、EPUB、目录模型、导出、自检、合并、redo）
src/chapflow_gui.py    GUI 入口（PySide6）
src/ui/                界面层：主题令牌 / QSS / 图标 / 模型 / 组件 / 对话框 / 主窗
src/xmldom.py          轻量 XML DOM（EPUB 与 DOCX 共用，保留格式保真）
src/txtconv.py         TXT 解析：编码嗅探、章节切分、段落重建、XHTML 生成
src/docxconv.py        DOCX 解析：标题/列表/表格/图片/元数据 → XHTML
docs/GUI-开发接口.md   API 契约 + 控件映射（做界面看这份）
docs/功能清单.md       功能清单与未实现边界（查进度看这份）
docs/GUI-设计对照.md   规范 ↔ 视觉稿对照、冲突处理、运行与验证
tests/                 样本生成 + 192 项回归断言
```

纯 Python 标准库，无第三方依赖，Python 3.8+。
可选：装有 Java 11+ 时，`--epubcheck` 可追加 W3C 官方 EPUBCheck 规范校验（工具放 `tools/epubcheck-*/`）。

---

## 一、GitHub 调研结论

| 项目 | 形态 | 可复用的部分 | 不能直接用的原因 |
|---|---|---|---|
| [Sigil](https://github.com/Sigil-Ebook/Sigil)（C++/Qt，GPLv3） | 桌面 EPUB 编辑器 | 目录编辑器语义：**允许"无链接标题"作为分组**；改名只动 nav/ncx、不动正文 | 安装型大型应用，无法"便携"；GPL 传染；体积大 |
| [ebooklib](https://github.com/aerkalov/ebooklib)（Python，AGPL） | EPUB 读写库 | `epub.Section(title)` + `Link(href, title)` 正是"大标题 + 章节"的模型；nav 与 ncx 双写的字段对应关系 | 整本重写，会丢失原文件字节/排版细节；AGPL；需 pip 安装 |
| [epub.js](https://github.com/futurepress/epub.js)（BSD-2） | 浏览器渲染/解析 | `book.navigation` 同时解析 nav 与 ncx、href → 资源解析思路 | 只读渲染器，不提供编辑与回写 |
| [JSZip](https://github.com/Stuk/jszip)（MIT） | 浏览器 zip | 前端解包思路 | 需内联/联网引入，桌面端用 `zipfile` 即可 |
| [E-BMaker](https://github.com/JkVely/E-BMaker)（JavaFX） | EPUB 编辑器 | 目录树 UI 参考 | "拖拽排序"仍标注未实现；需 Java 21 |
| [You-Ebook](https://github.com/laowus/You-Ebook)（Electron） | 电子书编辑器 | 章节批量处理思路 | TODO 里写着"目录标题拉动进行位置更改"，未完成 |

**结论**：GitHub 上没有"免安装 + 只改目录 + nav/ncx 双写 + 保留原文件字节"的现成方案。
可复用的是**语义约定**（Sigil 的无链接标题、ebooklib 的 Section/Link 映射），落地需要自研内核：

1. 不重建整本 EPUB，只在原 ZIP 上**定点替换** `nav.xhtml` / `toc.ncx` / `content.opf`，其余资源原字节拷贝；
2. 自研轻量 XML 树（保留注释、DOCTYPE、空白与属性顺序），避免 ElementTree 回写时丢失格式；
3. 目录模型统一为「大标题（无链接）/ 章节（有链接，可再带子项）」，两套目录由同一模型生成，天然同步。

---

## 二、目录层级模型

与需求附图一致：大标题是一级分组，章节缩进在其下；大标题本身可以没有链接（只作分组）。

```
▣ 隐藏处和山顶的旅馆        ← 大标题（group，无链接，nav 里是 <span>，NCX 里 content 指向首个子项）
   有什么事吗？             ← 章节（doc，指向正文文件，可带锚点 #sig）
   真的说晚了……
   ...
```

- 章节也可以是"可点击的分组"：既指向正文，又带子项（nav 里 `<li><a/><ol/></li>`，NCX 里 `navPoint` 同时有 `content` 和子 `navPoint`）。
- 路径索引为 **1 基**：`1` 第 1 个顶级条目，`1.2` 第 1 个大标题下的第 2 项，最多支持任意层。

---

## 三、同步规则（改一处，联动哪些文件）

| 操作 | nav.xhtml | toc.ncx | OPF manifest | OPF spine | 其它 |
|---|---|---|---|---|---|
| 改章节名 | 重写 `a`/`span` 文本 | 重写 `navLabel/text` | — | — | 可选同步正文 `<title>` 与首个 `h1-h6` |
| 移动/改序号 | 重排 `<ol><li>` | 重排 `navPoint`，**playOrder 从 1 连续重排** | — | 按新目录顺序重排 `itemref`（非目录项原位不动） | — |
| 删除章节 | 删除条目 | 删除 `navPoint` | 删除 `item` | 删除 `itemref` | 删除正文文件、清理 `guide`/landmarks 中的死链 |
| 新增大标题 | 新增 `<span>` 分支 | 新增 `navPoint`（content 指向首个子项，满足 NCX DTD） | — | — | 更新 `dtb:depth` |
| 追加 TXT | 追加条目 | 追加 `navPoint` | 新增 `item` | 追加 `itemref` | 新增 XHTML 文件、刷新 `dcterms:modified` |

补充约束：

- **EPUB3** 写 nav（主目录）+ NCX（兼容旧阅读器）；nav 缺失时自动补建并加 `properties="nav"`。
- **EPUB2** 只维护 NCX，**不会**塞入 nav.xhtml（否则 OEBPS 2.0 校验会报错）。
- nav 里非 toc 的导航（`landmarks`、`page-list`）中指向已删文件的条目会被清理。
- 改名默认**不碰正文**，`--sync-heading` 才会同时改 `<title>` 和首个标题标签（含内联标签时跳过，避免破坏排版）。
- 元数据（书名/作者/出版社/identifier）不变，仅按 EPUB3 规范刷新 `dcterms:modified`（可用 `--keep-modified` 关闭）。

---

## 四、命令行用法

```bash
# 查看书籍信息 / 目录树（带文件路径）
python src/chapflow.py info book.epub
python src/chapflow.py tree book.epub --files

# 改名（1.2 = 第 1 个大标题下的第 2 项）
python src/chapflow.py edit book.epub -o out.epub --op "rename 1.2 新的标题"

# 移动：把 1.5 提到根层级第 3 位；把 3 放进第 1 个大标题的第 2 位
python src/chapflow.py edit book.epub -o out.epub --op "move 1.5 3" --op "move 3 1.2"

# 删除（默认连正文文件一起删；加 --keep 只移出目录）
python src/chapflow.py edit book.epub -o out.epub --op "delete 1.4"
python src/chapflow.py edit book.epub -o out.epub --op "delete 1.4 --keep"

# 大标题：新增 / 把连续条目打包 / 删除（promote 子项上提，cascade 连子项删）
python src/chapflow.py edit book.epub -o out.epub --op "group-add 第二卷@3"
python src/chapflow.py edit book.epub -o out.epub --op "group-wrap 第二卷 3 4 5"
python src/chapflow.py edit book.epub -o out.epub --op "group-remove 2 promote"

# 批量操作（JSON，GUI 也用这个协议）
python src/chapflow.py edit book.epub --ops ops.json -o out.epub --dry-run

# TXT 转章节并追加
python src/chapflow.py append book.epub 新内容.txt -o out.epub --group "外传"
python src/chapflow.py append book.epub 新内容.txt --at 1 --encoding gbk

# DOCX 转章节并追加（与 TXT 同一个命令，按扩展名自动识别）
python src/chapflow.py append book.epub 新章节.docx -o out.epub --group "附录"
python src/chapflow.py append book.epub 新章节.docx --split-level 2   # 按“标题 2”切章

# DOCX 直接转成一本新 EPUB
python src/chapflow.py fromdocx 手稿.docx -o 手稿.epub --title "书名" --author "作者"

# 合并多本 EPUB（主书元数据/封面为准，其余书按书名包成大标题）
python src/chapflow.py merge 主书.epub 外传.epub 番外.epub -o 合集.epub
python src/chapflow.py merge 主书.epub 第二部.epub --no-group --prefix src

# 单独校验任意 EPUB
python src/chapflow.py validate out.epub
```

### ops.json 协议

```json
[
  {"op": "rename",      "path": "1.2", "title": "新标题"},
  {"op": "move",        "path": "1.5", "to": "3"},
  {"op": "delete",      "path": "1.4", "physical": true},
  {"op": "group_add",   "title": "第二卷", "at": "end"},
  {"op": "group_wrap",  "title": "第二卷", "paths": ["3", "4"]},
  {"op": "group_remove","path": "2", "mode": "promote"}
]
```

`at` / `to` 支持：`end`（末尾）、`3`（根层级第 3 位）、`end:2`（第 2 项内部末尾）、`2.1`（第 2 项内部第 1 位）。

---

## 五、TXT / DOCX 转换细节

**编码**：按 `utf-8-sig → utf-8 → utf-16 → gb18030 → big5` 顺序试解，命中即用；全部失败会提示可用的 `--encoding` 选项。
空文件、纯空白、含 NUL 的二进制文件都会被拦下并给出明确原因。

**切分**（`--split`）：

| 模式 | 行为 |
|---|---|
| `auto`（默认） | 能识别标题就按标题切；识别不到就按长度兜底 |
| `heading` | 只按标题切，识别不到直接报错 |
| `size` | 每 `--size` 字（默认 4000）切一段，尽量在段落边界断开 |
| `none` | 整篇作为一个章节 |

标题规则：`第X章/回/节/话`（X 可为中文数字或阿拉伯数字）、`第X卷/部/篇/集`（识别为**大标题**）、
`序章/楔子/引子/前言/后记/尾声/终章/番外/附录`、`Chapter N` / `CHAPTER IV` / `Part N`、Markdown `#` 标题。
标题行长度需 ≤ 45 字且不以句末标点结尾，避免把正文里的"第一章讲了……"误判成标题。

**段落**（`--paragraphs`）：`line` 每行一段（中文 TXT 最常见）、`blank` 空行分段并把段内换行合并、
`indent` 按行首缩进分段。`--drop-page-numbers` 可丢弃纯页码行。

**输出**：每个章节生成一个 XHTML 文件，落在书内正文目录（自动沿用原书最常见的正文目录），
继承原书第一个样式表，`<title>`/`<h1>` 用章节标题，正文逐段 `<p>`；
`& < > "` 与 XML 非法控制字符全部转义/清除，避免生成破损 XML。


### DOCX（Word）

`docxconv.py` 用标准库直接解 OOXML（ZIP + `word/document.xml`），无需 python-docx。

| 能力 | 说明 |
|---|---|
| 章节切分 | 按 `Heading N` / `标题 N` / `outlineLvl` 判定层级，默认按 1 级切章（`--split-level` 可调）；无标题则整篇一章，标题取文档属性或文件名 |
| 行内格式 | 加粗 `<strong>`、斜体 `<em>`、下划线（内联样式） |
| 段落 | 保留原分段；`w:br` 转 `<br/>`，`w:tab` 转空格 |
| 列表 | 读 `numbering.xml` 的 `numFmt` 判定 ul/ol，按 `ilvl` 嵌套 |
| 表格 | `w:tbl` → `<table>`（首行作表头），合并单元格按首格降级 |
| 图片 | `a:blip r:embed` → 抽出到 `Images/` 或 `media/`，按 `wp:extent` 计算显示宽度并限制 100% |
| 元数据 | `docProps/core.xml` 的标题 / 作者 / 语言 |

两种用法：

1. **追加**：`append book.epub x.docx` —— 章节写入书内正文目录，图片落到 `media/`，全部登记
   manifest + spine + 目录，原书内容与元数据不动；
2. **转换**：`fromdocx x.docx` —— 生成 EPUB3 新书（nav + NCX + 基础 CSS + 元数据），
   可直接用 `--epubcheck` 复验。

不保留的内容（明确边界）：脚注/尾注、批注与修订痕迹、页眉页脚、目录域、公式(OMML)、
文本框/SmartArt、分栏、字体嵌入与精确版式。DOCX 只提供语义结构，排版以阅读器渲染为准。

---

## 六、EPUB 合并

`merge` 把多本书并成一本，难点不在拼接字节，而在**资源路径冲突**：两本书往往都有
`Text/ch01.xhtml`、`Styles/style.css`、`Images/cover.jpg`，直接拼会互相覆盖。

做法是**整棵资源子树重定位**：

```
主书          OEBPS/Text/ch01.xhtml           ← 一字不改
并入书 1  →   OEBPS/merged/b1/Text/ch01.xhtml  ← 文件名不变，只换根
并入书 2  →   OEBPS/merged/b2/text/chapter0001.xhtml
```

因为整棵树（OPF 目录下的全部内容）原样搬迁，书内 `../Styles/style.css`、`../Images/x.jpg`
这类相对链接**不需要改写**就仍然有效——这是保持排版与图片不丢的关键。

随后只重映射三处：

1. **manifest**：每项 href 换成新路径，id 加 `bN-` 前缀并去重；保留 `svg`/`scripted` 等
   `properties`，但不重复声明 `nav` 和 `cover-image`（封面仍用主书的）。
2. **spine**：按来源书的 spine 顺序追加新 `itemref`，再由目录顺序做最终排序。
3. **目录**：把来源书的 TOC 整棵树搬过来，默认用书名包一层大标题（`--no-group` 可平铺），
   锚点（`#sig1`）原样保留；映射不到的条目会跳过并告警。

另外处理了几种边界：

- 资源落在源 OPF 目录**之外**（少见的共享目录）→ 收进 `merged/bN/_ext/` 并自动改写引用。
- 来源书带 `META-INF/encryption.xml`（DRM/字体混淆）→ **直接拒绝并说明原因**，避免产出坏书。
- 书名/作者/语言不同 → 保留主书的，逐条提示哪本书的哪项被覆盖。
- 合并后 `nav`/`NCX`/`spine` 由同一份目录模型重建，天然一致；导出后照常自检。

限制：不合并各书的 `guide`/landmarks（避免重复的"封面""正文"入口），不合并元数据与封面。

---

## 七、错误提示

| 场景 | 提示 |
|---|---|
| TXT 为 0 字节 | `TXT 文件为空（0 字节），无法生成章节：<路径>` |
| TXT 只有空白 | `TXT 内容为空（只有空白字符），无法生成章节：<路径>` |
| 编码识别失败 | `无法识别文本编码（已尝试：…）。请用 --encoding 指定…` |
| 非文本文件 | `文件看起来不是纯文本（包含 NUL 字节）` |
| 也非 EPUB / 损坏 | `不是有效的 EPUB 文件（无法按 ZIP 解包，可能已损坏或并非 EPUB）：<路径>` |
| 缺 container.xml | `不是有效的 EPUB：缺少 META-INF/container.xml` |
| 路径越界 | `路径超出范围：9.9` |
| 删除非空大标题未指定方式 | `《…》是一个大标题，含 N 个子条目，请指定 mode=promote 或 cascade` |

导出后会自动自检并逐条打印：mimetype 位置与压缩方式、manifest 资源存在性、
spine 引用完整性、nav 链接可达性、NCX `playOrder` 连续性与链接可达性。有 error 时退出码为 1。

---

## 八、兼容性保证

- `mimetype` 永远是**第一个条目、STORE 不压缩、无 extra field**（OCF 硬性要求）。
- 除 `nav.xhtml` / `toc.ncx` / `content.opf` / 新生成章节外，所有资源按**原字节**拷贝，不改动图片、字体、CSS、原章节。
- nav 中分组用 `<span>`（EPUB3 规范允许），NCX 中分组 `content` 指向首个子项（满足 NCX DTD 的 `content` 必填）。
- 移动后 `playOrder` 从 1 连续重排，`navPoint` id 重新编号，避免阅读器排序错乱。
- EPUB2 不写 nav；EPUB3 缺失 nav 时自动补建并挂 `linear="no"` 到 spine。
- 相对路径按各自文件所在目录重新计算（`nav` 在 `EPUB/text/`、`NCX` 在 `EPUB/` 这类 KEPUB/Calibre 结构已验证）。

---

## 九、验证

```bash
python tests/make_fixtures.py     # 生成 EPUB3 / EPUB2 / Calibre 风格样本 + 各类 TXT
python tests/run_tests.py         # 192 项断言
```

**规范级复验**：`tests/fixtures/*.epub` 与八种产物（含 DOCX 追加、DOCX 转书）（改名/移动/删除/分组、TXT 追加、三书合并、EPUB2 追加、EPUB2 合并）
均通过 EPUBCheck 5.4.0：`0 fatals / 0 errors / 0 warnings`。

覆盖点：载入识别、改名双写、跨层级/同层移动与 spine 重排、物理删除与软删除、
大标题新增/打包/移除、TXT 追加（UTF-8 BOM / GBK、--group、指定位置、撤销）、
无标题按长度切分、段落模式、错误提示、元数据不变、原文件字节不变、
Calibre 多目录 + 锚点 + page-list 清理、EPUB 合并（资源隔离/路径不冲突/原书字节不变/平铺模式/合入 EPUB2/撤销）、
DOCX 解析（标题层级/加粗斜体/嵌套列表/表格/图片宽度/元数据）、DOCX 追加与直接转换、DOCX 异常提示，
导出后 XML 全部可解析。

实测输出示例：

```
TXT 编码      : utf-8-sig
切分规则      : heading（命中标题 7 处）
新增章节      : 5 个，大标题 2 个，共 113 字
已导出：out.epub (7.4 KB)

  ✓ mimetype 位置、存储方式、extra field 均合规
  ✓ manifest 13 个资源全部存在
  ✓ spine 共 10 项
  ✓ nav.xhtml 目录 9 个链接全部可达
  ✓ NCX 13 个节点，playOrder 连续
  ✓ NCX 链接全部可达
```

---

## 十、已知限制

1. 暂不支持 ZIP64（>4GB 或条目数 >65535 的 EPUB）。
2. 加密/DRM（存在 `META-INF/encryption.xml`）会给出警告，字体混淆类 EPUB 修改后部分阅读器可能校验失败。
3. TXT 标题识别依赖规则；无标题文本只能按长度切分，标题需事后手动改。
4. 合并只保留主书的元数据与封面；各书的 guide/landmarks 不合并。
5. EPUBCheck 为可选依赖（需 Java 11+），未安装时退回自研自检。
6. 空大标题无法写入目录：EPUB 规范要求分组必须至少含一个子项，导出时会被丢弃并在报告中提示。

---

## 十一、图形界面

<img src="tests/shots/main.png" width="720" alt="ChapFlow 主界面">

```bash
py -3 -m pip install PySide6-Essentials    # 仅首次（装到你要用的那个 Python）
py -3 src/chapflow_gui.py book.epub        # 或直接双击 ChapFlow.bat
```

`ChapFlow.bat` 会依次尝试 `py -3` / `python` / 常见安装路径，**只选真正装了 PySide6 的解释器**，
再用同目录的 `pythonw.exe` 无控制台启动；找不到时会在窗口里给出安装命令并暂停，不会一闪而过。
启动失败的原因会写入 `logs/gui-error.log` 并弹出提示。

界面按 `docs/GUI-设计对照.md` 实现：左侧目录树（拖动排序 / 双击重命名 / 右键菜单）、
右侧章节详情卡、底部书籍信息与操作日志、底部状态栏；四个界面状态（未打开 / 未选章节 /
搜索无结果 / 忙碌）都有明确提示。

- 所有控件均连接真实业务方法：改名走 `Session.rename`、拖动与上下移走 `Session.move`、
  删除走 `Session.delete`、导入走 `append_txt/append_docx`、合并走 `merge_epubs`、
  导出与自检走 `Session.export`。
- 打开 / 合并 / 自检 / 导出在 `QThread` 中执行，界面不冻结。
- 拖放的落点会先做“是否移入自身子树”的本地判断，再由引擎二次校验。
- 双击章节可打开**内置阅读器**（只读预览，支持上下章、字号、章内跳转；图片直接从 EPUB 读取）。
- 校验报告底部提供「一键修复」：清理指向缺失文件的目录条目与空大标题（可撤销），
  清单失效项 / 正文顺序无效引用 / 唯一标识错误在导出时自动修正。

GUI 依赖 PySide6（约 100 MB）；**引擎与命令行仍为零第三方依赖**，不装 PySide6 也能照常使用 CLI。
