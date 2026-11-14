#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成测试用 EPUB / TXT 样本（不依赖第三方库）"""
import os
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures")

import struct
import zlib


def _make_png(w: int, h: int, rgb=(22, 131, 75)) -> bytes:
    """生成合法 PNG（CRC 正确），避免 libpng 报 IDAT 校验错误"""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


PNG = _make_png(64, 64)

CHAPTERS = {
    "ch01": ("第一章 初见", "他推开门的时候，风把灰尘吹了起来。<br/>这是他第一次来到这里。"),
    "ch02": ("第二章 迷雾", "雾从山谷里升起来，像是有人在下面点了一堆湿柴。"),
    "ch03": ("第三章 远行", "马车在石板上颠了一下，他抓紧了扶手。"),
    "ch04": ("第四章 归途", "回去的路比来时短，也许是因为心里有了方向。"),
}


def chapter_xhtml(title, body, epub2=False):
    doctype = ('<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
               '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">'
               if epub2 else "<!DOCTYPE html>")
    charset = ('<meta http-equiv="Content-Type" content="text/html; charset=utf-8"/>'
               if epub2 else '<meta charset="utf-8"/>')
    return """<?xml version="1.0" encoding="utf-8"?>
%s
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
  %s
  <title>%s</title>
  <link rel="stylesheet" type="text/css" href="../Styles/style.css"/>
</head>
<body>
  <h1>%s</h1>
  <p>%s</p>
  <p>这里是正文的第二段，用于验证内容未被改动。</p>
</body>
</html>
""" % (doctype, charset, title, title, body.replace("<br/>", ""))


NAV = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head>
  <title>目录</title>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>目录</h1>
    <ol>
      <li><span>第一卷 试炼</span>
        <ol>
          <li><a href="Text/ch01.xhtml">第一章 初见</a></li>
          <li><a href="Text/ch02.xhtml">第二章 迷雾</a></li>
        </ol>
      </li>
      <li><a href="Text/ch03.xhtml">第三章 远行</a></li>
      <li><a href="Text/ch04.xhtml">第四章 归途</a></li>
    </ol>
  </nav>
  <nav epub:type="landmarks" hidden="hidden">
    <ol>
      <li><a epub:type="bodymatter" href="Text/ch01.xhtml">正文</a></li>
      <li><a epub:type="backmatter" href="Text/ch04.xhtml">结尾</a></li>
    </ol>
  </nav>
</body>
</html>
"""

NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:11111111-2222-3333-4444-555555555555"/>
    <meta name="dtb:depth" content="2"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>测试之书</text></docTitle>
  <navMap>
    <navPoint id="navPoint-1" playOrder="1">
      <navLabel><text>第一卷 试炼</text></navLabel>
      <content src="Text/ch01.xhtml"/>
      <navPoint id="navPoint-2" playOrder="1">
        <navLabel><text>第一章 初见</text></navLabel>
        <content src="Text/ch01.xhtml"/>
      </navPoint>
      <navPoint id="navPoint-3" playOrder="2">
        <navLabel><text>第二章 迷雾</text></navLabel>
        <content src="Text/ch02.xhtml"/>
      </navPoint>
    </navPoint>
    <navPoint id="navPoint-4" playOrder="3">
      <navLabel><text>第三章 远行</text></navLabel>
      <content src="Text/ch03.xhtml"/>
    </navPoint>
    <navPoint id="navPoint-5" playOrder="4">
      <navLabel><text>第四章 归途</text></navLabel>
      <content src="Text/ch04.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
"""

CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

CSS = """body { font-family: serif; line-height: 1.7; }
h1 { text-align: center; font-size: 1.5em; }
p { text-indent: 2em; margin: 0.5em 0; }
"""


def opf3():
    items = "\n".join(
        '    <item id="%s" href="Text/%s.xhtml" media-type="application/xhtml+xml"/>' % (k, k)
        for k in CHAPTERS)
    spine = "\n".join('    <itemref idref="%s"/>' % k for k in CHAPTERS)
    return """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="zh-CN">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>测试之书</dc:title>
    <dc:creator>测试作者</dc:creator>
    <dc:language>zh-CN</dc:language>
    <dc:publisher>示例出版社</dc:publisher>
    <meta property="dcterms:modified">2020-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="Styles/style.css" media-type="text/css"/>
    <item id="cover" href="Images/cover.png" media-type="image/png" properties="cover-image"/>
%s
  </manifest>
  <spine toc="ncx">
%s
  </spine>
</package>
""" % (items, spine)


def opf2():
    items = "\n".join(
        '    <item id="%s" href="Text/%s.xhtml" media-type="application/xhtml+xml"/>' % (k, k)
        for k in CHAPTERS)
    spine = "\n".join('    <itemref idref="%s"/>' % k for k in CHAPTERS)
    return """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">urn:uuid:11111111-2222-3333-4444-555555555555</dc:identifier>
    <dc:title>EPUB2 测试书</dc:title>
    <dc:creator>老作者</dc:creator>
    <dc:language>zh-CN</dc:language>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="css" href="Styles/style.css" media-type="text/css"/>
%s
  </manifest>
  <spine toc="ncx">
%s
  </spine>
  <guide>
    <reference type="text" title="正文" href="Text/ch01.xhtml"/>
  </guide>
</package>
""" % (items, spine)


def build_epub(path, opf, with_nav, nav=None, epub2=False):
    if os.path.exists(path):
        os.remove(path)
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/toc.ncx", NCX, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/Styles/style.css", CSS, zipfile.ZIP_DEFLATED)
        zf.writestr("OEBPS/Images/cover.png", PNG, zipfile.ZIP_DEFLATED)
        if with_nav:
            zf.writestr("OEBPS/nav.xhtml", nav or NAV, zipfile.ZIP_DEFLATED)
        for k, (title, body) in CHAPTERS.items():
            zf.writestr("OEBPS/Text/%s.xhtml" % k,
                        chapter_xhtml(title, body, epub2=epub2), zipfile.ZIP_DEFLATED)


TXT_UTF8 = """\ufeff序章

风从北边来。

第一卷　山中来客

第一章　投宿

　　“有什么事吗？”老板娘从柜台后抬起头。
　　他说：真的说晚了……<script>alert(1)</script>
　　墙上写着一行字：A & B < C > D。

第二章　夜话

　　越早越好把……

第二卷　雾散之后

第三章　远行

　　马车碾过石板，颠得人骨头疼。

第四章　归途

　　再见吧，远野寺先生。
"""

TXT_GBK = """楔子

江南的雨下了三天。

第一章 初到

他撑着一把油纸伞。

第二章 旧识

茶凉了，人还没来。
"""


def main():
    os.makedirs(FIX, exist_ok=True)
    build_epub(os.path.join(FIX, "book3.epub"), opf3(), True)
    build_epub(os.path.join(FIX, "book2.epub"), opf2(), False, epub2=True)

    # 损坏的 EPUB
    with open(os.path.join(FIX, "broken.epub"), "wb") as f:
        f.write(b"this is not a zip file at all")

    with open(os.path.join(FIX, "sample_utf8.txt"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(TXT_UTF8)
    with open(os.path.join(FIX, "sample_gbk.txt"), "wb") as f:
        f.write(TXT_GBK.encode("gbk"))
    open(os.path.join(FIX, "empty.txt"), "wb").close()
    with open(os.path.join(FIX, "blank.txt"), "w", encoding="utf-8") as f:
        f.write("   \n\n\t\n  ")

    # 无标题的长文本，用于验证按长度切分
    paras = []
    for i in range(1, 41):
        paras.append("这是第 %d 段没有任何章节标题的连续正文，用来测试按长度切分的兜底逻辑是否正常工作。" % i)
    with open(os.path.join(FIX, "noheading.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(paras))

    build_calibre(os.path.join(FIX, "calibre.epub"))
    build_dirty(os.path.join(FIX, "dirty.epub"))
    build_dirty2(os.path.join(FIX, "dirty2.epub"))
    build_docx(os.path.join(FIX, "sample.docx"))
    build_docx(os.path.join(FIX, "plain.docx"), with_heading=False, has_image=False, has_table=False)

    # 异常样本：不是 ZIP / 空文件 / ZIP 里没有 document.xml
    with open(os.path.join(FIX, "broken.docx"), "wb") as f:
        f.write(b"definitely not a docx")
    open(os.path.join(FIX, "empty.docx"), "wb").close()
    with zipfile.ZipFile(os.path.join(FIX, "nodoc.docx"), "w") as zf:
        zf.writestr("word/styles.xml", DOCX_STYLES)

    print("fixtures 已生成：", FIX)




# ---------------------------------------------------------------------------
# Calibre 风格：OPF 在 EPUB/ 下，nav 在 EPUB/text/ 下，NCX 在 EPUB/ 根，带锚点
# ---------------------------------------------------------------------------

CAL_CONTAINER = """<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

CAL_NAV = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>目录</title></head>
<body>
  <nav epub:type="toc" id="toc">
    <ol>
      <li><a href="chapter0001.xhtml">第一话 起点</a>
        <ol>
          <li><a href="chapter0001.xhtml#sig1">第一节</a></li>
          <li><a href="chapter0001.xhtml#sig2">第二节</a></li>
        </ol>
      </li>
      <li><a href="chapter0002.xhtml">第二话 途中</a></li>
      <li><a href="chapter0003.xhtml#top">第三话 终点</a></li>
    </ol>
  </nav>
  <nav epub:type="page-list" hidden="hidden">
    <ol><li><a href="chapter0001.xhtml#p1">1</a></li></ol>
  </nav>
</body>
</html>
"""

CAL_NCX = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta name="dtb:uid" content="urn:uuid:123e4567-e89b-12d3-a456-426614174000"/>
    <meta name="dtb:depth" content="2"/>
  </head>
  <docTitle><text>Calibre 样本</text></docTitle>
  <navMap>
    <navPoint id="np1" playOrder="1">
      <navLabel><text>第一话 起点</text></navLabel>
      <content src="text/chapter0001.xhtml"/>
      <navPoint id="np2" playOrder="2">
        <navLabel><text>第一节</text></navLabel>
        <content src="text/chapter0001.xhtml#sig1"/>
      </navPoint>
    </navPoint>
    <navPoint id="np3" playOrder="3">
      <navLabel><text>第二话 途中</text></navLabel>
      <content src="text/chapter0002.xhtml"/>
    </navPoint>
    <navPoint id="np4" playOrder="4">
      <navLabel><text>第三话 终点</text></navLabel>
      <content src="text/chapter0003.xhtml#top"/>
    </navPoint>
  </navMap>
</ncx>
"""


def cal_chapter(n, title):
    return """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>%s</title>
<link rel="stylesheet" type="text/css" href="../styles/style0001.css"/>
</head>
<body>
  <h1 id="top">%s</h1>
  <p id="p1">正文开始。</p>
  <h2 id="sig1">第一节</h2><p>一。</p>
  <h2 id="sig2">第二节</h2><p>二。</p>
</body>
</html>
""" % (title, title)


def cal_opf():
    return """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uuid_id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uuid_id">urn:uuid:123e4567-e89b-12d3-a456-426614174000</dc:identifier>
    <dc:title>Calibre 样本</dc:title>
    <dc:creator>某人</dc:creator>
    <dc:language>zh</dc:language>
    <meta property="dcterms:modified">2021-05-05T05:05:05Z</meta>
  </metadata>
  <manifest>
    <item href="text/nav.xhtml" id="nav" media-type="application/xhtml+xml" properties="nav"/>
    <item href="toc.ncx" id="ncx" media-type="application/x-dtbncx+xml"/>
    <item href="styles/style0001.css" id="css" media-type="text/css"/>
    <item href="text/chapter0001.xhtml" id="c1" media-type="application/xhtml+xml"/>
    <item href="text/chapter0002.xhtml" id="c2" media-type="application/xhtml+xml"/>
    <item href="text/chapter0003.xhtml" id="c3" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="c1"/>
    <itemref idref="c2"/>
    <itemref idref="c3"/>
  </spine>
</package>
"""


def build_calibre(path):
    if os.path.exists(path):
        os.remove(path)
    with zipfile.ZipFile(path, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, "application/epub+zip")
        zf.writestr("META-INF/container.xml", CAL_CONTAINER, zipfile.ZIP_DEFLATED)
        zf.writestr("EPUB/content.opf", cal_opf(), zipfile.ZIP_DEFLATED)
        zf.writestr("EPUB/toc.ncx", CAL_NCX, zipfile.ZIP_DEFLATED)
        zf.writestr("EPUB/text/nav.xhtml", CAL_NAV, zipfile.ZIP_DEFLATED)
        zf.writestr("EPUB/styles/style0001.css", CSS, zipfile.ZIP_DEFLATED)
        for i, t in enumerate(["第一话 起点", "第二话 途中", "第三话 终点"], start=1):
            zf.writestr("EPUB/text/chapter%04d.xhtml" % i, cal_chapter(i, t), zipfile.ZIP_DEFLATED)




# ---------------------------------------------------------------------------
# DOCX 样本：标题 / 加粗斜体 / 列表 / 表格 / 图片
# ---------------------------------------------------------------------------

DOCX_CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
  <Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>
</Types>
"""

DOCX_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
</Relationships>
"""

DOCX_CORE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
  xmlns:dc="http://purl.org/dc/elements/1.1/">
  <dc:title>DOCX 测试文稿</dc:title>
  <dc:creator>文档作者</dc:creator>
  <dc:language>zh-CN</dc:language>
</cp:coreProperties>
"""

DOCX_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/></w:style>
  <w:style w:type="paragraph" w:styleId="2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/></w:style>
</w:styles>
"""

DOCX_NUM = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/></w:lvl>
    <w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
</w:numbering>
"""

DOCX_DOC_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId10" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>
</Relationships>
"""


def docx_document(with_heading=True, has_image=True, has_table=True):
    def p(text, style=None, bold=False, italic=False, num=None, ilvl=0):
        ppr = ""
        if style or num:
            ppr = "<w:pPr>"
            if style:
                ppr += '<w:pStyle w:val="%s"/>' % style
            if num:
                ppr += '<w:numPr><w:ilvl w:val="%d"/><w:numId w:val="%d"/></w:numPr>' % (ilvl, num)
            ppr += "</w:pPr>"
        rpr = ""
        if bold:
            rpr += "<w:b/>"
        if italic:
            rpr += "<w:i/>"
        rpr = "<w:rPr>%s</w:rPr>" % rpr if rpr else ""
        return '<w:p>%s<w:r>%s<w:t xml:space="preserve">%s</w:t></w:r></w:p>' % (ppr, rpr, text)

    drawing = ""
    if has_image:
        drawing = '''<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">
  <wp:extent cx="1905000" cy="1905000"/>
  <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData>
    <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
      <pic:blipFill><a:blip r:embed="rId10" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"/></pic:blipFill>
    </pic:pic>
  </a:graphicData></a:graphic>
</wp:inline></w:drawing></w:r></w:p>'''

    table = ""
    if has_table:
        table = ('<w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc>'
                 '<w:tc><w:p><w:r><w:t>数值</w:t></w:r></w:p></w:tc></w:tr>'
                 '<w:tr><w:tc><w:p><w:r><w:t>字数</w:t></w:r></w:p></w:tc>'
                 '<w:tc><w:p><w:r><w:t>一万</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')

    parts = []
    if with_heading:
        parts.append(p("第一章　来自 Word", style="1"))
    parts.append('<w:p><w:r><w:t>这是一个</w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>加粗</w:t></w:r>'
                 '<w:r><w:t>、</w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>斜体</w:t></w:r>'
                 '<w:r><w:t>混排的段落，含特殊字符 A &amp; B &lt; C。</w:t></w:r></w:p>')
    parts += [p("列表第一项", num=1, ilvl=0), p("列表第二项", num=1, ilvl=0),
              p("嵌套子项", num=1, ilvl=1)]
    parts.append(table)
    parts.append(drawing)
    if with_heading:
        parts.append(p("第二章　表格与图片", style="1"))
        parts.append(p("第二章的正文，用于验证章节切分。"))
        parts.append(p("2.1　一个小节", style="2"))
        parts.append(p("小节内的正文。"))

    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:body>%s<w:sectPr/></w:body></w:document>' % "".join(parts))


def build_docx(path, with_heading=True, has_image=True, has_table=True):
    if os.path.exists(path):
        os.remove(path)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", DOCX_CT)
        zf.writestr("_rels/.rels", DOCX_RELS)
        zf.writestr("docProps/core.xml", DOCX_CORE)
        zf.writestr("word/styles.xml", DOCX_STYLES)
        zf.writestr("word/numbering.xml", DOCX_NUM)
        zf.writestr("word/document.xml", docx_document(with_heading, has_image, has_table))
        zf.writestr("word/_rels/document.xml.rels", DOCX_DOC_RELS)
        if has_image:
            zf.writestr("word/media/image1.png", PNG)


# ---------------------------------------------------------------------------
# 脏样本：目录指向缺失文件、清单里挂着不存在的文件、正文顺序引用无效、唯一标识错
# ---------------------------------------------------------------------------

def build_dirty(path):
    src = os.path.join(FIX, "book3.epub")
    if not os.path.exists(src):
        build_epub(src, opf3(), True)
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(path, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "OEBPS/nav.xhtml":
                text = data.decode("utf-8")
                text = text.replace(
                    "      <li><a href=\"Text/ch04.xhtml\">第四章 归途</a></li>",
                    "      <li><a href=\"Text/ch04.xhtml\">第四章 归途</a></li>\n"
                    "      <li><a href=\"Text/missing.xhtml\">不存在的章节</a></li>")
                data = text.encode("utf-8")
            elif info.filename == "OEBPS/content.opf":
                text = data.decode("utf-8")
                text = text.replace('unique-identifier="bookid"', 'unique-identifier="nope"')
                text = text.replace(
                    '    <item id="cover" href="Images/cover.png" media-type="image/png" properties="cover-image"/>',
                    '    <item id="cover" href="Images/cover.png" media-type="image/png" properties="cover-image"/>\n'
                    '    <item id="ghost" href="Text/ghost.xhtml" media-type="application/xhtml+xml"/>')
                text = text.replace(
                    '    <itemref idref="ch04"/>',
                    '    <itemref idref="ch04"/>\n    <itemref idref="nope-ref"/>')
                data = text.encode("utf-8")
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zout.writestr(zi, data)


def build_dirty2(path):
    """包层损坏样本：重复 id / 重复登记 / 空元数据 / 悬空封面 / 未登记文件 / 坏 XML"""
    src = os.path.join(FIX, "book3.epub")
    if not os.path.exists(src):
        build_epub(src, opf3(), True)
    zin = zipfile.ZipFile(src)
    with zipfile.ZipFile(path, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "OEBPS/content.opf":
                text = data.decode("utf-8")
                # 重复 id：ch02 的条目改叫 ch01，spine 同步 → 编号重复且引用配对
                text = text.replace('<item id="ch02"', '<item id="ch01"')
                text = text.replace('<itemref idref="ch02"/>', '<itemref idref="ch01"/>')
                # 重复登记：ch04 再登记一份（ch04b），并占一个 spine 引用
                text = text.replace(
                    '    <item id="ch04" href="Text/ch04.xhtml" media-type="application/xhtml+xml"/>',
                    '    <item id="ch04" href="Text/ch04.xhtml" media-type="application/xhtml+xml"/>\n'
                    '    <item id="ch04b" href="Text/ch04.xhtml" media-type="application/xhtml+xml"/>')
                text = text.replace(
                    '    <itemref idref="ch04"/>',
                    '    <itemref idref="ch04"/>\n    <itemref idref="ch04b"/>')
                # 必填元数据清空
                text = text.replace("<dc:title>测试之书</dc:title>", "<dc:title></dc:title>")
                text = text.replace("<dc:language>zh-CN</dc:language>", "<dc:language></dc:language>")
                # 封面标记指向不存在的条目
                text = text.replace(
                    '<meta property="dcterms:modified">',
                    '<meta name="cover" content="no-such-id"/>\n'
                    '    <meta property="dcterms:modified">')
                data = text.encode("utf-8")
            elif info.filename == "OEBPS/Text/ch03.xhtml":
                # HTML 习惯写法：命名实体 + 裸 & + 未自闭合空元素（XML 解析必挂）
                text = data.decode("utf-8")
                text = text.replace(
                    "</body>",
                    "  <p>坏&nbsp;实体 & 裸连 <br>空元素</p>\n</body>")
                data = text.encode("utf-8")
            zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zout.writestr(zi, data)
        # 包内存在但没登记进 manifest 的文件
        zi = zipfile.ZipInfo("OEBPS/Styles/unused.css", date_time=(2020, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_DEFLATED
        zout.writestr(zi, "p { color: #333; }\n")


if __name__ == "__main__":
    main()
