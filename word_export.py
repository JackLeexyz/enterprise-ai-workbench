from __future__ import annotations

import io
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.shared import Cm, Inches, Pt, RGBColor
import rich_content


def inline(paragraph, fragments, header=False):
    for fragment in fragments:
        run = paragraph.add_run(fragment["text"])
        marks = {mark["type"]: mark for mark in fragment.get("marks", [])}
        run.bold, run.italic = header or "bold" in marks, "italic" in marks
        if "link" in marks:
            link = OxmlElement("w:hyperlink")
            link.set(qn("r:id"), paragraph.part.relate_to(rich_content.safe_link(marks["link"]["href"]), RT.HYPERLINK, is_external=True))
            run.font.color.rgb, run.font.underline = RGBColor.from_string("315BC9"), True
            link.append(run._r)
            paragraph._p.append(link)


def structured_block(document, block):
    if block["type"] != "table":
        paragraph = document.add_heading(level=block["level"]) if block["type"] == "heading" else document.add_paragraph(style="List Bullet" if block["type"] == "bullet" else None)
        inline(paragraph, block["runs"])
        return
    table = document.add_table(rows=len(block["rows"]), cols=len(block["rows"][0]))
    table.style = "Table Grid"
    table.autofit = False
    for row_index, row in enumerate(block["rows"]):
        if block["header"] and row_index == 0:
            table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for column, cell in enumerate(row):
            target = table.cell(row_index, column)
            section = document.sections[0]
            target.width = (section.page_width - section.left_margin - section.right_margin) // len(row)
            inline(target.paragraphs[0], cell["runs"], block["header"] and row_index == 0)


def render_word(title, content, references, revision):
    if content.get("schema_version") == 2:
        content = rich_content.validate(content)
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Cm(2)
    section.left_margin = section.right_margin = Cm(2.2)
    normal = document.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(11)
    normal._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Source Han Sans SC")
    normal.paragraph_format.space_after = Pt(7)
    normal.paragraph_format.line_spacing = 1.35
    for name, size in [("Title", 24), ("Subtitle", 12), ("Heading 1", 14), ("Heading 2", 12), ("Heading 3", 11)]:
        style = document.styles[name]
        style.font.name = "Arial"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(0, 0, 0)
        fonts = style._element.get_or_add_rPr().rFonts
        fonts.set(qn("w:eastAsia"), "Source Han Sans SC")
        for attribute in ("asciiTheme", "hAnsiTheme", "eastAsiaTheme", "cstheme"):
            fonts.attrib.pop(qn("w:" + attribute), None)
        for border in style._element.findall(".//" + qn("w:pBdr")):
            border.getparent().remove(border)
        style.paragraph_format.space_before = Pt(14 if "Heading" in name else 0)
        style.paragraph_format.space_after = Pt(8)
    heading = document.add_paragraph(title, "Title")
    for run in heading.runs:
        run.font.color.rgb = RGBColor(0, 0, 0)
    document.add_paragraph(f"虚构资料演示文档  已审修订 r{revision}  不构成正式签章或客户验收证明")
    for block in content["blocks"]:
        if content["schema_version"] == 2:
            structured_block(document, block)
        elif block["type"] == "heading":
            document.add_heading(block["text"], level=1)
        elif block["type"] == "bullet":
            document.add_paragraph(block["text"], "List Bullet")
        else:
            document.add_paragraph(block["text"])
    if references:
        document.add_heading("引用资料与版本", level=1)
        for number, ref in enumerate(references, 1):
            document.add_paragraph(f"[{number}] {ref['title']}  {ref['label']}  来源版本 {ref['version_id']}")
    document.add_paragraph("本文件绑定上述审核修订。后续来源或正文变化不改变已经下载的文件，重新下载需再次通过权限与状态检查。")
    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()
