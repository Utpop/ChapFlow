# -*- coding: utf-8 -*-
"""测试用：生成最小可用的 DOCX。

章节标题一律写成普通段落（不带「标题 1」样式），用来验证
“文档没有标题样式时按正文里的「第X章」兜底切分”。
"""

from __future__ import annotations

import zipfile

CT = ('<?xml version="1.0" encoding="UTF-8"?>'
      '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
      '<Default Extension="xml" ContentType="application/xml"/>'
      '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
      '<Override PartName="/word/document.xml" '
      'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
      '</Types>')

RELS = ('<?xml version="1.0" encoding="UTF-8"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>')


def _para(text: str) -> str:
    return ('<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>'
            % text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build_docx(path: str, paragraphs) -> str:
    """paragraphs: 纯文本段落列表（章节标题与正文都按普通段落写）"""
    body = "".join(_para(t) for t in paragraphs)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           '<w:body>%s<w:sectPr/></w:body></w:document>' % body)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", CT)
        zf.writestr("_rels/.rels", RELS)
        zf.writestr("word/document.xml", doc)
    return path
