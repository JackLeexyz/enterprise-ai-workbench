from concurrent.futures import ThreadPoolExecutor
import copy
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
from store import Store, Problem


class WorkingCopyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="working-copy-", dir=Path(__file__).parent)
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

    def problem(self, code, fn, *args):
        with self.assertRaises(Problem) as error:
            fn(*args)
        self.assertEqual(error.exception.code, code)

    def payload(self, title="仅工作草稿可见的虚构标题"):
        doc = self.store.get_artifact(self.writer, "proposal")
        data = {key: copy.deepcopy(doc[key]) for key in ("content_json", "citations", "fields")}
        data.update(title=title, base_revision=doc["revision"], base_draft_version=doc["draft_version"])
        return data

    def save(self, payload=None, key=None):
        return self.store.save_working_copy(self.writer, "proposal", payload or self.payload(), key or self.key())

    def discard(self, number, key=None):
        return self.store.discard_working_copy(self.writer, "proposal", {"base_draft_version": number, "confirm": True}, key or self.key())

    def test_initial_copy_is_empty_and_does_not_create_row(self):
        data = self.store.get_working_copy(self.writer, "proposal")
        self.assertFalse(data["has_draft"]); self.assertEqual(data["draft_version"], 0)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM artifact_working_copies").fetchone()[0], 0)

    def test_autosave_retains_revision_status_and_history(self):
        before = self.store.get_artifact(self.writer, "proposal")
        self.save()
        after = self.store.get_artifact(self.writer, "proposal")
        for key in ("revision", "status", "title", "history", "content_json", "updated_at"):
            self.assertEqual(after[key], before[key])
        self.assertTrue(after["has_working_copy"])
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM revisions WHERE artifact_id='proposal'").fetchone()[0], 1)

    def test_copy_persists_across_store_restart_and_restores_reference_metadata(self):
        self.save()
        restored = Store(self.store.path).get_working_copy(self.writer, "proposal")
        self.assertEqual(restored["draft"]["title"], self.payload()["title"])
        self.assertEqual(restored["draft_version"], 1)
        self.assertTrue(all(item["available"] for item in restored["reference_details"]))

    def test_two_tabs_cannot_overwrite_same_draft_version(self):
        a, b = self.payload("页面甲"), self.payload("页面乙")
        self.save(a)
        self.problem("DRAFT_CONFLICT", self.save, b)
        self.assertEqual(self.store.get_working_copy(self.writer, "proposal")["draft"]["title"], "页面甲")

    def test_concurrent_draft_writers_only_one_wins(self):
        payload = self.payload()
        def write(_):
            try: self.save(payload); return "ok"
            except Problem as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(write, range(2))), ["ok", "DRAFT_CONFLICT"])

    def test_lost_response_retry_is_idempotent_and_ack_has_no_full_text(self):
        payload, key = self.payload(), self.key()
        first = self.save(payload, key)
        self.assertEqual(first, self.save(payload, key))
        self.assertNotIn("仅工作草稿", json.dumps(first, ensure_ascii=False))
        self.assertEqual(self.store.get_working_copy(self.writer, "proposal")["draft_version"], 1)

    def test_obsolete_save_replay_does_not_restore_discarded_copy(self):
        payload, key = self.payload(), self.key()
        self.save(payload, key); self.discard(1)
        self.problem("DRAFT_CONFLICT", self.save, payload, key)
        self.assertFalse(self.store.get_working_copy(self.writer, "proposal")["has_draft"])

    def test_discard_requires_confirmation_and_current_version(self):
        self.save()
        self.problem("CONFIRM_REQUIRED", self.store.discard_working_copy, self.writer, "proposal", {"base_draft_version": 1}, self.key())
        self.problem("DRAFT_CONFLICT", self.discard, 0)
        self.assertTrue(self.discard(1)["is_current"])
        self.problem("DRAFT_CONFLICT", self.save, {**self.payload(), "base_draft_version": 1})

    def test_discard_replay_does_not_delete_subsequent_copy(self):
        self.save(); key = self.key(); self.discard(1, key)
        self.save(self.payload("新的草稿"))
        self.assertFalse(self.discard(1, key)["is_current"])
        self.assertEqual(self.store.get_working_copy(self.writer, "proposal")["draft"]["title"], "新的草稿")

    def test_shared_reader_reviewer_and_admin_cannot_access_owner_copy(self):
        doc = self.store.get_artifact(self.writer, "proposal")
        self.store.share_artifact(self.writer, "proposal", {"scope": "internal", "shared_user_ids": [], "base_revision": 1, "base_version": doc["sharing"]["version"], "confirm_body": True}, self.key())
        self.save()
        for user in (self.reader, self.admin):
            self.problem("NOT_FOUND", self.store.get_working_copy, user, "proposal")
            self.problem("NOT_FOUND", self.store.save_working_copy, user, "proposal", self.payload(), self.key())
            self.problem("NOT_FOUND", self.store.discard_working_copy, user, "proposal", {"confirm": True, "base_draft_version": 1}, self.key())
            view = self.store.get_artifact(user, "proposal")
            self.assertNotIn("has_working_copy", view)
            self.assertNotIn("仅工作草稿", json.dumps(view, ensure_ascii=False))

    def test_pending_draft_blocks_legacy_save_review_and_suggestion(self):
        self.save(); data = self.payload(); del data["base_draft_version"]
        self.problem("DRAFT_PENDING", self.store.save_artifact, self.writer, "proposal", data, self.key())
        self.problem("DRAFT_PENDING", self.store.submit_review, self.writer, "proposal", {"base_revision": 1, "reviewer_id": "u_reviewer"}, self.key())
        doc = self.store.get_artifact(self.writer, "proposal")
        self.problem("DRAFT_PENDING", self.store.create_suggestion, self.writer, "proposal", {"base_revision": 1, "target_block_id": doc["content_json"]["blocks"][1]["id"], "source_id": "product", "section_index": 0}, self.key())

    def test_manual_revision_atomically_clears_copy_and_advances_both_versions(self):
        self.save(); payload = self.payload()
        result = self.store.save_artifact(self.writer, "proposal", payload, self.key())
        self.assertEqual(result["revision"], 2); self.assertEqual(result["draft_version"], 2)
        data = self.store.get_working_copy(self.writer, "proposal")
        self.assertFalse(data["has_draft"]); self.assertEqual(data["base_revision"], 2)
        self.problem("REVISION_CONFLICT", self.save, {**payload, "base_draft_version": 2})

    def test_manual_revision_refuses_newer_copy_from_other_tab(self):
        self.save(); payload = self.payload()
        self.save({**payload, "title": "其他页面更新"})
        self.problem("DRAFT_CONFLICT", self.store.save_artifact, self.writer, "proposal", payload, self.key())

    def test_invalid_versions_and_content_do_not_write(self):
        for invalid in (None, True, -1, "0"):
            self.problem("INVALID_DRAFT_VERSION", self.save, {**self.payload(), "base_draft_version": invalid})
        self.problem("INVALID_CONTENT", self.save, {**self.payload(), "content_json": {"schema_version": 1, "blocks": []}})
        self.assertFalse(self.store.get_working_copy(self.writer, "proposal")["has_draft"])

    def test_revoked_source_keeps_existing_copy_but_blocks_new_save(self):
        self.save()
        source = self.store.get_source(self.admin, "product")
        self.store.withdraw_source(self.admin, "product", {"base_version": source["version"]}, self.key())
        draft = self.store.get_working_copy(self.writer, "proposal")
        self.assertTrue(draft["has_draft"])
        self.assertTrue(any(not item["available"] for item in draft["reference_details"]))
        self.problem("SOURCE_UNAVAILABLE", self.save)

    def test_disabled_or_changed_role_invalidates_save_and_replay(self):
        payload, key = self.payload(), self.key(); self.save(payload, key)
        with self.store.connection(write=True) as db: db.execute("UPDATE users SET enabled=0 WHERE id='u_writer'")
        self.problem("SESSION_EXPIRED", self.save, payload, key)

    def test_rich_downgrade_is_rejected_by_draft_route(self):
        payload = self.payload()
        payload["content_json"] = json.loads((ROOT / "tests/rich_fixture.json").read_text())
        self.store.save_artifact(self.writer, "proposal", payload, self.key())
        self.problem("FORMAT_DOWNGRADE", self.save, {**self.payload(), "content_json": {"schema_version": 1, "blocks": [{"id": "p", "type": "paragraph", "text": "丢格式"}]}})

    def test_pending_structured_copy_cannot_be_flattened_before_its_first_revision(self):
        original = self.payload()
        self.save({**original, "content_json": json.loads((ROOT / "tests/rich_fixture.json").read_text())})
        downgraded = {**original, "base_draft_version": 1}
        self.problem("FORMAT_DOWNGRADE", self.save, downgraded)
        self.problem("FORMAT_DOWNGRADE", self.store.save_artifact, self.writer, "proposal", downgraded, self.key())
        self.assertEqual(self.store.get_working_copy(self.writer, "proposal")["draft"]["content_json"]["schema_version"], 2)

    def test_http_contract_uses_session_csrf_and_owner_only(self):
        app = Application(self.store); token, user = self.store.login("writer", self.passwords["writer"])
        headers = {"Host": app.host, "Cookie": app.cookie_name + "=" + token, "Origin": app.origin, "Content-Type": "application/json", "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key()}
        route = "/api/workbench/artifacts/proposal/draft"
        response = app.handle("POST", route, headers, json.dumps(self.payload()).encode())
        self.assertEqual(response.status, 200)
        response = app.handle("GET", route, headers, b"")
        self.assertTrue(json.loads(response.body)["data"]["has_draft"])
        bad = {**headers, "X-CSRF-Token": "wrong"}
        self.assertEqual(app.handle("POST", route, bad, b"{}").status, 403)

    def test_migration_adds_empty_table_without_changing_existing_revisions(self):
        before = self.store.get_artifact(self.writer, "proposal")
        with self.store.connection(write=True) as db: db.execute("DROP TABLE artifact_working_copies")
        migrated = Store(self.store.path)
        self.assertEqual(migrated.get_artifact(self.writer, "proposal"), before)
        self.assertFalse(migrated.get_working_copy(self.writer, "proposal")["has_draft"])

    def test_approved_revision_and_export_do_not_include_pending_copy(self):
        review = self.store.submit_review(self.writer, "proposal", {"base_revision": 1, "reviewer_id": "u_reviewer"}, self.key())
        self.store.decide_review(self.reader, review["id"], {"revision": 1, "decision": "approved"}, self.key())
        self.save()
        document = self.store.get_artifact(self.reader, "proposal")
        self.assertEqual(document["status"], "approved")
        self.assertNotIn("仅工作草稿", json.dumps(document, ensure_ascii=False))
        output = self.store.create_export(self.reader, "proposal", {"revision": 1}, self.key())
        word, title = self.store.download_export(self.reader, output["id"])
        self.assertNotIn("仅工作草稿", title)
        self.assertTrue(word.startswith(b"PK"))

    def test_concurrent_commit_and_draft_update_have_one_winner(self):
        self.save(); payload = self.payload()
        def operation(kind):
            try:
                method = self.store.save_artifact if kind == "commit" else self.store.save_working_copy
                method(self.writer, "proposal", payload, self.key()); return kind
            except Problem as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: results = list(pool.map(operation, ["commit", "copy"]))
        self.assertEqual(sum(item in {"commit", "copy"} for item in results), 1)
        self.assertTrue(any(item in {"DRAFT_CONFLICT", "REVISION_CONFLICT"} for item in results))
