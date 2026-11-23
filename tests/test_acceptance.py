from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import secrets
import shutil
import sqlite3
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

    def test_review_submit_decide_and_stale_after_change(self):
        endpoint = "/acceptance/projects/demo-a/review/"
        self.assertEqual(self.req("POST", endpoint + "submit", {"base_version": 1})[0], 409)
        self.assertEqual(self.receive("checklist")[0], 200)
        self.assertEqual(self.req("POST", "/acceptance/projects/demo-a/items/training/received", {"base_version": 2, "material_version": "v1"})[0], 200)
        self.assertEqual(self.req("POST", endpoint + "submit", {"base_version": 3}, who="reviewer")[0], 403)
        key = secrets.token_hex(16)
        submitted = self.req("POST", endpoint + "submit", {"base_version": 3}, key=key)
        self.assertEqual(submitted[0], 200)
        self.assertEqual(self.req("GET", endpoint + "report")[0], 409)
        self.assertEqual(self.req("POST", endpoint + "submit", {"base_version": 3}, key=key), submitted)
        self.assertEqual(self.req("POST", endpoint + "submit", {"base_version": 3})[0], 409)
        body = {"review_id": submitted[1]["id"], "decision": "approved"}
        self.assertEqual(self.req("POST", endpoint + "decide", body)[0], 403)
        self.assertEqual(self.req("POST", endpoint + "decide", body, who="reviewer")[0], 200)
        status, report = self.req("GET", endpoint + "report", who="reviewer")
        self.assertEqual(status, 200)
        self.assertEqual(report["review_id"], submitted[1]["id"])
        self.assertEqual(len(report["materials"]), 4)
        self.assertEqual(self.req("GET", endpoint + "report", who="admin")[0], 404)
        with self.store.connection(write=True) as db:
            original = db.execute("SELECT manifest_json FROM acceptance_reviews WHERE id=?", (submitted[1]["id"],)).fetchone()[0]
            db.execute("UPDATE acceptance_reviews SET manifest_json='[]' WHERE id=?", (submitted[1]["id"],))
        self.assertEqual(self.req("GET", endpoint + "report")[0], 409)
        with self.store.connection(write=True) as db:
            db.execute("UPDATE acceptance_reviews SET manifest_json=? WHERE id=?", (original, submitted[1]["id"]))
        self.assertFalse(self.req("GET", "/acceptance/projects/demo-a")[1]["review"]["stale"])
        self.assertEqual(self.req("POST", "/acceptance/projects/demo-a/items/training/relink", {"base_version": 3, "material_source_id": "delivery"})[0], 200)
        review = self.req("GET", "/acceptance/projects/demo-a")[1]["review"]
        self.assertEqual(review["status"], "approved")
        self.assertTrue(review["stale"])
        self.assertEqual(self.req("GET", endpoint + "report")[0], 409)
        history = self.req("GET", "/acceptance/projects/demo-a")[1]["review_history"]
        self.assertEqual(history[0]["status"], "approved")
        self.assertEqual(len(history[0]["materials"]), 4)
        self.assertTrue(all(set(item) == {"name", "version", "linked"} for item in history[0]["materials"]))
        self.assertEqual(self.req("GET", "/acceptance/projects/demo-a", who="admin")[0], 404)

    def test_review_reject_resubmit_and_withdrawn_evidence(self):
        endpoint = "/acceptance/projects/demo-a/review/"
        self.receive("checklist")
        self.req("POST", "/acceptance/projects/demo-a/items/training/received", {"base_version": 2, "material_version": "v1"})
        rid = self.req("POST", endpoint + "submit", {"base_version": 3})[1]["id"]
        body = {"review_id": rid, "decision": "rejected", "comment": ""}
        self.assertEqual(self.req("POST", endpoint + "decide", body, who="reviewer")[0], 422)
        self.assertEqual(self.req("POST", endpoint + "decide", {**body, "comment": "补充材料说明"}, who="reviewer")[0], 200)
        rid2 = self.req("POST", endpoint + "submit", {"base_version": 3})[1]["id"]
        self.assertNotEqual(rid, rid2)
        history = self.req("GET", "/acceptance/projects/demo-a", who="reviewer")[1]["review_history"]
        self.assertEqual([item["status"] for item in history], ["pending", "rejected"])
        self.assertEqual(history[1]["comment"], "补充材料说明")
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET publication='withdrawn' WHERE id='delivery'")
        self.assertTrue(self.req("GET", "/acceptance/projects/demo-a")[1]["review"]["stale"])
        self.assertEqual(self.req("POST", endpoint + "decide", {"review_id": rid2, "decision": "approved"}, who="reviewer")[0], 409)

    def test_review_requires_distinct_reviewer_and_detects_silent_evidence_change(self):
        self.assertEqual(self.req("POST", "/acceptance/projects/demo-b/review/submit", {"base_version": 1}, who="admin")[0], 409)
        self.receive("checklist")
        self.req("POST", "/acceptance/projects/demo-a/items/training/received", {"base_version": 2, "material_version": "v1"})
        endpoint = "/acceptance/projects/demo-a/review/"
        rid = self.req("POST", endpoint + "submit", {"base_version": 3})[1]["id"]
        with self.store.connection(write=True) as db:
            sections = json.loads(db.execute("SELECT sections_json FROM sources WHERE id='delivery'").fetchone()[0])
            sections[0]["text"] += " 变更测试"
            db.execute("UPDATE sources SET sections_json=? WHERE id='delivery'", (json.dumps(sections),))
        self.assertTrue(self.req("GET", "/acceptance/projects/demo-a", who="reviewer")[1]["review"]["stale"])
        response = self.req("POST", endpoint + "decide", {"review_id": rid, "decision": "approved"}, who="reviewer")
        self.assertEqual(response[0], 409)
        self.assertEqual(response[1]["code"], "ACCEPTANCE_REVIEW_STALE")

    def test_review_decision_idempotency_and_material_permission(self):
        self.receive("checklist")
        self.req("POST", "/acceptance/projects/demo-a/items/training/received", {"base_version": 2, "material_version": "v1", "material_source_id": "product"})
        endpoint = "/acceptance/projects/demo-a/review/"
        rid = self.req("POST", endpoint + "submit", {"base_version": 3})[1]["id"]
        key = secrets.token_hex(16)
        body = {"review_id": rid, "decision": "rejected", "comment": "请补充说明"}
        first = self.req("POST", endpoint + "decide", body, who="reviewer", key=key)
        self.assertEqual(first[0], 200)
        self.assertEqual(self.req("POST", endpoint + "decide", body, who="reviewer", key=key), first)
        self.assertEqual(self.req("POST", endpoint + "decide", body, who="reviewer")[0], 409)
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET scope='selected',shares_json=? WHERE id='product'", (json.dumps(["u_writer"]),))
        self.assertTrue(self.req("GET", "/acceptance/projects/demo-a")[1]["review"]["stale"])
        self.assertEqual(self.req("POST", endpoint + "submit", {"base_version": 3})[0], 409)

    def test_create_project_with_materials_is_idempotent_and_scoped(self):
        body = {"name": "虚构交付", "due_date": "2026-12-30", "reviewer_id": "u_reviewer", "source_id": "delivery", "items": ["实施说明", "验收清单"]}
        key = secrets.token_hex(16)
        first = self.req("POST", "/acceptance/projects", body, key=key)
        self.assertEqual(first[0], 200)
        self.assertEqual(self.req("POST", "/acceptance/projects", body, key=key), first)
        pid = first[1]["id"]
        self.assertEqual(len(self.req("GET", "/acceptance/projects/" + pid)[1]["items"]), 2)
        self.assertEqual(self.req("GET", "/acceptance/projects/" + pid, who="admin")[0], 404)
        self.assertEqual(self.req("POST", "/acceptance/projects", {**body, "items": ["同名", "同名"]})[0], 422)
        self.assertEqual(self.req("POST", "/acceptance/projects", {**body, "source_id": "weekly"})[0], 404)
        self.assertEqual(self.req("POST", "/acceptance/projects", {**body, "due_date": "2026-02-30"})[0], 422)

    def test_legacy_database_migration_preserves_existing_records(self):
        path = Path(self.sandbox.name) / (secrets.token_hex(8) + ".db")
        self.addCleanup(path.unlink, missing_ok=True)
        old_schema = Path("schema.sql").read_text().replace(
            "task_id TEXT REFERENCES tasks(id), evidence_fingerprint TEXT, previous_task_id TEXT,",
            "task_id TEXT REFERENCES tasks(id),")
        old_schema = old_schema.replace("  material_source_id TEXT REFERENCES sources(id), material_source_version_id TEXT,\n", "")
        old_schema = old_schema.replace("  manifest_json TEXT\n", "  legacy_placeholder TEXT\n")
        self.assertNotEqual(old_schema, Path("schema.sql").read_text())
        with sqlite3.connect(path) as db:
            db.executescript(old_schema)
            db.execute("INSERT INTO categories(id,name,updated_at) VALUES('legacy-test','旧分类','2026-11-20')")
        Store(path)
        Store(path)
        with sqlite3.connect(path) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(acceptance_previews)")}
            self.assertIn("evidence_fingerprint", columns)
            self.assertIn("previous_task_id", columns)
            item_columns = {row[1] for row in db.execute("PRAGMA table_info(acceptance_items)")}
            self.assertTrue({"material_source_id", "material_source_version_id"}.issubset(item_columns))
            self.assertIn("manifest_json", {row[1] for row in db.execute("PRAGMA table_info(acceptance_reviews)")})
            self.assertEqual(db.execute("SELECT name FROM categories WHERE id='legacy-test'").fetchone()[0], "旧分类")

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

    def test_receipt_reference_is_authorized_and_rechecked(self):
        route = "/acceptance/projects/demo-a/items/checklist/received"
        body = {"base_version": 1, "material_version": "v1", "material_source_id": "weekly"}
        self.assertEqual(self.req("POST", route, body)[0], 404)
        body["material_source_id"] = "delivery"
        self.assertEqual(self.req("POST", route, body)[0], 200)
        view = self.req("GET", "/acceptance/projects/demo-a", who="reviewer")[1]
        item = next(i for i in view["items"] if i["item_id"] == "checklist")
        self.assertEqual(item["material_reference"]["state"], "available")
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET publication='withdrawn' WHERE id='delivery'")
        view = self.req("GET", "/acceptance/projects/demo-a", who="reviewer")[1]
        item = next(i for i in view["items"] if i["item_id"] == "checklist")
        self.assertEqual(item["material_reference"], {"state": "unavailable"})
        self.assertEqual(item["material_version"], "v1")

    def test_receipt_reference_rechecks_each_viewer_after_permission_change(self):
        route = "/acceptance/projects/demo-a/items/checklist/received"
        body = {"base_version": 1, "material_version": "v1", "material_source_id": "delivery"}
        self.assertEqual(self.req("POST", route, body)[0], 200)
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET scope='selected',shares_json=? WHERE id='delivery'", (json.dumps(["u_writer"]),))
        owner = self.req("GET", "/acceptance/projects/demo-a")[1]
        reviewer = self.req("GET", "/acceptance/projects/demo-a", who="reviewer")[1]
        a = next(i for i in owner["items"] if i["item_id"] == "checklist")
        b = next(i for i in reviewer["items"] if i["item_id"] == "checklist")
        self.assertEqual(a["material_reference"]["state"], "available")
        self.assertEqual(b["material_reference"], {"state": "unavailable"})
        self.assertNotIn("title", b["material_reference"])

    def test_owner_relinks_unavailable_reference_with_version_guard(self):
        route = "/acceptance/projects/demo-a/items/checklist"
        self.assertEqual(self.req("POST", route + "/received", {"base_version": 1, "material_version": "v1", "material_source_id": "delivery"})[0], 200)
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET publication='withdrawn' WHERE id='delivery'")
        body = {"base_version": 2, "material_source_id": "product"}
        self.assertEqual(self.req("POST", route + "/relink", body, who="reviewer")[0], 403)
        self.assertEqual(self.req("POST", route + "/relink", {**body, "material_source_id": "weekly"})[0], 404)
        key = secrets.token_hex(16)
        self.assertEqual(self.req("POST", route + "/relink", body, key=key), (200, {"version": 3}))
        self.assertEqual(self.req("POST", route + "/relink", body, key=key), (200, {"version": 3}))
        self.assertEqual(self.req("POST", route + "/relink", body)[1]["code"], "ACCEPTANCE_CHANGED")
        view = self.req("GET", "/acceptance/projects/demo-a", who="reviewer")[1]
        item = next(i for i in view["items"] if i["item_id"] == "checklist")
        self.assertEqual(item["material_reference"]["source_id"], "product")
        self.assertEqual(item["material_version"], "v1")

    def test_assignee_completes_task_then_owner_records_receipt(self):
        pid = self.preview()
        tid = self.req("POST", f"/acceptance/previews/{pid}/confirm")[1]["id"]
        status, result = self.req("POST", f"/tasks/{tid}/status", {"base_version": 1, "status": "completed"}, who="writer")
        self.assertEqual((status, result["code"]), (403, "TASK_ROLE_REQUIRED"))
        status, result = self.req("POST", f"/tasks/{tid}/status", {"base_version": 1, "status": "completed"}, who="reviewer")
        self.assertEqual((status, result["status"]), (200, "completed"))
        view = self.req("GET", "/acceptance/projects/demo-a")[1]
        item = next(i for i in view["items"] if i["item_id"] == "checklist")
        self.assertEqual(item["task_status"], "completed")
        self.assertIsNone(item["material_version"])
        self.assertEqual(view["summary"]["missing"], 2)
        self.assertEqual(self.receive()[0], 200)
        view = self.req("GET", "/acceptance/projects/demo-a")[1]
        item = next(i for i in view["items"] if i["item_id"] == "checklist")
        self.assertEqual((item["task_status"], item["material_version"]), ("completed", "v1.0"))
        self.assertEqual(view["summary"]["missing"], 1)

    def test_parallel_previews_confirm_only_one_task(self):
        pids = [self.preview(), self.preview()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pid: self.req("POST", f"/acceptance/previews/{pid}/confirm"), pids))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], 200)
        self.assertEqual(len(self.req("GET", "/tasks")[1]), 1)
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pids[0]}/confirm"), results[0])

    def test_canceled_task_can_be_reissued_without_duplicate(self):
        old_preview = self.preview()
        old_id = self.req("POST", f"/acceptance/previews/{old_preview}/confirm")[1]["id"]
        self.assertEqual(self.req("POST", f"/tasks/{old_id}/status", {"base_version": 1, "status": "canceled"})[0], 200)
        pids = [self.preview(), self.preview()]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda pid: self.req("POST", f"/acceptance/previews/{pid}/confirm"), pids))
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0][0], 200)
        self.assertNotEqual(results[0][1]["id"], old_id)
        tasks = self.req("GET", "/tasks")[1]
        self.assertEqual(sorted(t["status"] for t in tasks), ["canceled", "pending"])
        self.assertEqual(self.req("POST", f"/acceptance/previews/{old_preview}/confirm")[1]["id"], old_id)

    def test_unconfirmed_old_preview_cannot_reissue_canceled_task(self):
        first, stale = self.preview(), self.preview()
        task_id = self.req("POST", f"/acceptance/previews/{first}/confirm")[1]["id"]
        self.assertEqual(self.req("POST", f"/tasks/{task_id}/status", {"base_version": 1, "status": "canceled"})[0], 200)
        status, result = self.req("POST", f"/acceptance/previews/{stale}/confirm")
        self.assertEqual((status, result["code"]), (409, "ACCEPTANCE_CHANGED"))
        self.assertEqual(len(self.req("GET", "/tasks")[1]), 1)

    def test_completed_task_can_be_reissued_if_material_still_missing(self):
        first, stale = self.preview(), self.preview()
        old_id = self.req("POST", f"/acceptance/previews/{first}/confirm")[1]["id"]
        self.assertEqual(self.req("POST", f"/tasks/{old_id}/status", {"base_version": 1, "status": "completed"}, who="reviewer")[0], 200)
        self.assertEqual(self.req("POST", f"/acceptance/previews/{stale}/confirm")[1]["code"], "ACCEPTANCE_CHANGED")
        fresh = self.preview()
        new_id = self.req("POST", f"/acceptance/previews/{fresh}/confirm")[1]["id"]
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(sorted(t["status"] for t in self.req("GET", "/tasks")[1]), ["completed", "pending"])

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

    def test_changed_evidence_invalidates_preview(self):
        pid = self.preview()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET label='new-version' WHERE id='delivery'")
        status, result = self.req("POST", f"/acceptance/previews/{pid}/confirm")
        self.assertEqual(status, 409)
        self.assertEqual(result["code"], "ACCEPTANCE_EVIDENCE_CHANGED")
        self.assertEqual(self.req("GET", "/tasks")[1], [])

    def test_changed_later_section_invalidates_preview(self):
        pid = self.preview()
        with self.store.connection(write=True) as db:
            row = db.execute("SELECT sections_json FROM sources WHERE id='delivery'").fetchone()
            sections = json.loads(row[0])
            sections.append({"title": "新增条款", "text": "补充验收要求"})
            db.execute("UPDATE sources SET sections_json=? WHERE id='delivery'", (json.dumps(sections),))
        status, result = self.req("POST", f"/acceptance/previews/{pid}/confirm")
        self.assertEqual((status, result["code"]), (409, "ACCEPTANCE_EVIDENCE_CHANGED"))
        self.assertEqual(self.req("GET", "/tasks")[1], [])

    def test_legacy_preview_requires_new_preview(self):
        pid = self.preview()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE acceptance_previews SET evidence_fingerprint=NULL WHERE id=?", (pid,))
        status, result = self.req("POST", f"/acceptance/previews/{pid}/confirm")
        self.assertEqual((status, result["code"]), (409, "ACCEPTANCE_EVIDENCE_CHANGED"))

    def test_disabled_assignee_after_preview_cannot_receive_new_task(self):
        pid = self.preview()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE users SET enabled=0 WHERE id='u_reviewer'")
        status, result = self.req("POST", f"/acceptance/previews/{pid}/confirm")
        self.assertEqual((status, result["code"]), (422, "ASSIGNEE_REQUIRED"))
        self.assertEqual(self.req("GET", "/tasks")[1], [])

    def test_reassign_disabled_assignee_and_invalidate_preview(self):
        pid = self.preview()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE users SET enabled=0 WHERE id='u_reviewer'")
        route = "/acceptance/projects/demo-a/items/checklist/reassign"
        self.assertEqual(self.req("POST", route, {"base_version": 1, "assignee_id": "u_admin"})[0], 422)
        self.assertEqual(self.req("POST", route, {"base_version": 1, "assignee_id": "u_writer"}, who="reviewer")[0], 401)
        self.assertEqual(self.req("POST", route, {"base_version": 1, "assignee_id": "u_writer"}), (200, {"version": 2}))
        self.assertEqual(self.req("POST", f"/acceptance/previews/{pid}/confirm")[1]["code"], "ACCEPTANCE_CHANGED")
        status, preview = self.req("POST", "/acceptance/projects/demo-a/items/checklist/preview", {"base_version": 2})
        self.assertEqual(status, 200)
        task = self.req("POST", f"/acceptance/previews/{preview['id']}/confirm")
        self.assertEqual(task[0], 200)
        self.assertEqual(self.req("POST", route, {"base_version": 2, "assignee_id": "u_writer"})[1]["code"], "ACCEPTANCE_TASK_ACTIVE")

    def test_reassignment_permissions_candidates_and_stale_version(self):
        route = "/acceptance/projects/demo-a/items/checklist/reassign"
        owner_view = self.req("GET", "/acceptance/projects/demo-a")[1]
        self.assertEqual({u["id"] for u in owner_view["assignee_candidates"]}, {"u_writer", "u_reviewer"})
        self.assertEqual(self.req("GET", "/acceptance/projects/demo-a", who="reviewer")[1]["assignee_candidates"], [])
        status, result = self.req("POST", route, {"base_version": 1, "assignee_id": "u_writer"}, who="reviewer")
        self.assertEqual((status, result["code"]), (403, "ACCEPTANCE_OWNER_REQUIRED"))
        self.assertEqual(self.req("POST", route, {"base_version": 1, "assignee_id": "u_writer"})[0], 200)
        status, result = self.req("POST", route, {"base_version": 1, "assignee_id": "u_reviewer"})
        self.assertEqual((status, result["code"]), (409, "ACCEPTANCE_CHANGED"))
        with self.store.connection() as db:
            item = db.execute("SELECT assignee_id FROM acceptance_items WHERE project_id='demo-a' AND item_id='checklist'").fetchone()
            self.assertEqual(item[0], "u_writer")

    def test_database_unavailable_is_an_error_not_a_missing_count(self):
        from unittest.mock import patch
        with patch.object(self.app.acceptance, "detail", side_effect=RuntimeError("unavailable")):
            status, data = self.req("GET", "/acceptance/projects/demo-a")
        self.assertEqual(status, 500)
        self.assertNotIn("summary", data)
