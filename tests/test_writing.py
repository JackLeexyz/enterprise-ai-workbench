from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application import Application
from store import Problem, Store, dump


class WritingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="writing-tests-", dir=Path(__file__).parent)
        cls.seed = Path(cls.workspace.name) / "seed.db"
        cls.passwords = {name: secrets.token_urlsafe(24) for name in ("writer", "reviewer", "admin")}
        Store(cls.seed).bootstrap(cls.passwords, json.loads((ROOT / "fixtures.json").read_text()))

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.workspace.name)
        target = Path(self.temp.name) / "app.db"
        shutil.copy2(self.seed, target)
        self.store = Store(target)
        users = {user["username"]: user for user in self.store.users()}
        self.writer, self.reviewer, self.admin = [users[name] for name in ("writer", "reviewer", "admin")]

    def tearDown(self):
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def problem(self, code, fn, *args, **kwargs):
        with self.assertRaises(Problem) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def template(self, key="solution"):
        return next(item for item in self.store.templates() if item["template_id"] == key)

    def definition(self, key="solution", **changes):
        item = self.template(key)
        return {"base_version": item["number"], "name": item["name"], "fields": item["fields"], "sections": item["sections"], "active": True, **changes}

    def create(self, fields=None):
        item = self.store.create_artifact(self.writer, {"title": "结构化方案", "template_version_id": self.template()["id"], "source_ids": ["product"], "fields": fields if fields is not None else {"subject": "内部演示工作台", "objective": "统一资料查询与文档交付"}}, self.key())
        return self.store.get_artifact(self.writer, item["id"])

    def save(self, doc, **changes):
        return self.store.save_artifact(self.writer, doc["id"], {"title": doc["title"], "base_revision": doc["revision"], "content_json": doc["content_json"], "citations": doc["citations"], "fields": doc.get("fields", {}), **changes}, self.key())

    def preview(self, doc=None, **changes):
        doc = doc or self.store.get_artifact(self.writer, "proposal")
        block = next(item for item in doc["content_json"]["blocks"] if item["type"] == "paragraph")
        return self.store.create_suggestion(self.writer, doc["id"], {"base_revision": doc["revision"], "target_block_id": block["id"], "source_id": "delivery", "section_index": 1, "operation": "insert_after", **changes}, self.key())

    def apply(self, suggestion, docid="proposal", key=None):
        return self.store.apply_suggestion(self.writer, docid, suggestion["id"], {"base_revision": suggestion["base_revision"]}, key or self.key())

    def test_default_templates_are_versioned_and_do_not_modify_legacy_documents(self):
        self.assertEqual({item["template_id"] for item in self.store.templates()}, {"solution", "delivery"})
        doc = self.store.get_artifact(self.writer, "proposal")
        self.assertIsNone(doc["template_binding"])
        self.assertEqual(doc["fields"], {})
        self.assertTrue(doc["checks"]["legacy_template"])
        self.assertEqual(doc["revision"], 1)

    def test_template_change_is_new_immutable_version(self):
        old = self.template()
        result = self.store.update_template(self.admin, "solution", self.definition(name="内部产品方案"), self.key())
        self.assertEqual(result["number"], 2)
        self.assertEqual(self.store.template_version(old["id"])["name"], old["name"])
        self.problem("ADMIN_REQUIRED", self.store.update_template, self.writer, "solution", self.definition(), self.key())

    def test_template_update_conflict_and_idempotency(self):
        body, key = self.definition(), self.key()
        first = self.store.update_template(self.admin, "solution", body, key)
        self.assertEqual(first, self.store.update_template(self.admin, "solution", body, key))
        self.problem("TEMPLATE_CONFLICT", self.store.update_template, self.admin, "solution", body, self.key())

    def test_concurrent_template_edits_cannot_overwrite(self):
        body = self.definition()
        def update(_):
            try: return self.store.update_template(self.admin, "solution", body, self.key())["number"]
            except Problem as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, range(2)))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(results.count("TEMPLATE_CONFLICT"), 1)

    def test_template_rejects_invalid_keys_duplicate_or_unknown_section_fields(self):
        for fields, sections in [([], []), ([{"key": "bad.key", "label": "错误", "required": True, "multiline": False}], [{"key": "s", "title": "章节", "field_keys": ["bad.key"]}]), ([{"key": "a", "label": "字段", "required": True, "multiline": False}] * 2, [{"key": "s", "title": "章节", "field_keys": ["a"]}]), ([{"key": "a", "label": "字段", "required": "true", "multiline": False}], [{"key": "s", "title": "章节", "field_keys": ["a"]}]), ([{"key": "a", "label": "字段", "required": True, "multiline": False}], [{"key": "s", "title": "章节", "field_keys": ["unknown"]}])]:
            self.problem("INVALID_TEMPLATE", self.store.update_template, self.admin, "solution", self.definition(fields=fields, sections=sections), self.key())

    def test_new_document_is_bound_to_exact_template_not_later_head(self):
        doc = self.create()
        old = doc["template_binding"]
        self.store.update_template(self.admin, "solution", self.definition(name="另一个名称"), self.key())
        fresh = self.store.get_artifact(self.writer, doc["id"])
        self.assertEqual(fresh["template_binding"]["id"], old["id"])
        self.assertEqual(fresh["template_binding"]["name"], old["name"])
        self.assertEqual(fresh["content_json"], doc["content_json"])

    def test_stale_or_inactive_template_cannot_create_new_draft(self):
        old = self.template()
        self.store.update_template(self.admin, "solution", self.definition(active=False), self.key())
        inactive = next(item for item in self.store.templates(include_inactive=True) if item["template_id"] == "solution")
        for tid in (old["id"], inactive["id"]):
            self.problem("TEMPLATE_UNAVAILABLE", self.store.create_artifact, self.writer, {"title": "新方案", "template_version_id": tid, "source_ids": ["product"]}, self.key())

    def test_missing_fields_remain_unconfirmed_and_block_review_not_draft_save(self):
        doc = self.create(fields={"subject": "演示对象"})
        self.assertIn("objective", [item["key"] for item in doc["checks"]["missing_fields"]])
        self.assertIn("[待确认]", dump(doc["content_json"]))
        self.assertNotIn("100万元", dump(doc["content_json"]))
        self.problem("REQUIRED_FIELDS", self.store.submit_review, self.writer, doc["id"], {"base_revision": 1, "reviewer_id": self.reviewer["id"]}, self.key())
        self.save(doc, fields={"subject": "演示对象", "objective": "核对模板缺项"})
        current = self.store.get_artifact(self.writer, doc["id"])
        self.assertEqual(current["checks"]["missing_fields"], [])
        self.assertEqual(self.store.get_artifact(self.writer, doc["id"], 1)["fields"], {"subject": "演示对象"})
        self.assertTrue(current["checks"]["can_submit_review"])

    def test_fields_reject_unknown_nontext_or_oversized_values(self):
        for fields in ({"unregistered": "text"}, {"subject": []}, {"subject": "x" * 1001}, ["not", "object"]):
            self.problem("INVALID_FIELDS", self.create, fields)

    def test_omitted_fields_on_old_client_save_preserve_existing_values(self):
        doc = self.create()
        payload = {"title": doc["title"], "base_revision": 1, "content_json": doc["content_json"], "citations": doc["citations"]}
        self.store.save_artifact(self.writer, doc["id"], payload, self.key())
        self.assertEqual(self.store.get_artifact(self.writer, doc["id"])["fields"], doc["fields"])

    def test_preview_does_not_change_document_or_add_citation(self):
        before = self.store.get_artifact(self.writer, "proposal")
        suggestion = self.preview()
        after = self.store.get_artifact(self.writer, "proposal")
        self.assertEqual(suggestion["status"], "prepared")
        self.assertEqual(suggestion["mode"], "offline_excerpt")
        self.assertIn("未完成项", suggestion["excerpt"])
        self.assertEqual((before["revision"], before["content_json"], before["citations"]), (after["revision"], after["content_json"], after["citations"]))

    def test_apply_is_atomic_idempotent_and_binds_new_revision(self):
        suggestion = self.preview()
        key = self.key()
        first = self.apply(suggestion, key=key)
        self.assertEqual(first, self.apply(suggestion, key=key))
        self.assertEqual(self.apply(suggestion)["revision"], 2)
        doc = self.store.get_artifact(self.writer, "proposal")
        self.assertEqual(doc["revision"], 2)
        self.assertEqual(sum("未完成项" in block["text"] for block in doc["content_json"]["blocks"]), 1)
        self.assertEqual(doc["undo_suggestion"]["id"], suggestion["id"])

    def test_replacement_uses_explicit_operation(self):
        before = self.store.get_artifact(self.writer, "proposal")
        suggestion = self.preview(operation="replace")
        self.apply(suggestion)
        after = self.store.get_artifact(self.writer, "proposal")
        self.assertEqual(len(after["content_json"]["blocks"]), len(before["content_json"]["blocks"]))
        target = next(block for block in after["content_json"]["blocks"] if block["id"] == suggestion["target_block_id"])
        self.assertIn("未完成项", target["text"])

    def test_changed_revision_supersedes_preview(self):
        suggestion = self.preview()
        doc = self.store.get_artifact(self.writer, "proposal")
        self.save(doc)
        self.problem("SUGGESTION_CHANGED", self.apply, suggestion)

    def test_target_fingerprint_detects_mutation_without_revision_increment(self):
        suggestion = self.preview()
        with self.store.connection(write=True) as db:
            row = db.execute("SELECT content_json FROM revisions WHERE artifact_id='proposal' AND revision=1").fetchone()
            content = json.loads(row[0])
            next(block for block in content["blocks"] if block["id"] == suggestion["target_block_id"])["text"] = "测试模拟异常改写"
            db.execute("UPDATE revisions SET content_json=? WHERE artifact_id='proposal' AND revision=1", (dump(content),))
        self.problem("SUGGESTION_CHANGED", self.apply, suggestion)

    def test_withdrawal_blocks_get_replay_and_apply(self):
        doc = self.store.get_artifact(self.writer, "proposal")
        block = next(item for item in doc["content_json"]["blocks"] if item["type"] == "paragraph")
        body = {"base_revision": 1, "target_block_id": block["id"], "source_id": "delivery", "section_index": 1, "operation": "insert_after"}
        key = self.key()
        suggestion = self.store.create_suggestion(self.writer, "proposal", body, key)
        source = self.store.get_source(self.admin, "delivery")
        self.store.withdraw_source(self.admin, "delivery", {"base_version": source["version"]}, self.key())
        self.problem("NOT_FOUND", self.store.get_suggestion, self.writer, "proposal", suggestion["id"])
        self.problem("NOT_FOUND", self.store.create_suggestion, self.writer, "proposal", body, key)
        self.problem("NOT_FOUND", self.apply, suggestion)

    def test_changed_source_fingerprint_blocks_apply(self):
        suggestion = self.preview()
        with self.store.connection(write=True) as db:
            sections = json.loads(db.execute("SELECT sections_json FROM sources WHERE id='delivery'").fetchone()[0])
            sections[1]["text"] += "版本内异常改动"
            db.execute("UPDATE sources SET sections_json=? WHERE id='delivery'", (dump(sections),))
        self.problem("SUGGESTION_CHANGED", self.apply, suggestion)

    def test_cancel_and_expire_do_not_write_document(self):
        suggestion = self.preview()
        self.store.cancel_suggestion(self.writer, "proposal", suggestion["id"], {}, self.key())
        self.problem("SUGGESTION_CANCELED", self.apply, suggestion)
        expired = self.preview()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE writing_suggestions SET expires=? WHERE id=?", (time.time() - 1, expired["id"]))
        self.problem("SUGGESTION_EXPIRED", self.apply, expired)
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["revision"], 1)

    def test_other_people_cannot_read_apply_cancel_suggestion(self):
        suggestion = self.preview()
        for user in (self.admin, self.reviewer):
            self.problem("NOT_FOUND", self.store.get_suggestion, user, "proposal", suggestion["id"])
            self.problem("NOT_FOUND", self.store.apply_suggestion, user, "proposal", suggestion["id"], {"base_revision": 1}, self.key())
            self.problem("NOT_FOUND", self.store.cancel_suggestion, user, "proposal", suggestion["id"], {}, self.key())

    def test_invalid_preview_target_operation_and_section_are_rejected(self):
        for extra in ({"target_block_id": "missing"}, {"operation": "execute"}, {"section_index": -1}, {"section_index": True}, {"section_index": 999}):
            self.problem("INVALID_TARGET", self.preview, None, **extra)

    def test_undo_creates_revision_without_deleting_history(self):
        before = self.store.get_artifact(self.writer, "proposal")
        suggestion = self.preview()
        self.apply(suggestion)
        result = self.store.undo_suggestion(self.writer, "proposal", suggestion["id"], {"base_revision": 2}, self.key())
        self.assertEqual(result["revision"], 3)
        doc = self.store.get_artifact(self.writer, "proposal")
        self.assertEqual(doc["content_json"], before["content_json"])
        self.assertEqual(doc["citations"], before["citations"])
        self.assertIsNone(doc["undo_suggestion"])
        self.assertEqual(len(doc["history"]), 3)

    def test_undo_cannot_overwrite_later_manual_save(self):
        suggestion = self.preview()
        self.apply(suggestion)
        self.save(self.store.get_artifact(self.writer, "proposal"))
        self.problem("SUGGESTION_CHANGED", self.store.undo_suggestion, self.writer, "proposal", suggestion["id"], {"base_revision": 3}, self.key())

    def test_simultaneous_apply_creates_only_one_revision(self):
        suggestion = self.preview()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.apply(suggestion), range(2)))
        self.assertEqual([item["revision"] for item in results], [2, 2])
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["revision"], 2)

    def test_apply_to_approved_document_requires_new_review_for_export(self):
        review = self.store.submit_review(self.writer, "proposal", {"base_revision": 1, "reviewer_id": self.reviewer["id"]}, self.key())
        self.store.decide_review(self.reviewer, review["id"], {"revision": 1, "decision": "approved"}, self.key())
        self.apply(self.preview())
        doc = self.store.get_artifact(self.writer, "proposal")
        self.assertEqual(doc["status"], "draft")
        self.problem("APPROVAL_REQUIRED", self.store.create_export, self.writer, "proposal", {"revision": 2}, self.key())

    def test_full_document_apply_fails_without_partial_revision(self):
        doc = self.store.get_artifact(self.writer, "proposal")
        content = {"schema_version": 1, "blocks": [{"id": "b" + str(i), "type": "paragraph", "text": "文档中的已知文本"} for i in range(100)]}
        self.save(doc, content_json=content)
        doc = self.store.get_artifact(self.writer, "proposal")
        suggestion = self.preview(doc)
        self.problem("INVALID_CONTENT", self.apply, suggestion)
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["revision"], 2)
        self.assertEqual(self.store.get_suggestion(self.writer, "proposal", suggestion["id"])["status"], "prepared")

    def test_private_unpublished_source_cannot_enter_preview(self):
        source = self.store.create_source(self.writer, {"title": "未发布", "text": "不应进入引用"}, self.key())
        self.problem("NOT_FOUND", self.preview, None, source_id=source["id"], section_index=0)

    def test_undo_revalidates_original_references(self):
        suggestion = self.preview()
        self.apply(suggestion)
        source = self.store.get_source(self.admin, "delivery")
        self.store.withdraw_source(self.admin, "delivery", {"base_version": source["version"]}, self.key())
        self.problem("SOURCE_UNAVAILABLE", self.store.undo_suggestion, self.writer, "proposal", suggestion["id"], {"base_revision": 2}, self.key())

    def test_old_database_seeding_preserves_legacy_revisions(self):
        with self.store.connection(write=True) as db:
            original = [tuple(row) for row in db.execute("SELECT * FROM revisions ORDER BY artifact_id")]
            for name in ("writing_suggestions", "revision_fields", "artifact_templates", "template_versions", "templates"):
                db.execute("DROP TABLE " + name)
        Store(self.store.path)
        with self.store.connection() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM revisions ORDER BY artifact_id")], original)
        self.assertIsNone(self.store.get_artifact(self.writer, "proposal")["template_binding"])

    def test_new_template_export_keeps_fields_and_unconfirmed_values(self):
        doc = self.create()
        review = self.store.submit_review(self.writer, doc["id"], {"base_revision": 1, "reviewer_id": self.reviewer["id"]}, self.key())
        self.store.decide_review(self.reviewer, review["id"], {"revision": 1, "decision": "approved"}, self.key())
        export = self.store.create_export(self.writer, doc["id"], {"revision": 1}, self.key())
        content, _ = self.store.download_export(self.writer, export["id"])
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
            rendered = "".join(root.itertext())
        for expected in ("内部演示工作台", "统一资料查询与文档交付", "预算：[待确认]", "实施日期：[待确认]"):
            self.assertIn(expected, rendered)

    def test_twenty_references_still_allow_another_quote_from_existing_source(self):
        with self.store.connection(write=True) as db:
            seed = db.execute("SELECT * FROM sources WHERE id='delivery'").fetchone()
            for index in range(16):
                sid = f"extra{index}"
                db.execute("INSERT INTO sources(id,version_id,title,category,label,owner_id,scope,publication,sections_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (sid, "ver_" + sid, "虚构资料" + str(index), seed["category"], "v1", seed["owner_id"], "internal", "published", seed["sections_json"], seed["created_at"], seed["updated_at"]))
            refs = [{"source_id": row["id"], "version_id": row["version_id"]} for row in db.execute("SELECT id,version_id FROM sources WHERE publication='published'")]
        self.assertEqual(len(refs), 20)
        self.save(self.store.get_artifact(self.writer, "proposal"), citations=refs)
        doc = self.store.get_artifact(self.writer, "proposal")
        self.apply(self.preview(doc))
        self.assertEqual(len(self.store.get_artifact(self.writer, "proposal")["citations"]), 20)

    def test_fields_only_draft_does_not_invent_citations(self):
        created = self.store.create_artifact(self.writer, {"title": "手工信息起稿", "template_version_id": self.template()["id"], "fields": {"subject": "演示企业", "objective": "整理业务需求"}, "source_ids": []}, self.key())
        doc = self.store.get_artifact(self.writer, created["id"])
        self.assertEqual(doc["citations"], [])
        self.assertNotIn("参考资料", dump(doc["content_json"]))
        self.assertIn("[待确认]", dump(doc["content_json"]))
        self.assertEqual(doc["checks"]["missing_fields"], [])

    def test_api_template_fields_checks_and_suggestion_routes(self):
        app = Application(self.store)
        token, session = self.store.login("writer", self.passwords["writer"])
        def request(method, route, body=None):
            headers = {"Host": app.host, "Origin": app.origin, "Content-Type": "application/json", "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": session["csrf"], "Idempotency-Key": self.key()}
            response = app.handle(method, "/api/workbench" + route, headers, json.dumps(body or {}).encode() if method == "POST" else b"")
            return response.status, json.loads(response.body)
        self.assertEqual(request("GET", "/templates")[0], 200)
        self.assertEqual(request("GET", "/templates/versions/" + self.template()["id"])[0], 200)
        self.assertEqual(request("GET", "/admin/templates")[0], 403)
        self.assertEqual(request("GET", "/artifacts/proposal/checks")[0], 200)
        suggestion = self.preview()
        self.assertEqual(request("GET", "/artifacts/proposal/suggestions/" + suggestion["id"])[0], 200)
        self.assertEqual(request("POST", "/artifacts/proposal/suggestions/" + suggestion["id"] + "/apply", {"base_revision": 1})[0], 200)


if __name__ == "__main__":
    unittest.main()
