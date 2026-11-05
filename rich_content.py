"""Bounded structured text; never store executable HTML or fetch link targets."""
import re
from urllib.parse import urlsplit

BLOCK_TYPES = {"heading", "paragraph", "bullet", "table"}
MAX_RUNS = 2000


def invalid(message="正文结构无效或超过允许长度。"):
    raise ValueError(message)


def invalid_xml(value):
    return any((ord(char) < 32 and char not in "\n\t") or 0xD800 <= ord(char) <= 0xDFFF or ord(char) in {0xFFFE, 0xFFFF} for char in value)


def safe_link(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or re.search(r"[\x00-\x20\x7f\\]", value) or invalid_xml(value):
        invalid("链接只接受有效的 HTTP/HTTPS 地址。")
    try:
        url = urlsplit(value)
        if url.scheme.lower() not in {"http", "https"} or not url.hostname or url.username is not None or url.password is not None:
            invalid("链接不接受脚本、内嵌数据或带账号口令的地址。")
        url.port
    except (ValueError, UnicodeError):
        invalid("链接地址无效。")
    return value


def runs(value, budget):
    if not isinstance(value, list) or len(value) > 200:
        invalid("每段最多200个行内片段。")
    output, length = [], 0
    for run in value:
        if not isinstance(run, dict) or set(run) - {"text", "marks"} or not isinstance(run.get("text"), str):
            invalid("行内片段仅接受文字和允许的样式。")
        body, marks = run["text"], run.get("marks", [])
        if not isinstance(marks, list) or len(marks) > 3:
            invalid("行内样式无效。")
        clean, seen = [], set()
        for mark in marks:
            if not isinstance(mark, dict) or not isinstance(mark.get("type"), str) or mark["type"] not in {"bold", "italic", "link"} or mark["type"] in seen:
                invalid("只支持不重复的粗体、斜体和链接样式。")
            kind = mark["type"]
            if set(mark) != ({"type", "href"} if kind == "link" else {"type"}):
                invalid("样式包含不支持的属性。")
            clean.append({"type": kind, **({"href": safe_link(mark["href"])} if kind == "link" else {})})
            seen.add(kind)
        budget[0] += 1
        length += len(body)
        if budget[0] > MAX_RUNS or length > 12000 or invalid_xml(body):
            invalid("行内文字过长或包含不支持的控制字符。")
        if body:
            item = {"text": body}
            if clean:
                item["marks"] = sorted(clean, key=lambda mark: {"bold": 0, "italic": 1, "link": 2}[mark["type"]])
            output.append(item)
    return output


def plain_runs(value):
    return "".join(run["text"] for run in value)


def mirrored(item, plain):
    if "text" in item and item["text"] != plain:
        invalid("纯文本摘要与结构化正文不一致，拒绝覆盖。")
    return plain


def validate(value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 2 or set(value) != {"schema_version", "blocks"}:
        invalid("结构化正文须使用schema_version=2。")
    if not isinstance(value["blocks"], list) or not 1 <= len(value["blocks"]) <= 100:
        invalid("正文需要1至100个内容块。")
    blocks, ids, budget, characters, cells = [], set(), [0], 0, 0
    for block in value["blocks"]:
        if not isinstance(block, dict) or not isinstance(block.get("type"), str) or block["type"] not in BLOCK_TYPES:
            invalid("内容类型不支持；图片和任意HTML尚未开放。")
        bid, kind = block.get("id"), block["type"]
        if not isinstance(bid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", bid) or bid in ids:
            invalid("内容块标识无效或重复。")
        ids.add(bid)
        if kind == "table":
            if set(block) - {"id", "type", "text", "rows", "header"} or type(block.get("header", False)) is not bool or not isinstance(block.get("rows"), list) or not 1 <= len(block["rows"]) <= 20:
                invalid("表格仅支持1至20行和布尔表头设置。")
            rows, width = [], None
            for row in block["rows"]:
                if not isinstance(row, list) or not 1 <= len(row) <= 8 or (width is not None and width != len(row)):
                    invalid("表格需为最多8列的矩形结构。")
                width = len(row)
                clean_row = []
                for cell in row:
                    if not isinstance(cell, dict) or set(cell) - {"text", "runs"}:
                        invalid("表格单元格不接受合并、嵌套或其他属性。")
                    fragments = runs(cell.get("runs"), budget)
                    clean_row.append({"text": mirrored(cell, plain_runs(fragments)), "runs": fragments})
                    cells += 1
                    if cells > 200:
                        invalid("每份稿件最多200个表格单元格。")
                rows.append(clean_row)
            body = "\n".join("\t".join(cell["text"] for cell in row) for row in rows)
            result = {"id": bid, "type": kind, "text": mirrored(block, body), "header": block.get("header", False), "rows": rows}
        else:
            if set(block) - {"id", "type", "text", "runs", "level"} or ("level" in block and kind != "heading"):
                invalid("文字块包含不支持的属性。")
            fragments = runs(block.get("runs"), budget)
            body = plain_runs(fragments)
            result = {"id": bid, "type": kind, "text": mirrored(block, body), "runs": fragments}
            if kind == "heading":
                if type(block.get("level")) is not int or block["level"] not in {1, 2, 3}:
                    invalid("标题层级须为1、2或3。")
                result["level"] = block["level"]
        characters += len(body)
        if len(body) > 12000 or characters > 50000:
            invalid("单块最多12000字，正文合计最多50000字。")
        blocks.append(result)
    return {"schema_version": 2, "blocks": blocks}


def replace_text(block, body):
    if block["type"] == "table":
        invalid("表格不能作为整段摘录替换目标。")
    block["text"] = body
    if "runs" in block:
        block["runs"] = [{"text": body}]


def quote_block(bid, body, schema_version):
    result = {"id": bid, "type": "paragraph", "text": body}
    if schema_version == 2:
        result["runs"] = [{"text": body}]
    return result
