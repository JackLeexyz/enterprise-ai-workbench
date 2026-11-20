from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import shutil
import tempfile
import unittest

from acceptance import seed_demo
from application import Application
from store import Store


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sandbox = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        cls.seed = Path(cls.sandbox.name) / "seed.db"
        cls.passwords = {name: secrets.token_urlsafe(24) for name in ("writer", "reviewer", "admin")}
        db = Store(cls.seed)
        db.bootstrap(cls.passwords, json.loads(Path("fixtures.json").read_text()))
        with db.connection(write=True) as conn:
            seed_demo(conn)

    @classmethod
    def tearDownClass(cls):
        cls.sandbox.cleanup()

    def setUp(self):
        case = tempfile.TemporaryDirectory(dir=self.sandbox.name)
        self.addCleanup(case.cleanup)
        path = Path(case.name) / "workbench.db"
        shutil.copy2(self.seed, path)
        self.store = Store(path)
        self.app = Application(self.store, acceptance_demo=True)
        self.sessions = {name: self.store.login(name, secret) for name, secret in self.passwords.items()}

    def req(self, method, path, body=None, who="writer", key=None, csrf=True):
        headers = {"Host": self.app.host}
        if who:
            token, user = self.sessions[who]
            headers["Cookie"] = self.app.cookie_name + "=" + token
            if csrf:
                headers["X-CSRF-Token"] = user["csrf"]
        data = b""
        if method == "POST":
            headers.update({"Origin": self.app.origin, "Content-Type": "application/json",
                            "Idempotency-Key": key or secrets.token_hex(16)})
            data = json.dumps(body or {}).encode()
        response = self.app.handle(method, "/api/workbench" + path, headers, data)
        value = json.loads(response.body)
        return response.status, value.get("data", value.get("error"))

    def preview(self, item="checklist"):
        status, preview = self.req("POST", f"/acceptance/projects/demo-a/items/{item}/preview", {"base_version": 1})
        self.assertEqual(status, 200)
        return preview["id"]

    def receive(self, item="checklist", key=None):
        return self.req("POST", f"/acceptance/projects/demo-a/items/{item}/received",
                        {"base_version": 1, "material_version": "v1.0"}, key=key)

    def test_feature_is_opt_in(self):
        self.app = Application(self.store, acceptance_demo=False)
        self.assertNotIn("acceptance_demo", self.req("GET", "/login-options", who=None)[1])
        self.assertEqual(self.req("GET", "/acceptance/projects")[0], 404)

    def test_access_scope_and_role_are_distinct(self):
        self.assertEqual(self.req("GET", "/acceptance/projects", who=None)[0], 401)
        self.assertEqual([p["id"] for p in self.req("GET", "/acceptance/projects")[1]], ["demo-a"])
        self.assertEqual([p["id"] for p in self.req("GET", "/acceptance/projects", who="admin")[1]], ["demo-b"])
        self.assertEqual(self.req("GET", "/acceptance/projects/demo-a", who="admin")[0], 404)
        self.assertEqual(self.req("POST", "/acceptance/projects/demo-a/items/checklist/preview", {"base_version": 1}, who="reviewer")[0], 403)
        pid = self.preview()
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pid}/confirm", who="reviewer")[0], 404)
        self.assertEqual(self.req("POST", f"/task-previews/{pid}/confirm")[0], 404)
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pid}/confirm", csrf=False)[0], 403)

    def test_receipt_changes_current_summary_and_does_not_complete_task(self):
        status, view = self.req("GET", "/acceptance/projects/demo-a")
        self.assertEqual(status, 200)
        self.assertEqual(view["summary"], {"missing": 2, "total": 4})
        self.assertEqual(view["evidence"]["source_id"], "delivery")
        pid = self.preview()
        self.assertEqual(self.req("GET", "/tasks")[1], [])
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pid}/confirm")[0], 200)
        self.assertEqual(self.receive()[1]["version"], 2)
        self.assertEqual(self.req("GET", "/acceptance/projects/demo-a")[1]["summary"]["missing"], 1)
        self.assertEqual(self.req("GET", "/tasks", who="reviewer")[1][0]["status"], "pending")

    def test_parallel_previews_confirm_only_one_task(self):
        pids = [self.preview(), self.preview()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pid: self.req("POST", f"/acceptance/previews/{pid}/confirm"), pids))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], 200)
        self.assertEqual(len(self.req("GET", "/tasks")[1]), 1)
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pids[0]}/confirm"), results[0])

    def test_stale_preview_stops_and_receipt_can_be_replayed(self):
        pid = self.preview()
        key = secrets.token_hex(16)
        self.assertEqual(self.receive(key=key), (200, {"version": 2}))
        self.assertEqual(self.receive(key=key), (200, {"version": 2}))
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pid}/confirm")[1]["code"], "ACCEPTANCE_CHANGED")
        self.assertEqual(self.req("GET", "/tasks")[1], [])

    def test_withdrawn_evidence_does_not_report_missing_or_allow_confirmation(self):
        pid = self.preview()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET publication='withdrawn' WHERE id='delivery'")
        view = self.req("GET", "/acceptance/projects/demo-a")[1]
        self.assertIsNone(view["summary"])
        self.assertIsNone(view["evidence"])
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pid}/confirm")[1]["code"], "ACCEPTANCE_EVIDENCE_UNAVAILABLE")

    def test_database_unavailable_is_an_error_not_a_missing_count(self):
        from unittest.mock import patch
        with patch.object(self.app.acceptance, "detail", side_effect=RuntimeError("unavailable")):
            status, data = self.req("GET", "/acceptance/projects/demo-a")
        self.assertEqual(status, 500)
        self.assertNotIn("summary", data)
