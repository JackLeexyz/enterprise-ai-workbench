import io
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import unittest
import zipfile
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import rich_content
from application import Application
from store import Problem, Store
from word_export import render_word


def sample():
    return {"schema_version": 2, "blocks": [
        {"id": "h1", "type": "heading", "level": 1, "runs": [{"text": "虚构实施方案"}]},
        {"id": "h2", "type": "heading", "level": 2, "runs": [{"text": "交付准备"}]},
        {"id": "h3", "type": "heading", "level": 3, "runs": [{"text": "资料核对"}]},
        {"id": "p1", "type": "paragraph", "runs": [{"text": "重要内容", "marks": [{"type": "bold"}, {"type": "italic"}]}, {"text": "保留空格 "}, {"text": "参考资料", "marks": [{"type": "link", "href": "https://example.com/docs?x=1&y=2"}]}]},
        {"id": "b1", "type": "bullet", "runs": [{"text": "完成清单后由负责人审核，不自动视为验收通过。"}]},
        {"id": "t1", "type": "table", "header": True, "rows": [[{"runs": [{"text": "资料"}]}, {"runs": [{"text": "状态"}]}], [{"runs": [{"text": "培训记录"}]}, {"runs": [{"text": "[待确认]", "marks": [{"type": "bold"}]}]}]]}
    ]}


class RichContentTests(unittest.TestCase):
    def invalid(self, value):
        with self.assertRaises(ValueError):
            rich_content.validate(value)

    def test_normalization_is_idempotent_and_text_is_derived(self):
        value = rich_content.validate(sample())
        self.assertEqual(value, rich_content.validate(value))
        self.assertEqual(value["blocks"][3]["text"], "重要内容保留空格 参考资料")
        self.assertEqual(value["blocks"][5]["text"], "资料\t状态\n培训记录\t[待确认]")

    def test_inconsistent_mirrors_are_rejected_not_silently_accepted(self):
        for target in ("paragraph", "table", "cell"):
            value = sample()
            node = value["blocks"][3] if target == "paragraph" else value["blocks"][5] if target == "table" else value["blocks"][5]["rows"][0][0]
            node["text"] = "伪造另一个摘要"
            self.invalid(value)

    def test_heading_levels_and_schema_reject_booleans_or_unknown_values(self):
        for level in (True, False, 0, 4, "1", None):
            value = sample(); value["blocks"][0]["level"] = level
            self.invalid(value)
        self.invalid({**sample(), "schema_version": True})
        self.invalid({**sample(), "extra": "HTML"})

    def test_unsafe_link_schemes_credentials_controls_and_invalid_ports(self):
        for href in ("javascript:alert(1)", "data:text/html,test", "//example.com", "/local", "file:///tmp/test", "https://name:secret@example.com", "https://example.com:99999", "https://example.com\\@other.test", "https://example.com\n", "https://", "https://exa mple.com"):
            with self.subTest(href=href):
                value = sample(); value["blocks"][3]["runs"][2]["marks"][0]["href"] = href
                self.invalid(value)

    def test_regular_links_unicode_and_ampersand_are_preserved(self):
        for href in ("https://example.com/a?q=1&b=2#part", "HTTP://example.com/", "https://例子.测试/资料"):
            self.assertEqual(rich_content.safe_link(href), href)

    def test_unrecognized_marks_duplicate_marks_and_html_attributes_fail(self):
        for marks in ([{"type": "underline"}], [{"type": "bold"}, {"type": "bold"}], [{"type": "bold", "onclick": "x"}], [{"type": "link", "href": "https://example.com", "target": "_self"}], [None], "bold"):
            value = sample(); value["blocks"][3]["runs"][0]["marks"] = marks
            self.invalid(value)
        value = sample(); value["blocks"][3]["runs"][0]["html"] = "<script>x</script>"
        self.invalid(value)

    def test_raw_html_is_only_literal_text_and_unsupported_nodes_are_rejected(self):
        value = sample(); value["blocks"][3]["runs"] = [{"text": "<script>alert(1)</script>"}]
        self.assertEqual(rich_content.validate(value)["blocks"][3]["text"], "<script>alert(1)</script>")
        for kind in ("image", "html", "iframe", [], {}):
            value = sample(); value["blocks"][3]["type"] = kind
            self.invalid(value)

    def test_table_requires_rectangle_and_disallows_nested_or_merged_cells(self):
        for mode in ("ragged", "nested", "merged", "wrong_header", "empty"):
            value = sample(); table = value["blocks"][5]
            if mode == "ragged": table["rows"][1].pop()
            elif mode == "nested": table["rows"][0][0]["table"] = []
            elif mode == "merged": table["rows"][0][0]["colspan"] = 2
            elif mode == "wrong_header": table["header"] = 1
            else: table["rows"] = []
            self.invalid(value)

    def test_tables_rows_columns_and_total_cells_are_bounded(self):
        cell = {"runs": [{"text": "x"}]}
        for rows in ([[cell] for _ in range(21)], [[cell] * 9]):
            self.invalid({"schema_version": 2, "blocks": [{"id": "t", "type": "table", "rows": rows}]})
        table = {"id": "t", "type": "table", "rows": [[cell] * 8 for _ in range(20)]}
        self.invalid({"schema_version": 2, "blocks": [table, {**table, "id": "t2"}]})

    def test_text_run_block_and_document_limits(self):
        for runs in ([{"text": "x"}] * 201, [{"text": "x" * 12001}], [{"text": "\x00"}], [{"text": "\ud800"}], [{"text": "\uffff"}]):
            self.invalid({"schema_version": 2, "blocks": [{"id": "p", "type": "paragraph", "runs": runs}]})
        self.invalid({"schema_version": 2, "blocks": [{"id": "p" + str(i), "type": "paragraph", "runs": [{"text": "x" * 11000}]} for i in range(5)]})
        self.invalid({"schema_version": 2, "blocks": [{"id": "p" + str(i), "type": "paragraph", "runs": [{"text": "x"}] * 200} for i in range(11)]})

    def test_block_identity_and_extra_properties_are_rejected(self):
        for change in ({"id": "<bad>"}, {"id": "h1"}, {"onclick": "x"}, {"level": 2}):
            value = sample(); value["blocks"][3].update(change)
            self.invalid(value)

    def test_empty_paragraph_and_cell_are_supported_without_empty_mark_runs(self):
        value = sample(); value["blocks"][3]["runs"] = [{"text": "", "marks": [{"type": "bold"}]}]
        value["blocks"][5]["rows"][1][1] = {"runs": []}
        clean = rich_content.validate(value)
        self.assertEqual(clean["blocks"][3]["runs"], [])
        self.assertEqual(clean["blocks"][5]["rows"][1][1]["text"], "")

    def test_docx_contains_heading_styles_table_inline_marks_and_external_link(self):
        output = render_word("虚构结构化样稿", sample(), [], 2)
        with zipfile.ZipFile(io.BytesIO(output)) as archive:
            document = ET.fromstring(archive.read("word/document.xml"))
            xml = archive.read("word/document.xml").decode()
            relationships = ET.fromstring(archive.read("word/_rels/document.xml.rels"))
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        for heading in ("Heading1", "Heading2", "Heading3"):
            self.assertIn('w:val="' + heading + '"', xml)
        self.assertEqual(len(document.findall(".//w:tbl", ns)), 1)
        self.assertEqual(len(document.findall(".//w:tc", ns)), 4)
        self.assertTrue(document.findall(".//w:tblHeader", ns))
        marked = next(run for run in document.findall(".//w:r", ns) if "重要内容" in "".join(run.itertext()))
        self.assertIsNotNone(marked.find("w:rPr/w:b", ns)); self.assertIsNotNone(marked.find("w:rPr/w:i", ns))
        link = next(row for row in relationships if row.attrib["Type"].endswith("/hyperlink"))
        self.assertEqual(link.attrib["Target"], "https://example.com/docs?x=1&y=2")
        self.assertEqual(link.attrib["TargetMode"], "External")


class RichWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="rich-content-", dir=Path(__file__).parent)
        cls.seed = Path(cls.workspace.name) / "seed.db"
        cls.passwords = {name: secrets.token_urlsafe(24) for name in ("writer", "reviewer", "admin")}
        Store(cls.seed).bootstrap(cls.passwords, json.loads((ROOT / "fixtures.json").read_text()))

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.workspace.name)
        target = Path(self.temp.name) / "app.db"; shutil.copy2(self.seed, target)
        self.store = Store(target)
        self.writer, self.reader = {"id": "u_writer", "role": "employee"}, {"id": "u_reviewer", "role": "employee"}

    def tearDown(self):
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def problem(self, code, fn, *args):
        with self.assertRaises(Problem) as error: fn(*args)
        self.assertEqual(error.exception.code, code)

    def save(self, body=None):
        current = self.store.get_artifact(self.writer, "proposal")
        return self.store.save_artifact(self.writer, "proposal", {"title": current["title"], "base_revision": current["revision"], "content_json": body or sample(), "citations": current["citations"]}, self.key())

    def preview(self, operation="insert_after", target="p1"):
        return self.store.create_suggestion(self.writer, "proposal", {"base_revision": 2, "target_block_id": target, "source_id": "product", "section_index": 0, "operation": operation}, self.key())

    def test_save_restart_and_history_preserve_format_and_original_revision(self):
        original = self.store.get_artifact(self.writer, "proposal")["content_json"]
        self.save(); self.store = Store(self.store.path)
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["content_json"], rich_content.validate(sample()))
        self.assertEqual(self.store.get_artifact(self.writer, "proposal", 1)["content_json"], original)

    def test_old_client_cannot_downgrade_rich_revision(self):
        old = self.store.get_artifact(self.writer, "proposal")["content_json"]
        self.save()
        self.problem("FORMAT_DOWNGRADE", self.save, old)
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["revision"], 2)

    def test_invalid_structure_never_creates_a_revision(self):
        bad = sample(); bad["blocks"][5]["rows"][0][0]["html"] = "<script>bad</script>"
        self.problem("INVALID_CONTENT", self.save, bad)
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["revision"], 1)

    def test_checks_include_table_text_and_markup_spanning_unconfirmed_text(self):
        self.save()
        doc = self.store.get_artifact(self.writer, "proposal")
        self.assertIn("t1", [row["id"] for row in doc["checks"]["unconfirmed_blocks"]])

    def test_review_export_and_format_change_require_new_approval(self):
        self.save()
        review = self.store.submit_review(self.writer, "proposal", {"base_revision": 2, "reviewer_id": "u_reviewer"}, self.key())
        self.store.decide_review(self.reader, review["id"], {"revision": 2, "decision": "approved"}, self.key())
        export = self.store.create_export(self.writer, "proposal", {"revision": 2}, self.key())
        self.assertTrue(self.store.download_export(self.writer, export["id"])[0])
        body = sample(); body["blocks"][0]["level"] = 2
        self.save(body)
        self.problem("APPROVAL_REQUIRED", self.store.download_export, self.writer, export["id"])

    def test_excerpt_insertion_uses_rich_run_schema_and_keeps_table(self):
        self.save(); preview = self.preview(target="t1")
        self.store.apply_suggestion(self.writer, "proposal", preview["id"], {"base_revision": 2}, self.key())
        body = self.store.get_artifact(self.writer, "proposal")["content_json"]
        self.assertEqual(body["schema_version"], 2)
        self.assertEqual(body["blocks"][5]["type"], "table")
        self.assertEqual(body["blocks"][6]["runs"], [{"text": body["blocks"][6]["text"]}])

    def test_replacement_resets_only_target_inline_marks_with_explicit_flag(self):
        self.save(); preview = self.preview("replace")
        self.assertTrue(preview["target_format_reset"])
        self.store.apply_suggestion(self.writer, "proposal", preview["id"], {"base_revision": 2}, self.key())
        body = self.store.get_artifact(self.writer, "proposal")["content_json"]
        self.assertEqual(body["blocks"][3]["runs"], [{"text": body["blocks"][3]["text"]}])
        self.assertEqual(body["blocks"][5], rich_content.validate(sample())["blocks"][5])

    def test_table_and_heading_cannot_be_replaced_with_plain_excerpt(self):
        self.save()
        for target in ("t1", "h1"):
            self.problem("INVALID_TARGET", self.preview, "replace", target)

    def test_undo_restores_full_runs_table_and_heading_state(self):
        self.save(); preview = self.preview("replace")
        self.store.apply_suggestion(self.writer, "proposal", preview["id"], {"base_revision": 2}, self.key())
        self.store.undo_suggestion(self.writer, "proposal", preview["id"], {"base_revision": 3}, self.key())
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["content_json"], rich_content.validate(sample()))

    def test_shared_rich_body_does_not_gain_writes_export_or_history(self):
        self.save()
        self.store.share_artifact(self.writer, "proposal", {"base_revision": 2, "base_version": 1, "scope": "selected", "shared_user_ids": ["u_reviewer"], "confirm_body": True}, self.key())
        shared = self.store.get_artifact(self.reader, "proposal")
        self.assertEqual(shared["content_json"]["schema_version"], 2)
        self.assertFalse(shared["can_edit"] or shared["can_export"])
        self.assertEqual(shared["history"], [])

    def test_http_save_returns_normalized_structure_without_downgrading(self):
        app = Application(self.store)
        token, user = self.store.login("writer", self.passwords["writer"])
        headers = {"Host": app.host, "Origin": app.origin, "Content-Type": "application/json", "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key()}
        payload = {"title": "虚构格式稿", "base_revision": 1, "content_json": sample(), "citations": []}
        result = app.handle("POST", "/api/workbench/artifacts/proposal/revisions", headers, json.dumps(payload).encode())
        self.assertEqual(result.status, 200)
        result = app.handle("GET", "/api/workbench/artifacts/proposal", {"Host": app.host, "Cookie": app.cookie_name + "=" + token})
        self.assertEqual(json.loads(result.body)["data"]["content_json"], rich_content.validate(sample()))


if __name__ == "__main__":
    unittest.main()
