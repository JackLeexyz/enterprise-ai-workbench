from __future__ import annotations

from email import policy
from email.parser import BytesParser
import io
from pathlib import PurePosixPath
import zipfile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import etree

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = MAX_FILE_BYTES + 64 * 1024
MAX_TEXT_CHARS = 40000


class ImportProblem(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def reject(code, message):
    raise ImportProblem(code, message)


def validate_file(filename, content):
    if not isinstance(filename, str) or not 1 <= len(filename) <= 160 or any(ord(char) < 32 or ord(char) == 127 for char in filename) or any(char in filename for char in "/\\:") or filename in {".", ".."}:
        reject("UNSUPPORTED_FILE", "文件名无效，请使用不含路径和控制字符的文件名。")
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in {".txt", ".md", ".docx", ".pdf"}:
        reject("UNSUPPORTED_FILE", "当前仅支持 DOCX、TXT、MD 和文本 PDF；不接受宏文档或压缩包。")
    if not isinstance(content, bytes) or not content:
        reject("NO_TEXT", "文件为空，请选择包含文字的文件。")
    if len(content) > MAX_FILE_BYTES:
        reject("FILE_TOO_LARGE", "单文件不能超过 2 MiB。")
    return extension


def read_multipart(content_type, payload):
    if len(payload) > MAX_UPLOAD_BYTES:
        reject("FILE_TOO_LARGE", "上传请求超过限制。")
    try:
        if len(content_type) > 1024:
            raise ValueError()
        message = BytesParser(policy=policy.default).parsebytes(b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + payload)
        boundary = message.get_boundary()
        if message.get_content_type() != "multipart/form-data" or not boundary or len(boundary) > 100 or not message.is_multipart() or message.defects:
            raise ValueError()
        parts = list(message.iter_parts())
        if len(parts) != 1:
            raise ValueError()
        part = parts[0]
        if part.get_content_disposition() != "form-data" or part.get_param("name", header="content-disposition") != "file" or part.is_multipart() or part.defects or part.get("Content-Transfer-Encoding"):
            raise ValueError()
        filename, content = part.get_filename(), part.get_payload(decode=True)
    except (ValueError, TypeError, UnicodeError):
        reject("INVALID_UPLOAD", "请提交一个名为 file 的文件，不接受多文件或嵌套上传。")
    validate_file(filename, content)
    return filename, content


def sections_from_units(units):
    total = sum(len(text) + 1 for _, text, _ in units)
    if total > MAX_TEXT_CHARS:
        reject("TEXT_TOO_LARGE", "解析文字超过 40000 字，请拆分文件后上传。")
    sections, current, length, kind = [], [], 0, None
    def flush():
        if current:
            title = current[0][0] if len(current) == 1 else current[0][0] + " - " + current[-1][0]
            sections.append({"title": title, "text": "\n".join(item[1] for item in current)})
            current.clear()
    for locator, text, unit_kind in units:
        if current and (kind != unit_kind or length + len(text) > 3500):
            flush()
            length = 0
        for offset in range(0, len(text), 3500):
            part = text[offset:offset + 3500]
            if current and length + len(part) > 3500:
                flush()
                length = 0
            current.append((locator + (f" 片段{offset // 3500 + 1}" if offset else ""), part))
            length += len(part) + 1
            kind = unit_kind
    flush()
    if not sections:
        reject("NO_TEXT", "没有可入库的文字；图片和扫描内容不在本批解析范围。")
    if len(sections) > 120:
        reject("TEXT_TOO_LARGE", "内容结构超过 120 段，请拆分文件后上传。")
    return sections


def word_units(content):
    warnings = ["只提取主文档段落与表格文字；不保留原样分页、页眉页脚或修订批注。"]
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > 256 or sum(item.file_size for item in entries) > 12 * 1024 * 1024:
                reject("DOCX_ARCHIVE_LIMIT", "DOCX 展开内容超过安全限制。")
            names = {item.filename for item in entries}
            if len(names) != len(entries) or not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                reject("INVALID_DOCX", "文件不是受支持的 DOCX 文档。")
            for item in entries:
                parts = PurePosixPath(item.filename).parts
                lower = item.filename.lower()
                if item.filename.startswith("/") or ".." in parts or "\\" in item.filename or item.flag_bits & 1 or any(value in lower for value in ("vbaproject", "activex", "embeddings/")):
                    reject("UNSAFE_DOCX", "不接受加密、宏、嵌入对象或异常路径内容。")
                if item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                    reject("UNSAFE_DOCX", "DOCX 使用了不支持的压缩方式。")
                if item.file_size > 4 * 1024 * 1024 or item.file_size / max(1, item.compress_size) > 150:
                    reject("DOCX_ARCHIVE_LIMIT", "DOCX 压缩展开比例或单项大小超过安全限制。")
                if lower.endswith((".xml", ".rels")):
                    parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, recover=False, huge_tree=False)
                    xml = etree.fromstring(archive.read(item), parser)
                    if xml.getroottree().docinfo.doctype:
                        reject("UNSAFE_DOCX", "文档包含不支持的 XML 声明。")
                    if lower.endswith(".rels") and any(node.get("TargetMode", "").lower() == "external" for node in xml.iter()):
                        if "外部链接仅保留显示文字，不访问链接或加载外部资源。" not in warnings:
                            warnings.append("外部链接仅保留显示文字，不访问链接或加载外部资源。")
                if lower.startswith("word/media/") and "图片未做 OCR，相关内容未入库。" not in warnings:
                    warnings.append("图片未做 OCR，相关内容未入库。")
        document = Document(io.BytesIO(content))
        units, paragraph_number, table_number, characters = [], 0, 0, 0
        def add_unit(locator, body, kind):
            nonlocal characters
            if body:
                characters += len(body) + 1
                if characters > MAX_TEXT_CHARS or len(units) >= 2000:
                    reject("TEXT_TOO_LARGE", "解析文字或结构过多，请拆分文件后上传。")
                units.append((locator, body, kind))
        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                paragraph_number += 1
                text = block.text.strip()
                add_unit(f"第{paragraph_number}段", text, "paragraph")
            elif isinstance(block, Table):
                table_number += 1
                for row_number, row in enumerate(block.rows, 1):
                    cells, seen = [], set()
                    for cell in row.cells:
                        if cell._tc not in seen:
                            cells.append(cell.text.strip())
                            seen.add(cell._tc)
                    text = " | ".join(cells).strip(" |")
                    add_unit(f"表{table_number}第{row_number}行", text, f"table{table_number}")
        return units, warnings
    except ImportProblem:
        raise
    except Exception:
        reject("INVALID_DOCX", "DOCX 已损坏、已加密或结构不受支持；请另存为普通 DOCX 或文本。")


def parse_document(filename, content):
    extension = validate_file(filename, content)
    if extension == ".pdf":
        from pdf_import import parse_pdf
        return parse_pdf(content)
    if extension == ".docx":
        units, warnings = word_units(content)
    else:
        try:
            encoding = "utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
            text = content.decode(encoding)
        except UnicodeError:
            reject("TEXT_ENCODING", "文字编码不受支持，请使用 UTF-8 或带 BOM 的 UTF-16。")
        if any(ord(char) < 32 and char not in "\n\r\t" for char in text) or "\x7f" in text:
            reject("BINARY_TEXT", "文件包含二进制或控制字符，不能作为文字入库。")
        units = [(f"第{number}行", line.strip(), "text") for number, line in enumerate(text.splitlines(), 1) if line.strip()]
        warnings = ["Markdown 按纯文本提取，HTML、脚本和外部链接不会执行。"] if extension == ".md" else []
    sections = sections_from_units(units)
    return {"sections": sections, "warnings": warnings, "characters": sum(len(item["text"]) for item in sections)}
