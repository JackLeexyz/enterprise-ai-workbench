from concurrent.futures import ThreadPoolExecutor
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
from store import Problem, Store


class ArtifactSharingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="artifact-sharing-", dir=Path(__file__).parent)
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

    def payload(self, scope="selected", shares=None):
        doc = self.store.get_artifact(self.writer, "proposal")
        return {"base_version": doc["sharing"]["version"], "base_revision": doc["revision"], "scope": scope, "shared_user_ids": shares if shares is not None else (["u_reviewer"] if scope == "selected" else []), "confirm_body": scope != "private"}

    def share(self, scope="selected", shares=None):
        return self.store.share_artifact(self.writer, "proposal", self.payload(scope, shares), self.key())

    def save(self, title="新的虚构修订"):
        doc = self.store.get_artifact(self.writer, "proposal")
        return self.store.save_artifact(self.writer, "proposal", {"title": title, "base_revision": doc["revision"], "content_json": doc["content_json"], "citations": doc["citations"]}, self.key())

    def test_default_private_and_admin_has_no_override(self):
        doc = self.store.get_artifact(self.writer, "proposal")
        self.assertEqual(doc["sharing"], {"scope": "private", "shared_user_ids": [], "version": 1})
        self.assertTrue(doc["can_share"])
        for user in (self.reader, self.admin):
            self.problem("NOT_FOUND", self.store.get_artifact, user, "proposal")
            self.assertNotIn("proposal", [item["id"] for item in self.store.list_artifacts(user)])

    def test_selected_read_only_projection_hides_recipient_list(self):
        self.share()
        doc = self.store.get_artifact(self.reader, "proposal")
        self.assertEqual(doc["access"], "shared")
        self.assertFalse(any(doc[key] for key in ("can_edit", "can_share", "can_comment", "can_export")))
        self.assertNotIn("shared_user_ids", doc["sharing"])
        self.assertEqual(doc["history"], [])
        item = next(item for item in self.store.list_artifacts(self.reader) if item["id"] == "proposal")
        self.assertEqual(item["access"], "shared")
        self.assertNotIn("shares_json", item)
        self.problem("NOT_FOUND", self.store.get_artifact, self.admin, "proposal")

    def test_internal_includes_admin_only_as_a_regular_reader(self):
        self.share("internal")
        for user in (self.reader, self.admin):
            self.assertEqual(self.store.get_artifact(user, "proposal")["access"], "shared")
        self.share("private")
        for user in (self.reader, self.admin):
            self.problem("NOT_FOUND", self.store.get_artifact, user, "proposal")

    def test_revoke_removes_list_direct_and_explicit_revision_access(self):
        self.share()
        self.share("private")
        self.assertNotIn("proposal", [item["id"] for item in self.store.list_artifacts(self.reader)])
        self.problem("NOT_FOUND", self.store.get_artifact, self.reader, "proposal")
        self.problem("NOT_FOUND", self.store.get_artifact, self.reader, "proposal", 1)

    def test_sharing_follows_future_current_revision_but_not_history(self):
        self.share()
        self.save()
        doc = self.store.get_artifact(self.reader, "proposal")
        self.assertEqual((doc["revision"], doc["title"]), (2, "新的虚构修订"))
        self.assertEqual(doc["history"], [])
        self.problem("NOT_FOUND", self.store.get_artifact, self.reader, "proposal", 1)
        self.assertEqual(self.store.get_artifact(self.reader, "proposal", 2)["revision"], 2)

    def test_shared_reader_cannot_see_internal_comments_or_review_notes(self):
        self.store.comment(self.writer, "proposal", {"base_revision": 1, "text": "私有协作留言"}, self.key())
        review = self.store.submit_review(self.writer, "proposal", {"base_revision": 1, "reviewer_id": "u_admin"}, self.key())
        self.store.decide_review(self.admin, review["id"], {"revision": 1, "decision": "rejected", "note": "私有审核意见"}, self.key())
        self.share()
        doc = self.store.get_artifact(self.reader, "proposal")
        self.assertEqual((doc["reviews"], doc["comments"]), ([], []))
        self.assertNotIn("私有", json.dumps(doc, ensure_ascii=False))
        assigned = self.store.get_artifact(self.admin, "proposal")
        self.assertEqual(assigned["access"], "reviewer")
        self.assertTrue(assigned["can_comment"])
        self.assertTrue(assigned["reviews"])

    def test_shared_read_does_not_allow_comment_export_edit_or_review(self):
        self.share()
        doc = self.store.get_artifact(self.reader, "proposal")
        self.problem("READ_ONLY", self.store.comment, self.reader, "proposal", {"base_revision": 1, "text": "不能写"}, self.key())
        self.problem("READ_ONLY", self.store.save_artifact, self.reader, "proposal", {"base_revision": 1, "title": doc["title"], "content_json": doc["content_json"]}, self.key())
        self.problem("SELF_REVIEW_FORBIDDEN", self.store.submit_review, self.reader, "proposal", {"base_revision": 1, "reviewer_id": "u_admin"}, self.key())
        self.problem("READ_ONLY", self.store.create_export, self.reader, "proposal", {"revision": 1}, self.key())
        self.problem("OWNER_REQUIRED", self.store.share_artifact, self.reader, "proposal", self.payload("internal"), self.key())

    def test_shared_reader_cannot_export_even_after_approval(self):
        review = self.store.submit_review(self.writer, "proposal", {"base_revision": 1, "reviewer_id": "u_admin"}, self.key())
        self.store.decide_review(self.admin, review["id"], {"revision": 1, "decision": "approved"}, self.key())
        self.share()
        self.assertFalse(self.store.get_artifact(self.reader, "proposal")["can_export"])
        self.problem("READ_ONLY", self.store.create_export, self.reader, "proposal", {"revision": 1}, self.key())
        output = self.store.create_export(self.writer, "proposal", {"revision": 1}, self.key())
        self.problem("NOT_FOUND", self.store.download_export, self.reader, output["id"])
        self.assertTrue(self.store.download_export(self.writer, output["id"])[0])

    def test_shared_reader_cannot_get_or_execute_author_suggestions(self):
        doc = self.store.get_artifact(self.writer, "proposal")
        target = next(block["id"] for block in doc["content_json"]["blocks"] if block["type"] == "paragraph")
        payload = {"base_revision": 1, "target_block_id": target, "source_id": "product", "section_index": 0}
        preview = self.store.create_suggestion(self.writer, "proposal", payload, self.key())
        self.share()
        self.problem("READ_ONLY", self.store.create_suggestion, self.reader, "proposal", payload, self.key())
        self.problem("NOT_FOUND", self.store.get_suggestion, self.reader, "proposal", preview["id"])
        for handler in (self.store.apply_suggestion, self.store.cancel_suggestion, self.store.undo_suggestion):
            self.problem("NOT_FOUND", handler, self.reader, "proposal", preview["id"], {"base_revision": 1}, self.key())

    def test_sharing_survives_restart_without_changing_content_or_pending_review(self):
        review = self.store.submit_review(self.writer, "proposal", {"base_revision": 1, "reviewer_id": "u_admin"}, self.key())
        before = self.store.get_artifact(self.writer, "proposal")
        self.share()
        self.store = Store(self.store.path)
        current = self.store.get_artifact(self.writer, "proposal")
        for field in ("content_json", "citations", "revision", "status", "reviews", "history"):
            self.assertEqual(current[field], before[field])
        self.assertEqual(self.store.get_artifact(self.reader, "proposal")["access"], "shared")
        self.assertEqual(self.store.reviews(self.admin)[0]["id"], review["id"])

    def test_reviewer_assignment_is_independent_of_sharing(self):
        review = self.store.submit_review(self.writer, "proposal", {"base_revision": 1, "reviewer_id": "u_reviewer"}, self.key())
        self.share()
        self.share("private")
        self.assertEqual(self.store.get_artifact(self.reader, "proposal")["access"], "reviewer")
        self.store.decide_review(self.reader, review["id"], {"revision": 1, "decision": "rejected", "note": "需补充"}, self.key())
        self.save()
        self.problem("NOT_FOUND", self.store.get_artifact, self.reader, "proposal")
        self.assertEqual(self.store.get_artifact(self.reader, "proposal", 1)["revision"], 1)

    def test_old_review_grant_does_not_expose_history_in_shared_current_view(self):
        self.store.submit_review(self.writer, "proposal", {"base_revision": 1, "reviewer_id": "u_reviewer"}, self.key())
        self.save()
        self.share()
        doc = self.store.get_artifact(self.reader, "proposal")
        self.assertEqual((doc["access"], doc["history"], doc["reviews"], doc["comments"]), ("shared", [], [], []))
        self.assertEqual(self.store.get_artifact(self.reader, "proposal", 1)["access"], "reviewer")

    def test_sharing_never_grants_source_access_or_retrieval(self):
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET scope='private' WHERE id='product'")
        self.share()
        doc = self.store.get_artifact(self.reader, "proposal")
        ref = next(ref for ref in doc["reference_details"] if ref["source_id"] == "product")
        self.assertFalse(ref["available"])
        self.assertEqual(ref["title"], "无权访问的来源")
        self.problem("NOT_FOUND", self.store.get_source, self.reader, "product")
        with self.store.connection() as db:
            self.assertFalse(self.store.search(db, self.reader, "企业知识库", "product"))

    def test_withdrawn_source_leaves_shared_body_but_disables_reference(self):
        self.share()
        self.store.withdraw_source(self.admin, "product", {"base_version": 1}, self.key())
        doc = self.store.get_artifact(self.reader, "proposal")
        self.assertTrue(doc["content_json"]["blocks"])
        self.assertFalse(next(ref for ref in doc["reference_details"] if ref["source_id"] == "product")["available"])

    def test_only_owner_can_share_and_guessed_id_is_uniform(self):
        for user in (self.reader, self.admin):
            self.problem("NOT_FOUND", self.store.share_artifact, user, "proposal", self.payload(), self.key())
        self.problem("NOT_FOUND", self.store.share_artifact, self.writer, "missing", self.payload(), self.key())

    def test_sharing_version_and_content_revision_both_guard_confirmation(self):
        stale = self.payload()
        self.share("internal")
        self.problem("VERSION_CONFLICT", self.store.share_artifact, self.writer, "proposal", stale, self.key())
        stale = self.payload()
        self.save()
        self.problem("REVISION_CONFLICT", self.store.share_artifact, self.writer, "proposal", stale, self.key())

    def test_explicit_body_confirmation_is_required_except_revocation(self):
        for value in (None, False, "true", 1):
            self.problem("BODY_CONFIRMATION_REQUIRED", self.store.share_artifact, self.writer, "proposal", {**self.payload(), "confirm_body": value}, self.key())
        self.share()
        self.assertEqual(self.share("private")["sharing"]["scope"], "private")

    def test_invalid_scope_ids_extra_fields_and_bool_versions_are_rejected(self):
        for changes in ({"scope": "public"}, {"scope": []}, {"scope": {}}, {"shared_user_ids": []}, {"shared_user_ids": ["missing"]}, {"shared_user_ids": ["u_writer"]}, {"shared_user_ids": "u_reviewer"}):
            self.problem("INVALID_SCOPE", self.store.share_artifact, self.writer, "proposal", {**self.payload(), **changes}, self.key())
        self.problem("INVALID_INPUT", self.store.share_artifact, self.writer, "proposal", {**self.payload(), "can_edit": True}, self.key())
        for field in ("base_version", "base_revision"):
            self.problem("INVALID_INPUT", self.store.share_artifact, self.writer, "proposal", {**self.payload(), field: True}, self.key())

    def test_disabled_recipient_and_stale_disabled_user_are_rejected(self):
        self.share()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE users SET enabled=0 WHERE id='u_reviewer'")
        self.problem("INVALID_SCOPE", self.store.share_artifact, self.writer, "proposal", self.payload(), self.key())
        self.problem("SESSION_EXPIRED", self.store.get_artifact, self.reader, "proposal")
        self.problem("SESSION_EXPIRED", self.store.list_artifacts, self.reader)

    def test_idempotent_replay_returns_current_scope_without_regranting(self):
        payload, key = self.payload(), self.key()
        first = self.store.share_artifact(self.writer, "proposal", payload, key)
        self.assertTrue(first["sharing_result"]["is_current"])
        self.share("private")
        replay = self.store.share_artifact(self.writer, "proposal", payload, key)
        self.assertEqual(replay["sharing"]["scope"], "private")
        self.assertFalse(replay["sharing_result"]["is_current"])
        self.problem("NOT_FOUND", self.store.get_artifact, self.reader, "proposal")
        with self.store.connection() as db:
            cached = json.loads(db.execute("SELECT result_json FROM idempotency WHERE key=?", (key,)).fetchone()[0])
        self.assertEqual(set(cached), {"id", "applied_version"})

    def test_duplicate_request_and_conflicting_reuse_do_not_create_revisions(self):
        payload, key = self.payload(), self.key()
        self.store.share_artifact(self.writer, "proposal", payload, key)
        self.store.share_artifact(self.writer, "proposal", payload, key)
        self.problem("IDEMPOTENCY_MISMATCH", self.store.share_artifact, self.writer, "proposal", {**payload, "scope": "internal"}, key)
        doc = self.store.get_artifact(self.writer, "proposal")
        self.assertEqual((doc["revision"], doc["sharing"]["version"]), (1, 2))

    def test_concurrent_changes_use_one_acl_version(self):
        payload = self.payload()
        def change(scope):
            try:
                self.store.share_artifact(self.writer, "proposal", {**payload, "scope": scope, "shared_user_ids": []}, self.key())
                return "ok"
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(change, ("internal", "private")))
        self.assertCountEqual(outcomes, ["ok", "VERSION_CONFLICT"])

    def test_additive_table_upgrade_preserves_existing_content(self):
        with self.store.connection(write=True) as db:
            before = [tuple(row) for row in db.execute("SELECT * FROM artifacts ORDER BY id")]
            revisions = [tuple(row) for row in db.execute("SELECT * FROM revisions ORDER BY artifact_id,revision")]
            db.execute("DROP TABLE artifact_sharing")
        Store(self.store.path)
        Store(self.store.path)
        with self.store.connection() as db:
            self.assertEqual(before, [tuple(row) for row in db.execute("SELECT * FROM artifacts ORDER BY id")])
            self.assertEqual(revisions, [tuple(row) for row in db.execute("SELECT * FROM revisions ORDER BY artifact_id,revision")])
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["sharing"]["scope"], "private")

    def test_http_patch_rechecks_csrf_origin_and_session(self):
        app = Application(self.store)
        token, user = self.store.login("writer", self.passwords["writer"])
        headers = {"Host": app.host, "Origin": app.origin, "Content-Type": "application/json", "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key()}
        path, body = "/api/workbench/artifacts/proposal/sharing", json.dumps(self.payload()).encode()
        for change in ({"X-CSRF-Token": "wrong"}, {"Origin": "https://invalid.example"}, {"Cookie": ""}):
            response = app.handle("PATCH", path, {**headers, **change}, body)
            self.assertIn(response.status, (401, 403))
        response = app.handle("PATCH", path, headers, body)
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body)["data"]["sharing"]["scope"], "selected")
        self.assertIn("no-store", response.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()
