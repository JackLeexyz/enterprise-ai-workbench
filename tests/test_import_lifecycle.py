from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application import Application
from document_import import parse_document
from ingestion_worker import IngestionWorker
from store import Problem, Store


class LifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="lifecycle-tests-", dir=Path(__file__).parent)
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
        self.users = {user["username"]: user for user in self.store.users()}
        self.writer, self.reviewer, self.admin = (self.users[name] for name in ("writer", "reviewer", "admin"))
        self.worker = IngestionWorker(self.store)

    def tearDown(self):
        self.worker.stop()
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def problem(self, code, fn, *args):
        with self.assertRaises(Problem) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)

    def upload(self, content=b"test evidence", key=None):
        return self.store.enqueue_import(self.writer, "demo.txt", content, key or self.key())

    def preview(self):
        job = self.upload()
        self.worker.run_once()
        return self.store.get_import(self.writer, job["id"])

    def delete_payload(self, job):
        return {"confirm": True, "file_sha256": job["file_sha256"]}

    def commit(self, job):
        return self.store.commit_import(self.writer, job["id"], {"title": "导入资料"}, self.key())

    def test_cancel_queued_retains_bytes_and_releases_only_queue_slot(self):
        job = self.upload()
        before = self.store.import_usage(self.writer)
        canceled = self.store.cancel_import(self.writer, job["id"], {}, self.key())
        after = self.store.import_usage(self.writer)
        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(after["used_bytes"], before["used_bytes"])
        self.assertEqual(after["pending_count"], 0)
        self.assertFalse(self.worker.run_once())
        self.store.retry_import(self.writer, job["id"], {}, self.key())
        self.assertTrue(self.worker.run_once())

    def test_running_cancel_waits_for_worker_and_discards_late_success(self):
        job = self.upload()
        self.store.claim_import()
        self.assertEqual(self.store.cancel_import(self.writer, job["id"], {}, self.key())["status"], "canceling")
        self.assertEqual(self.store.import_usage(self.writer)["pending_count"], 1)
        self.assertIsNone(self.store.claim_import())
        self.store.finish_import(job["id"], parse_document("demo.txt", b"late result"))
        current = self.store.get_import(self.writer, job["id"])
        self.assertEqual((current["status"], current["sections"], current["characters"]), ("canceled", [], 0))
        self.problem("IMPORT_NOT_READY", self.store.commit_import, self.writer, job["id"], {"title": "不能恢复"}, self.key())

    def test_canceling_failure_and_restart_settle_as_canceled(self):
        job = self.upload()
        self.store.claim_import()
        self.store.cancel_import(self.writer, job["id"], {}, self.key())
        self.store.fail_import(job["id"], "TEST_ERROR", "test")
        self.assertEqual(self.store.get_import(self.writer, job["id"])["status"], "canceled")
        self.store.retry_import(self.writer, job["id"], {}, self.key())
        self.store.claim_import()
        self.store.cancel_import(self.writer, job["id"], {}, self.key())
        self.store.recover_imports()
        self.assertEqual(self.store.get_import(self.writer, job["id"])["status"], "canceled")

    def test_preview_cancel_clears_text_and_original_still_counts(self):
        job = self.preview()
        current = self.store.cancel_import(self.writer, job["id"], {}, self.key())
        self.assertEqual(current["sections"], [])
        self.assertGreater(self.store.import_usage(self.writer)["used_bytes"], 0)
        self.assertEqual(len(self.store.list_sources(self.writer)), 5)

    def test_cancel_replay_does_not_cancel_a_subsequent_resume(self):
        job, key = self.upload(), self.key()
        self.store.cancel_import(self.writer, job["id"], {}, key)
        self.store.retry_import(self.writer, job["id"], {}, self.key())
        replay = self.store.cancel_import(self.writer, job["id"], {}, key)
        self.assertEqual(replay["status"], "queued")
        self.assertEqual(replay["attempts"], 0)

    def test_resume_obeys_current_pending_limit(self):
        job = self.upload()
        self.store.cancel_import(self.writer, job["id"], {}, self.key())
        for number in range(5):
            self.upload(f"different {number}".encode())
        self.problem("IMPORT_QUOTA", self.store.retry_import, self.writer, job["id"], {}, self.key())
        self.assertEqual(self.store.import_usage(self.writer)["pending_count"], 5)

    def test_delete_reclaims_quota_and_replay_does_not_delete_twice(self):
        job = self.preview()
        key = self.key()
        one = self.store.delete_import(self.writer, job["id"], self.delete_payload(job), key)
        two = self.store.delete_import(self.writer, job["id"], self.delete_payload(job), key)
        self.assertEqual(one, two)
        self.assertEqual(one["released_bytes"], job["file_size"])
        self.assertEqual(self.store.import_usage(self.writer)["used_bytes"], 0)
        self.assertEqual(self.store.list_imports(self.writer), [])
        self.problem("NOT_FOUND", self.store.get_import, self.writer, job["id"])
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='import_deleted'").fetchone()[0], 1)

    def test_delete_requires_confirmation_and_matching_file(self):
        job = self.preview()
        self.problem("DELETE_CONFIRM_REQUIRED", self.store.delete_import, self.writer, job["id"], {"confirm": False}, self.key())
        self.problem("IMPORT_CHANGED", self.store.delete_import, self.writer, job["id"], {"confirm": True, "file_sha256": "0" * 64}, self.key())
        self.assertEqual(self.store.get_import(self.writer, job["id"])["status"], "preview")

    def test_other_accounts_including_admin_cannot_cancel_or_delete(self):
        job = self.preview()
        for user in (self.reviewer, self.admin):
            self.problem("NOT_FOUND", self.store.cancel_import, user, job["id"], {}, self.key())
            self.problem("NOT_FOUND", self.store.delete_import, user, job["id"], self.delete_payload(job), self.key())
            self.assertEqual(self.store.import_usage(user)["used_bytes"], 0)

    def test_running_and_canceling_cannot_be_deleted(self):
        job = self.upload()
        self.store.claim_import()
        self.problem("IMPORT_BUSY", self.store.delete_import, self.writer, job["id"], self.delete_payload(job), self.key())
        self.store.cancel_import(self.writer, job["id"], {}, self.key())
        self.problem("IMPORT_BUSY", self.store.delete_import, self.writer, job["id"], self.delete_payload(job), self.key())

    def test_committed_source_original_and_citations_are_protected(self):
        job = self.preview()
        sid = self.commit(job)["source_id"]
        for action, payload in [(self.store.cancel_import, {}), (self.store.delete_import, self.delete_payload(job))]:
            self.problem("IMPORT_COMMITTED", action, self.writer, job["id"], payload, self.key())
        request = self.store.request_publication(self.writer, sid, {"base_version": 1, "admin_id": self.admin["id"], "scope": "internal"}, self.key())
        self.store.decide_publication(self.admin, request["id"], {"decision": "approved"}, self.key())
        self.store.create_artifact(self.writer, {"title": "保留引用", "source_ids": [sid]}, self.key())
        self.problem("IMPORT_COMMITTED", self.store.delete_import, self.writer, job["id"], self.delete_payload(job), self.key())
        self.assertEqual(self.store.source_file(self.reviewer, sid)[0], b"test evidence")

    def test_reupload_after_deletion_gets_new_id_old_intent_cannot_restore(self):
        key = self.key()
        job = self.upload(key=key)
        self.store.delete_import(self.writer, job["id"], self.delete_payload(job), self.key())
        self.problem("IMPORT_REMOVED", self.upload, b"test evidence", key)
        fresh = self.upload()
        self.assertNotEqual(fresh["id"], job["id"])
        self.assertFalse(fresh["duplicate"])
        self.store.finish_import(job["id"], parse_document("demo.txt", b"old result"))
        self.assertEqual(self.store.get_import(self.writer, fresh["id"])["status"], "queued")

    def test_cancel_and_commit_are_atomic(self):
        job = self.preview()
        def cancel():
            try:
                return self.store.cancel_import(self.writer, job["id"], {}, self.key())["status"]
            except Problem as error:
                return error.code
        def commit():
            try:
                return self.commit(job)["source_id"]
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(cancel), pool.submit(commit)
            outcomes = a.result(), b.result()
        current = self.store.get_import(self.writer, job["id"])
        if current["status"] == "canceled":
            self.assertEqual(outcomes, ("canceled", "IMPORT_NOT_READY"))
            self.assertEqual(len(self.store.list_sources(self.writer)), 5)
        else:
            self.assertEqual(current["status"], "committed")
            self.assertEqual(outcomes[0], "IMPORT_COMMITTED")

    def test_delete_and_commit_do_not_orphan_a_source(self):
        job = self.preview()
        def attempt(fn):
            try:
                return fn()
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            delete = pool.submit(attempt, lambda: self.store.delete_import(self.writer, job["id"], self.delete_payload(job), self.key()))
            commit = pool.submit(attempt, lambda: self.commit(job))
            a, b = delete.result(), commit.result()
        if isinstance(a, dict):
            self.assertEqual(b, "NOT_FOUND")
            self.assertEqual(len(self.store.list_sources(self.writer)), 5)
        else:
            self.assertEqual(a, "IMPORT_COMMITTED")
            self.assertEqual(self.store.source_file(self.writer, b["source_id"])[0], b"test evidence")

    def test_worker_acknowledges_cancel_without_stopping_thread(self):
        job = self.upload()
        started, release = threading.Event(), threading.Event()
        def delayed(*args):
            started.set()
            if not release.wait(3):
                raise AssertionError("test worker timed out")
            return parse_document(*args)
        with patch("ingestion_worker.parse_document", side_effect=delayed):
            try:
                self.worker.start()
                self.assertTrue(started.wait(2))
                current = self.store.cancel_import(self.writer, job["id"], {}, self.key())
                self.assertEqual(current["status"], "canceling")
                release.set()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    current = self.store.get_import(self.writer, job["id"])
                    if current["status"] == "canceled":
                        break
                    time.sleep(0.02)
                self.assertEqual(current["status"], "canceled")
                self.assertTrue(self.worker.thread.is_alive())
            finally:
                release.set()
                self.worker.stop()

    def test_api_usage_cancel_delete_and_csrf(self):
        app = Application(self.store)
        token, user = self.store.login("writer", self.passwords["writer"])
        headers = {"Host": app.host, "Origin": app.origin, "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"], "Content-Type": "application/json", "Idempotency-Key": self.key()}
        job = self.upload()
        usage = app.handle("GET", "/api/workbench/imports/usage", headers)
        self.assertEqual(usage.status, 200)
        self.assertEqual(json.loads(usage.body)["data"]["used_bytes"], job["file_size"])
        self.assertEqual(app.handle("GET", "/api/workbench/imports/usage", {"Host": app.host}).status, 401)
        canceled = app.handle("POST", f'/api/workbench/imports/{job["id"]}/cancel', headers, b"{}")
        self.assertEqual(json.loads(canceled.body)["data"]["status"], "canceled")
        payload = json.dumps(self.delete_payload(job)).encode()
        route = f'/api/workbench/imports/{job["id"]}/delete'
        self.assertEqual(app.handle("POST", route, {**headers, "X-CSRF-Token": "bad"}, payload).status, 403)
        result = app.handle("POST", route, {**headers, "Idempotency-Key": self.key()}, payload)
        self.assertEqual(result.status, 200, result.body)
        self.assertEqual(self.store.import_usage(self.writer)["used_bytes"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
