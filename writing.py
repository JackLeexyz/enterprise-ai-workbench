import re


def clean_text(value, maximum):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError("名称或标题不能为空，且不能超过允许长度。")
    return value.strip()


def definition(payload):
    name = clean_text(payload.get("name"), 60)
    fields, sections = payload.get("fields"), payload.get("sections")
    active = payload.get("active", True)
    if type(active) is not bool or not isinstance(fields, list) or not 1 <= len(fields) <= 12 or not isinstance(sections, list) or not 1 <= len(sections) <= 8:
        raise ValueError("模板需要1至12个字段、1至8个章节，以及有效的启用状态。")
    keys, field_rows = set(), []
    for field in fields:
        if not isinstance(field, dict):
            raise ValueError("字段结构无效。")
        key = field.get("key")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key) or key in keys or type(field.get("required")) is not bool or type(field.get("multiline")) is not bool:
            raise ValueError("字段标识须为不重复的英文小写/数字/下划线；必填和多行须为布尔值。")
        keys.add(key)
        field_rows.append({"key": key, "label": clean_text(field.get("label"), 30), "required": field["required"], "multiline": field["multiline"]})
    section_keys, used, section_rows = set(), set(), []
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("章节结构无效。")
        key, mapped = section.get("key"), section.get("field_keys")
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key) or key in section_keys or not isinstance(mapped, list) or len(mapped) > 12 or any(not isinstance(item, str) or item not in keys for item in mapped) or len(set(mapped)) != len(mapped):
            raise ValueError("章节标识或所选字段无效，请检查是否引用了已删除字段。")
        section_keys.add(key)
        used.update(mapped)
        section_rows.append({"key": key, "title": clean_text(section.get("title"), 60), "field_keys": mapped})
    if used != keys:
        raise ValueError("每个字段至少要分配到一个章节。")
    return {"name": name, "fields": field_rows, "sections": section_rows, "active": active}


def values(template, data):
    if not isinstance(data, dict):
        raise ValueError("文档信息必须是字段对象。")
    allowed = {field["key"] for field in template["fields"]} if template else set()
    if set(data) - allowed:
        raise ValueError("存在当前绑定模板未定义的字段。")
    result = {}
    for key, value in data.items():
        if not isinstance(value, str) or len(value) > 1000:
            raise ValueError("每个字段须为不超过1000字的文本。")
        if value.strip():
            result[key] = value.strip()
    return result


def draft_blocks(template, data, notes, make_id):
    fields = {field["key"]: field for field in template["fields"]}
    blocks = []
    for section in template["sections"]:
        blocks.append({"id": make_id("b"), "type": "heading", "text": section["title"]})
        for key in section["field_keys"]:
            label = fields[key]["label"]
            blocks.append({"id": make_id("b"), "type": "paragraph", "text": label + "：" + (data.get(key) or "[待确认]")})
        if not section["field_keys"]:
            blocks.append({"id": make_id("b"), "type": "paragraph", "text": "[待确认] 请补充本节内容。"})
    if notes:
        blocks += [{"id": make_id("b"), "type": "heading", "text": "补充信息"}, {"id": make_id("b"), "type": "paragraph", "text": notes}]
    return blocks


def checks(template, data, content, reference_issues):
    missing = [{"key": field["key"], "label": field["label"]} for field in template["fields"] if field["required"] and not data.get(field["key"], "").strip()] if template else []
    unconfirmed = [{"id": block["id"], "preview": block["text"][:100]} for block in content["blocks"] if "[待确认]" in block["text"]]
    return {"missing_fields": missing, "unconfirmed_blocks": unconfirmed, "reference_issues": reference_issues, "legacy_template": template is None, "can_submit_review": not missing and not reference_issues and sum(len(block["text"]) for block in content["blocks"]) >= 20}
