import hashlib
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application import Application
from document_import import parse_document
from store import Problem, Store


class SourceVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="source-versions-", dir=Path(__file__).parent)
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
        self.writer, self.reader, self.admin = ({"id": "u_" + name, "role": "admin" if name == "admin" else "employee"} for name in ("writer", "reviewer", "admin"))

    def tearDown(self):
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def problem(self, code, fn, *args, **kwargs):
        with self.assertRaises(Problem) as error:
            fn(*args, **kwargs)
        self.assertEqual(error.exception.code, code)

    def create(self):
        return self.store.create_source(self.writer, {"title": "虚构验收说明", "category_id": "cat_other", "label": "v1", "text": "验收材料必须包含旧版清单。"}, self.key())

    def publish(self, source, scope="internal", shares=None):
        request = self.store.request_publication(self.writer, source["id"], {"base_version": source["version"], "admin_id": "u_admin", "scope": scope, "shared_user_ids": shares or []}, self.key())
        self.store.decide_publication(self.admin, request["id"], {"decision": "approved"}, self.key())
        return self.store.get_source(self.writer, source["id"])

    def new_version(self, source, **changes):
        payload = {"base_version": source["family_version"], "title": "虚构验收说明", "category_id": "cat_other", "label": "v2", "text": "验收材料必须包含新版培训确认。", **changes}
        return self.store.add_source_version(self.writer, source["id"], payload, self.key())

    def share(self, source, scope, shares=None, key=None):
        return self.store.share_source(self.writer, source["id"], {"base_version": source["family_version"], "scope": scope, "shared_user_ids": shares or []}, key or self.key())

    def test_new_version_is_private_and_old_identity_is_immutable(self):
        old = self.publish(self.create())
        new = self.new_version(old)
        self.assertNotEqual(old["id"], new["id"])
        self.assertNotEqual(old["version_id"], new["version_id"])
        self.assertEqual((new["scope"], new["publication"], new["version_number"]), ("private", "draft", 2))
        self.assertEqual(old["family_id"], new["family_id"])
        self.problem("NOT_FOUND", self.store.get_source, self.reader, new["id"])
        preserved = self.store.get_source(self.writer, old["id"])
        self.assertEqual(preserved["sections"], old["sections"])
        self.assertTrue(preserved["is_current_published"])
        self.assertEqual([item["id"] for item in self.store.list_sources(self.writer) if item["family_id"] == old["family_id"]], [new["id"]])

    def test_publishing_switches_search_but_not_old_answer_and_reference(self):
        old = self.publish(self.create())
        answer = self.store.ask(self.reader, {"question": "验收材料清单"}, self.key())
        old_match = next(item for item in answer["matches"] if item["source_id"] == old["id"])
        new = self.publish(self.new_version(old))
        self.assertTrue(new["is_current_published"])
        self.assertFalse(self.store.get_source(self.writer, old["id"])["is_current_published"])
        with self.store.connection() as db:
            matches = self.store.search(db, self.reader, "验收材料清单")
            self.assertNotIn(old["id"], [item["source_id"] for item in matches])
            self.assertIn(new["id"], [item["source_id"] for item in matches])
            self.assertEqual(self.store.references(db, self.reader, [{"source_id": old["id"], "version_id": old["version_id"]}])[0]["source_id"], old["id"])
        evidence = self.store.get_evidence(self.reader, answer["id"], old_match["evidence_id"])
        self.assertIn("旧版清单", evidence["section"]["text"])
        self.assertTrue(evidence["historical"])
        self.problem("NOT_FOUND", self.store.create_artifact, self.reader, {"title": "不能新选历史资料", "source_ids": [old["id"]]}, self.key())

    def test_private_draft_is_not_enumerated_in_reader_history(self):
        old = self.publish(self.create())
        new = self.new_version(old, title="绝不能泄漏的草稿标题")
        view = self.store.get_source(self.reader, old["id"])
        self.assertNotIn(new["id"], [item["id"] for item in view["versions"]])
        self.assertNotIn("绝不能泄漏", json.dumps(view, ensure_ascii=False))
        self.problem("NOT_FOUND", self.store.get_source, self.admin, new["id"])

    def test_share_draft_without_publishing_and_revoke(self):
        source = self.create()
        shared = self.share(source, "selected", ["u_reviewer"])
        self.assertEqual(self.store.get_source(self.reader, source["id"])["publication"], "draft")
        with self.store.connection() as db:
            self.assertFalse(self.store.search(db, self.reader, "旧版清单", source["id"]))
        self.problem("OWNER_REQUIRED", self.store.share_source, self.reader, source["id"], {"base_version": shared["family_version"], "scope": "internal"}, self.key())
        self.share(shared, "private")
        self.problem("NOT_FOUND", self.store.get_source, self.reader, source["id"])

    def test_shrink_published_access_applies_to_history_and_cached_evidence(self):
        old = self.publish(self.create())
        answer = self.store.ask(self.reader, {"question": "旧版清单"}, self.key())
        match = next(item for item in answer["matches"] if item["source_id"] == old["id"])
        new = self.publish(self.new_version(old))
        result = self.share(new, "private")
        self.assertEqual(result["scope"], "private")
        for source in (old, new):
            self.problem("NOT_FOUND", self.store.get_source, self.reader, source["id"])
        self.problem("NOT_FOUND", self.store.get_evidence, self.reader, answer["id"], match["evidence_id"])
        self.assertNotIn("旧版清单", json.dumps([item["matches"] for item in self.store.messages(self.reader)], ensure_ascii=False))

    def test_scope_change_does_not_share_unreleased_drafts(self):
        old = self.publish(self.create(), "private")
        new = self.new_version(old)
        old = self.store.get_source(self.writer, old["id"])
        self.share(old, "internal")
        self.store.get_source(self.reader, old["id"])
        self.problem("NOT_FOUND", self.store.get_source, self.reader, new["id"])

    def test_publish_scope_shrinks_old_versions_and_revalidates_old_file(self):
        raw = "验收材料必须包含旧版清单。".encode()
        job = self.store.enqueue_import(self.writer, "虚构材料.txt", raw, self.key())
        self.store.claim_import()
        self.store.finish_import(job["id"], parse_document("虚构材料.txt", raw))
        sid = self.store.commit_import(self.writer, job["id"], {"title": "虚构验收说明", "category_id": "cat_other"}, self.key())["source_id"]
        old = self.publish(self.store.get_source(self.writer, sid))
        self.assertEqual(self.store.source_file(self.reader, sid)[0], raw)
        new = self.publish(self.new_version(old), "private")
        self.problem("NOT_FOUND", self.store.source_file, self.reader, sid)
        self.assertEqual(new["scope"], "private")

    def test_stale_new_version_and_non_latest_parent_are_rejected(self):
        old = self.create()
        new = self.new_version(old)
        self.problem("VERSION_CONFLICT", self.new_version, old, text="并发草稿")
        latest_old = self.store.get_source(self.writer, old["id"])
        self.problem("LATEST_VERSION_REQUIRED", self.new_version, latest_old, text="从旧版本误建")
        self.assertEqual(len(self.store.get_source(self.writer, new["id"])["versions"]), 2)

    def test_share_conflict_and_invalid_scope_have_no_side_effect(self):
        source = self.create()
        self.problem("INVALID_SCOPE", self.share, source, "selected", [])
        self.problem("INVALID_SCOPE", self.share, source, "selected", ["missing_user"])
        shared = self.share(source, "selected", ["u_reviewer"])
        self.problem("VERSION_CONFLICT", self.share, source, "internal")
        self.assertEqual(self.store.get_source(self.writer, source["id"])["scope"], shared["scope"])

    def test_sharing_supersedes_pending_review_and_revokes_admin_grant(self):
        source = self.create()
        pending = self.store.request_publication(self.writer, source["id"], {"base_version": source["version"], "admin_id": "u_admin", "scope": "internal"}, self.key())
        self.store.get_source(self.admin, source["id"])
        self.share(source, "private")
        self.assertFalse(self.store.admin_publications(self.admin))
        self.problem("NOT_FOUND", self.store.get_source, self.admin, source["id"])
        self.problem("NOT_FOUND", self.store.decide_publication, self.admin, pending["id"], {"decision": "approved"}, self.key())

    def test_new_version_supersedes_old_pending_request(self):
        source = self.create()
        self.store.request_publication(self.writer, source["id"], {"base_version": source["version"], "admin_id": "u_admin", "scope": "internal"}, self.key())
        self.new_version(source)
        self.assertFalse(self.store.admin_publications(self.admin))
        self.problem("NOT_FOUND", self.store.get_source, self.admin, source["id"])

    def test_withdraw_current_does_not_fall_back_to_old_search(self):
        old = self.publish(self.create())
        new = self.publish(self.new_version(old))
        self.store.withdraw_source(self.admin, new["id"], {"base_version": new["version"]}, self.key())
        with self.store.connection() as db:
            self.assertFalse([item for item in self.store.search(db, self.reader, "验收材料") if item["source_id"] in (old["id"], new["id"])])
        self.assertFalse(self.store.get_source(self.writer, old["id"])["is_current_published"])

    def test_import_new_version_deduplicates_and_does_not_rebind_files(self):
        old = self.create()
        raw = b"Fictional acceptance evidence for the next version."
        job = self.store.enqueue_import(self.writer, "new.txt", raw, self.key())
        self.store.claim_import()
        self.store.finish_import(job["id"], parse_document("new.txt", raw))
        payload = {"base_version": old["family_version"], "import_id": job["id"], "title": old["title"], "category_id": old["category_id"], "label": "v2"}
        key = self.key()
        new = self.store.add_source_version(self.writer, old["id"], payload, key)
        replay = self.store.add_source_version(self.writer, old["id"], payload, key)
        self.assertEqual(new["id"], replay["id"])
        self.assertEqual(self.store.get_import(self.writer, job["id"])["source_id"], new["id"])
        other = self.create()
        self.problem("IMPORT_COMMITTED", self.store.add_source_version, self.writer, other["id"], {**payload, "base_version": other["family_version"]}, self.key())
        self.assertEqual(hashlib.sha256(self.store.source_file(self.writer, new["id"])[0]).hexdigest(), job["file_sha256"])

    def test_new_version_and_sharing_replay_are_safe(self):
        old = self.create()
        key = self.key()
        payload = {"base_version": old["family_version"], "scope": "internal"}
        shared = self.store.share_source(self.writer, old["id"], payload, key)
        self.share(shared, "private")
        replay = self.store.share_source(self.writer, old["id"], payload, key)
        self.assertEqual(replay["scope"], "private")

    def test_http_version_and_sharing_contracts(self):
        app = Application(self.store)
        token, user = self.store.login("writer", self.passwords["writer"])
        source = self.create()
        headers = {"Host": app.host, "Origin": app.origin, "Content-Type": "application/json", "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key()}
        response = app.handle("PATCH", "/api/workbench/sources/" + source["id"] + "/sharing", headers, json.dumps({"base_version": source["family_version"], "scope": "selected", "shared_user_ids": ["u_reviewer"]}).encode())
        self.assertEqual(response.status, 200)
        shared = json.loads(response.body)["data"]
        headers["Idempotency-Key"] = self.key()
        response = app.handle("POST", "/api/workbench/sources/" + source["id"] + "/versions", headers, json.dumps({"base_version": shared["family_version"], "title": source["title"], "category_id": "cat_other", "label": "v2", "text": "Different fictional content."}).encode())
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body)["data"]["version_number"], 2)

    def test_singleton_migration_is_idempotent_and_preserves_source_bytes(self):
        with self.store.connection(write=True) as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM sources ORDER BY id")]
            db.execute("DELETE FROM source_versions")
            db.execute("DELETE FROM source_families")
        Store(self.store.path)
        Store(self.store.path)
        with self.store.connection() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM sources ORDER BY id")])
            self.assertEqual(len(before), db.execute("SELECT count(*) FROM source_versions").fetchone()[0])
            self.assertEqual(len(before), db.execute("SELECT count(*) FROM source_families").fetchone()[0])

    def test_new_version_owner_and_strict_concurrency_are_required(self):
        source = self.publish(self.create())
        payload = {"base_version": source["family_version"], "title": "虚构", "category_id": "cat_other", "text": "test"}
        self.problem("OWNER_REQUIRED", self.store.add_source_version, self.reader, source["id"], payload, self.key())
        self.problem("INVALID_INPUT", self.store.add_source_version, self.writer, source["id"], {**payload, "base_version": True}, self.key())
        self.problem("INVALID_INPUT", self.store.share_source, self.writer, source["id"], {"base_version": str(source["family_version"]), "scope": "internal"}, self.key())

    def test_existing_export_download_is_blocked_after_chain_scope_shrinks(self):
        old = self.publish(self.create())
        doc = self.store.create_artifact(self.reader, {"title": "虚构参考稿", "source_ids": [old["id"]], "fields": {"subject": "虚构项目", "objective": "验收核对"}}, self.key())
        review = self.store.submit_review(self.reader, doc["id"], {"base_revision": 1, "reviewer_id": "u_writer"}, self.key())
        self.store.decide_review(self.writer, review["id"], {"revision": 1, "decision": "approved"}, self.key())
        export = self.store.create_export(self.reader, doc["id"], {"revision": 1}, self.key())
        self.assertTrue(self.store.download_export(self.reader, export["id"])[0])
        new = self.publish(self.new_version(old))
        self.share(new, "private")
        self.problem("SOURCE_UNAVAILABLE", self.store.download_export, self.reader, export["id"])
        self.assertFalse(self.store.get_artifact(self.reader, doc["id"])["reference_details"][0]["available"])

    def test_prepared_excerpt_cannot_apply_after_new_publication(self):
        old = self.publish(self.create())
        doc = self.store.get_artifact(self.writer, "proposal")
        target = next(block for block in doc["content_json"]["blocks"] if block["type"] == "paragraph")
        preview = self.store.create_suggestion(self.writer, "proposal", {"base_revision": 1, "target_block_id": target["id"], "source_id": old["id"], "section_index": 0}, self.key())
        self.publish(self.new_version(old))
        self.problem("NOT_FOUND", self.store.apply_suggestion, self.writer, "proposal", preview["id"], {"base_revision": 1}, self.key())
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["revision"], 1)

    def test_current_selector_remains_available_under_private_head(self):
        old = self.publish(self.create())
        self.new_version(old)
        selected = next(item for item in self.store.list_sources(self.writer) if item["family_id"] == old["family_id"])
        self.assertEqual(selected["publication"], "draft")
        self.assertEqual(selected["published_source"]["id"], old["id"])
        self.assertTrue(selected["published_source"]["is_current_published"])

    def test_disabled_share_recipient_is_rechecked_at_publication(self):
        source = self.create()
        request = self.store.request_publication(self.writer, source["id"], {"base_version": source["version"], "admin_id": "u_admin", "scope": "selected", "shared_user_ids": ["u_reviewer"]}, self.key())
        with self.store.connection(write=True) as db:
            db.execute("UPDATE users SET enabled=0 WHERE id='u_reviewer'")
        self.problem("INVALID_SCOPE", self.store.decide_publication, self.admin, request["id"], {"decision": "approved"}, self.key())
        self.assertEqual(self.store.get_source(self.writer, source["id"])["publication"], "draft")

    def test_duplicate_file_cannot_be_used_to_link_another_owners_source(self):
        source = self.store.get_source(self.writer, "product")
        raw = b"Fictional imported evidence"
        job = self.store.enqueue_import(self.writer, "fictional.txt", raw, self.key())
        self.store.claim_import()
        self.store.finish_import(job["id"], parse_document("fictional.txt", raw))
        self.problem("OWNER_REQUIRED", self.store.add_source_version, self.writer, source["id"], {"base_version": source["family_version"], "import_id": job["id"], "title": "虚构", "category_id": "cat_other"}, self.key())
        self.assertEqual(self.store.get_import(self.writer, job["id"])["status"], "preview")

    def test_upload_target_survives_store_restart_and_list_readback(self):
        source = self.create()
        job = self.store.enqueue_import(self.writer, "next.txt", b"Fictional next version", self.key(), source["id"], source["family_version"])
        self.store = Store(self.store.path)
        for saved in (self.store.get_import(self.writer, job["id"]), self.store.list_imports(self.writer)[0]):
            self.assertEqual(saved["parent_source_id"], source["id"])
            self.assertEqual(saved["base_family_version"], source["family_version"])
        self.problem("NOT_FOUND", self.store.get_import, self.reader, job["id"])

    def test_upload_target_requires_current_owned_head_before_retaining_file(self):
        source = self.publish(self.create())
        self.problem("OWNER_REQUIRED", self.store.enqueue_import, self.reader, "next.txt", b"New fictional version", self.key(), source["id"], source["family_version"])
        self.problem("VERSION_CONFLICT", self.store.enqueue_import, self.writer, "next.txt", b"New fictional version", self.key(), source["id"], source["family_version"] + 1)
        self.problem("INVALID_INPUT", self.store.enqueue_import, self.writer, "next.txt", b"New fictional version", self.key(), None, 1)
        self.assertFalse(self.store.list_imports(self.writer))
        self.assertFalse(self.store.list_imports(self.reader))

    def test_duplicate_upload_retains_first_target_not_the_new_intent(self):
        first, second = self.create(), self.create()
        raw = b"One fictional original only"
        key = self.key()
        job = self.store.enqueue_import(self.writer, "next.txt", raw, key, first["id"], first["family_version"])
        duplicate = self.store.enqueue_import(self.writer, "next.txt", raw, self.key(), second["id"], second["family_version"])
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(job["id"], duplicate["id"])
        self.assertEqual(duplicate["parent_source_id"], first["id"])
        self.problem("IDEMPOTENCY_MISMATCH", self.store.enqueue_import, self.writer, "next.txt", raw, key, second["id"], second["family_version"])
        self.assertEqual(len(self.store.list_imports(self.writer)), 1)

    def test_delete_uncommitted_file_cascades_only_its_target(self):
        source = self.create()
        job = self.store.enqueue_import(self.writer, "next.txt", b"Fictional cancel/delete", self.key(), source["id"], source["family_version"])
        self.store.cancel_import(self.writer, job["id"], {}, self.key())
        self.assertEqual(self.store.get_import(self.writer, job["id"])["parent_source_id"], source["id"])
        self.store.delete_import(self.writer, job["id"], {"confirm": True, "file_sha256": job["file_sha256"]}, self.key())
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM import_targets WHERE job_id=?", (job["id"],)).fetchone()[0], 0)
        self.store.get_source(self.writer, source["id"])

    def test_frozen_upload_target_staleness_blocks_commit_until_explicit_reconfirmation(self):
        source = self.create()
        raw = b"Fictional new material for explicit reconfirmation"
        job = self.store.enqueue_import(self.writer, "next.txt", raw, self.key(), source["id"], source["family_version"])
        self.share(source, "selected", ["u_reviewer"])
        self.store.claim_import()
        self.store.finish_import(job["id"], parse_document("next.txt", raw))
        payload = {"title": "虚构确认", "category_id": "cat_other", "import_id": job["id"], "base_version": job["base_family_version"]}
        self.problem("VERSION_CONFLICT", self.store.add_source_version, self.writer, source["id"], payload, self.key())
        self.assertEqual(self.store.get_import(self.writer, job["id"])["status"], "preview")
        current = self.store.get_source(self.writer, source["id"])
        created = self.store.add_source_version(self.writer, source["id"], {**payload, "base_version": current["family_version"]}, self.key())
        self.assertEqual(created["scope"], "private")
        self.assertEqual(self.store.get_import(self.writer, job["id"])["source_id"], created["id"])

    def test_target_headers_are_paired_bounded_and_authenticated(self):
        source = self.create()
        app = Application(self.store)
        token, user = self.store.login("writer", self.passwords["writer"])
        boundary = "source_target_test_boundary"
        raw = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="fictional.txt"\r\n\r\nFictional text\r\n--{boundary}--\r\n').encode()
        headers = {"Host": app.host, "Origin": app.origin, "Content-Type": "multipart/form-data; boundary=" + boundary, "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key(), "X-Source-Parent": source["id"]}
        for extra in ({}, {"X-Source-Version": "NaN"}, {"X-Source-Version": "0001"}, {"X-Source-Version": "9999999999"}):
            response = app.handle("POST", "/api/workbench/imports", {**headers, **extra}, raw)
            self.assertEqual(response.status, 422)
        response = app.handle("POST", "/api/workbench/imports", {**headers, "X-Source-Version": str(source["family_version"])}, raw)
        self.assertEqual(response.status, 202)
        self.assertEqual(json.loads(response.body)["data"]["parent_source_id"], source["id"])


if __name__ == "__main__":
    unittest.main()
