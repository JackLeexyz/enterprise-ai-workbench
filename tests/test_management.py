from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application import Application
from auth import check_password
from server import Handler
from store import Problem, Store, dump


class ManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="management-tests-", dir=Path(__file__).parent)
        cls.seed = Path(cls.workspace.name) / "seed.db"
        cls.passwords = {name: secrets.token_urlsafe(24) for name in ("writer", "reviewer", "admin")}
        Store(cls.seed).bootstrap(cls.passwords, json.loads((ROOT / "fixtures.json").read_text()))

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=self.workspace.name)
        database = Path(self.temp.name) / "case.db"
        shutil.copy2(self.seed, database)
        self.store = Store(database)
        self.users = {item["username"]: item for item in self.store.users()}
        self.admin, self.writer, self.reviewer = [self.users[name] for name in ("admin", "writer", "reviewer")]
        self.app = Application(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def problem(self, code, fn, *args):
        with self.assertRaises(Problem) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def account(self, uid):
        return next(item for item in self.store.admin_users(self.admin) if item["id"] == uid)

    def update(self, user, **changes):
        row = self.account(user["id"])
        return self.store.update_user(self.admin, user["id"], {"base_version": row["version"], **changes}, self.key())

    def create(self, **changes):
        payload = {"username": "new.employee", "name": "新同事", "role": "employee", "password": secrets.token_urlsafe(24), **changes}
        return self.store.create_user(self.admin, payload, self.key()), payload

    def category(self, cid="cat_product"):
        return next(item for item in self.store.admin_categories(self.admin) if item["id"] == cid)

    def source(self, **changes):
        return self.store.create_source(self.writer, {"title": "个人资料", "text": "私有正文，不得自动开放", **changes}, self.key())

    def request(self, method, path, body=None, token=None, extra=None):
        token, user = token or self.store.login("admin", self.passwords["admin"])
        headers = {"Host": self.app.host, "Cookie": self.app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"]}
        if method != "GET":
            headers.update({"Origin": self.app.origin, "Content-Type": "application/json", "Idempotency-Key": self.key()})
        headers.update(extra or {})
        response = self.app.handle(method, "/api/workbench" + path, headers, json.dumps(body or {}).encode() if method != "GET" else b"")
        return response, json.loads(response.body)

    def test_admin_list_is_explicit_and_never_contains_credentials(self):
        self.assertEqual(len(self.store.admin_users(self.admin)), 3)
        for row in self.store.admin_users(self.admin):
            self.assertEqual(set(row), {"id", "username", "name", "role", "active", "version", "updated_at"})
            self.assertIs(type(row["active"]), bool)
            self.assertEqual(row["version"], 1)
        self.problem("ADMIN_REQUIRED", self.store.admin_users, self.writer)

    def test_created_account_can_login_without_opening_private_content(self):
        private = self.source()
        account, payload = self.create(role="admin")
        _, user = self.store.login(payload["username"], payload["password"])
        self.assertEqual(user["role"], "admin")
        self.assertNotIn("password", dump(account))
        self.assertNotIn(payload["password"], dump(account))
        self.problem("NOT_FOUND", self.store.get_source, user, private["id"])
        self.assertEqual(self.store.list_artifacts(user), [])

    def test_account_validation_duplicate_and_employee_access(self):
        for change in ({"role": "owner"}, {"username": "A B"}, {"username": "ab"}, {"name": " "}, {"password": "short"}):
            self.problem("INVALID_ACCOUNT", self.store.create_user, self.admin, {"username": "valid.user", "name": "同事", "role": "employee", "password": secrets.token_urlsafe(24), **change}, self.key())
        _, payload = self.create()
        self.problem("ACCOUNT_EXISTS", self.store.create_user, self.admin, payload, self.key())
        self.problem("ADMIN_REQUIRED", self.store.create_user, self.writer, payload, self.key())

    def test_creation_idempotency_binds_password_without_fast_secret_digest(self):
        payload = {"username": "retry.user", "name": "重试同事", "role": "employee", "password": secrets.token_urlsafe(24)}
        key = self.key()
        first = self.store.create_user(self.admin, payload, key)
        self.assertEqual(first, self.store.create_user(self.admin, payload, key))
        self.problem("IDEMPOTENCY_MISMATCH", self.store.create_user, self.admin, {**payload, "password": secrets.token_urlsafe(24)}, key)
        with self.store.connection() as db:
            row = db.execute("SELECT * FROM idempotency WHERE user_id=? AND key=?", (self.admin["id"], key)).fetchone()
            self.assertNotEqual(row["payload_hash"], hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
            self.assertNotIn(payload["password"], dump(dict(row)))
            self.assertNotIn("pbkdf2", row["result_json"])
            self.assertEqual(db.execute("SELECT count(*) FROM users WHERE username=?", (payload["username"],)).fetchone()[0], 1)

    def test_disable_revokes_all_sessions_and_reenable_cannot_restore_tokens(self):
        tokens = [self.store.login("writer", self.passwords["writer"])[0] for _ in range(2)]
        updated = self.update(self.writer, active=False)
        self.assertFalse(updated["active"])
        self.assertNotIn(self.writer["id"], {item["id"] for item in self.store.users()})
        self.problem("LOGIN_FAILED", self.store.login, "writer", self.passwords["writer"])
        self.update(self.writer, active=True)
        for token in tokens:
            self.problem("SESSION_EXPIRED", self.store.session, token)
        self.assertEqual(self.store.login("writer", self.passwords["writer"])[1]["id"], self.writer["id"])

    def test_role_change_revokes_sessions_and_stale_admin_operations(self):
        promoted = self.update(self.reviewer, role="admin")
        token, user = self.store.login("reviewer", self.passwords["reviewer"])
        self.assertEqual(promoted["role"], "admin")
        self.update(self.reviewer, role="employee")
        self.problem("SESSION_EXPIRED", self.store.session, token)
        self.problem("ADMIN_REQUIRED", self.store.admin_users, user)
        self.problem("ADMIN_REQUIRED", self.store.admin_templates, user)
        self.problem("SESSION_EXPIRED", self.store.create_source, user, {"title": "旧会话", "text": "不可写入"}, self.key())

    def test_last_enabled_admin_cannot_be_disabled_or_demoted(self):
        for changes in ({"active": False}, {"role": "employee"}, {"active": False, "role": "employee"}):
            self.problem("LAST_ADMIN", self.store.update_user, self.admin, self.admin["id"], {"base_version": 1, **changes}, self.key())
        self.assertEqual(self.account(self.admin["id"])["version"], 1)

    def test_disabled_admin_does_not_count_for_last_admin_guard(self):
        self.update(self.reviewer, role="admin")
        self.update(self.reviewer, active=False)
        self.problem("LAST_ADMIN", self.store.update_user, self.admin, self.admin["id"], {"base_version": 1, "active": False}, self.key())

    def test_two_concurrent_self_demotions_preserve_one_admin(self):
        self.update(self.reviewer, role="admin")
        reviewer = {**self.reviewer, "role": "admin"}
        versions = {item["id"]: item["version"] for item in self.store.admin_users(self.admin)}
        def demote(actor):
            try:
                return self.store.update_user(actor, actor["id"], {"base_version": versions[actor["id"]], "role": "employee"}, self.key())["role"]
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(demote, [self.admin, reviewer]))
        self.assertEqual(sorted(results), ["LAST_ADMIN", "employee"])
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0], 1)

    def test_account_version_conflict_and_bool_validation(self):
        body = {"base_version": 1, "name": "更新姓名"}
        key = self.key()
        first = self.store.update_user(self.admin, self.writer["id"], body, key)
        self.assertEqual(first, self.store.update_user(self.admin, self.writer["id"], body, key))
        self.problem("VERSION_CONFLICT", self.store.update_user, self.admin, self.writer["id"], {"base_version": 1, "active": False}, self.key())
        self.problem("INVALID_ACCOUNT", self.store.update_user, self.admin, self.writer["id"], {"base_version": 2, "active": 1}, self.key())
        self.assertEqual(first["version"], 2)

    def test_reset_password_is_versioned_idempotent_and_revokes_sessions(self):
        token, _ = self.store.login("writer", self.passwords["writer"])
        new_password = secrets.token_urlsafe(24)
        body, key = {"base_version": 1, "password": new_password}, self.key()
        first = self.store.reset_user_password(self.admin, self.writer["id"], body, key)
        self.assertEqual(first, self.store.reset_user_password(self.admin, self.writer["id"], body, key))
        self.assertEqual(first["version"], 2)
        self.problem("LOGIN_FAILED", self.store.login, "writer", self.passwords["writer"])
        self.assertEqual(self.store.login("writer", new_password)[1]["id"], self.writer["id"])
        self.problem("SESSION_EXPIRED", self.store.session, token)
        self.problem("IDEMPOTENCY_MISMATCH", self.store.reset_user_password, self.admin, self.writer["id"], {**body, "password": secrets.token_urlsafe(24)}, key)
        with self.store.connection() as db:
            self.assertTrue(check_password(new_password, db.execute("SELECT password_hash FROM users WHERE id=?", (self.writer["id"],)).fetchone()[0]))
            for row in db.execute("SELECT action,object_id FROM audit"):
                self.assertNotIn(new_password, dump(dict(row)))

    def test_reset_permissions_self_revocation_and_stale_version(self):
        self.problem("ADMIN_REQUIRED", self.store.reset_user_password, self.writer, self.writer["id"], {"base_version": 1, "password": secrets.token_urlsafe(24)}, self.key())
        self.update(self.writer, name="新的名字")
        self.problem("VERSION_CONFLICT", self.store.reset_user_password, self.admin, self.writer["id"], {"base_version": 1, "password": secrets.token_urlsafe(24)}, self.key())
        token = self.store.login("admin", self.passwords["admin"])
        response, result = self.request("POST", "/admin/users/u_admin/password", {"base_version": 1, "password": secrets.token_urlsafe(24)}, token=token)
        self.assertEqual(response.status, 200)
        self.assertTrue(result["data"]["reauthenticate"])
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        self.problem("SESSION_EXPIRED", self.store.session, token[0])

    def test_disabled_caller_cannot_replay_previous_write(self):
        body, key = {"title": "测试", "text": "保留旧记录"}, self.key()
        self.store.create_source(self.writer, body, key)
        self.update(self.writer, active=False)
        self.problem("SESSION_EXPIRED", self.store.create_source, self.writer, body, key)

    def test_demote_revokes_assigned_publication_grant_without_deleting_source(self):
        self.update(self.reviewer, role="admin")
        reviewer = {**self.reviewer, "role": "admin"}
        source = self.source()
        self.store.request_publication(self.writer, source["id"], {"base_version": source["version"], "admin_id": reviewer["id"]}, self.key())
        self.assertEqual(self.store.get_source(reviewer, source["id"])["title"], source["title"])
        self.update(self.reviewer, role="employee")
        self.problem("NOT_FOUND", self.store.get_source, self.reviewer, source["id"])
        self.assertEqual(self.store.get_source(self.writer, source["id"])["publication"], "draft")
        request = self.store.request_publication(self.writer, source["id"], {"base_version": source["version"], "admin_id": self.admin["id"]}, self.key())
        self.assertEqual(request["status"], "pending")

    def test_disabled_assignee_is_rechecked_when_confirming_old_preview(self):
        preview = self.store.task_preview(self.writer, {"title": "待办", "assignee_id": self.reviewer["id"]}, self.key())
        self.update(self.reviewer, active=False)
        self.problem("ASSIGNEE_REQUIRED", self.store.confirm_task, self.writer, preview["id"], {}, self.key())
        self.assertEqual(self.store.tasks(self.writer), [])

    def test_catalog_defaults_and_new_source_uses_stable_category_id(self):
        rows = self.store.categories()
        self.assertEqual(rows[0]["id"], "cat_other")
        self.assertTrue(rows[0]["active"])
        created = self.source(category_id="cat_product")
        self.assertEqual((created["category_id"], created["category"]), ("cat_product", "产品资料"))
        self.assertEqual(self.source()["category_id"], "cat_other")
        self.assertTrue(all(set(row) == {"id", "name", "active", "version", "updated_at"} for row in rows))

    def test_category_rename_changes_directory_not_content_or_reference_identity(self):
        source = self.store.get_source(self.writer, "product")
        doc = self.store.get_artifact(self.writer, "proposal")
        row = self.category()
        self.store.update_category(self.admin, row["id"], {"base_version": row["version"], "name": "企业产品资料"}, self.key())
        after = self.store.get_source(self.writer, "product")
        self.assertEqual(after["category"], "企业产品资料")
        for key in ("version_id", "version", "sections", "publication", "scope", "updated_at"):
            self.assertEqual(after[key], source[key])
        self.assertEqual(self.store.get_artifact(self.writer, "proposal")["citations"], doc["citations"])
        self.assertEqual(self.store.list_sources(self.writer, "企业产品资料")[0]["id"], "product")

    def test_category_disable_prevents_new_use_but_keeps_existing_knowledge(self):
        row = self.category()
        self.store.update_category(self.admin, row["id"], {"base_version": row["version"], "active": False}, self.key())
        self.assertNotIn(row["id"], {item["id"] for item in self.store.categories()})
        self.assertEqual(self.store.get_source(self.writer, "product")["category_id"], row["id"])
        self.problem("CATEGORY_UNAVAILABLE", self.store.create_source, self.writer, {"title": "新资料", "text": "正文", "category_id": row["id"]}, self.key())
        self.problem("CATEGORY_UNAVAILABLE", self.store.create_source, self.writer, {"title": "新资料", "text": "正文", "category": row["name"]}, self.key())
        self.assertTrue(self.store.ask(self.writer, {"question": "企业 AI 工作台"}, self.key())["matches"])

    def test_category_default_is_protected_and_catalog_has_no_private_counts(self):
        for changes in ({"active": False}, {"name": "改掉其他"}):
            self.problem("DEFAULT_CATEGORY", self.store.update_category, self.admin, "cat_other", {"base_version": 1, **changes}, self.key())
        self.source()
        self.assertNotIn("私有正文", dump(self.store.admin_categories(self.admin)))
        self.assertTrue(all("count" not in key for row in self.store.admin_categories(self.admin) for key in row))
        self.problem("ADMIN_REQUIRED", self.store.admin_categories, self.writer)

    def test_category_create_validation_idempotency_and_name_collision(self):
        body, key = {"name": "售后知识"}, self.key()
        first = self.store.create_category(self.admin, body, key)
        self.assertEqual(first, self.store.create_category(self.admin, body, key))
        self.problem("CATEGORY_EXISTS", self.store.create_category, self.admin, body, self.key())
        for body in ({"name": " "}, {"name": "X" * 31}):
            self.problem("INVALID_INPUT", self.store.create_category, self.admin, body, self.key())
        self.problem("ADMIN_REQUIRED", self.store.create_category, self.writer, {"name": "私自创建"}, self.key())

    def test_concurrent_category_edits_require_current_version(self):
        body = {"base_version": 1, "name": "产品与能力"}
        def update(_):
            try:
                return self.store.update_category(self.admin, "cat_product", body, self.key())["version"]
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, range(2)))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(results.count("VERSION_CONFLICT"), 1)

    def test_restart_does_not_recreate_renamed_or_disabled_category(self):
        self.store.update_category(self.admin, "cat_product", {"base_version": 1, "name": "企业产品", "active": False}, self.key())
        restored = Store(self.store.path)
        self.assertNotIn("产品资料", {item["name"] for item in restored.categories()})
        item = next(item for item in restored.admin_categories(self.admin) if item["id"] == "cat_product")
        self.assertEqual((item["name"], item["active"], item["version"]), ("企业产品", False, 2))
        self.assertEqual(restored.get_source(self.writer, "product")["category"], "企业产品")

    def test_old_schema_migration_preserves_rows_and_does_not_publish_private_labels(self):
        unknown = "旧个人分类-不可扩散"
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET category=? WHERE id='weekly'", (unknown,))
            db.execute("DROP TABLE source_categories")
            db.execute("DROP TABLE categories")
            db.execute("DROP TABLE account_versions")
            original = [tuple(row) for row in db.execute("SELECT * FROM sources ORDER BY id")]
            original_users = [tuple(row) for row in db.execute("SELECT * FROM users ORDER BY id")]
        migrated = Store(self.store.path)
        with migrated.connection() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM sources ORDER BY id")], original)
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM users ORDER BY id")], original_users)
        self.assertNotIn(unknown, dump(migrated.categories()))
        source = migrated.get_source(self.writer, "weekly")
        self.assertEqual(source["category_id"], "cat_other")
        self.assertEqual(source["legacy_category"], unknown)
        self.problem("NOT_FOUND", migrated.get_source, self.admin, "weekly")

    def test_import_confirmation_revalidates_catalog_without_consuming_preview(self):
        job = self.store.enqueue_import(self.writer, "演示.txt", "新资料正文".encode(), self.key())
        claimed = self.store.claim_import()
        self.store.finish_import(claimed["id"], {"sections": [{"title": "正文", "text": "新资料正文"}], "warnings": [], "characters": 6})
        self.store.update_category(self.admin, "cat_product", {"base_version": 1, "active": False}, self.key())
        self.problem("CATEGORY_UNAVAILABLE", self.store.commit_import, self.writer, job["id"], {"title": "资料", "category_id": "cat_product"}, self.key())
        self.assertEqual(self.store.get_import(self.writer, job["id"])["status"], "preview")
        saved = self.store.commit_import(self.writer, job["id"], {"title": "资料", "category_id": "cat_other"}, self.key())
        self.assertEqual(self.store.get_source(self.writer, saved["source_id"])["category_id"], "cat_other")

    def test_management_routes_csrf_roles_and_patch_contract(self):
        response, result = self.request("GET", "/admin/users")
        self.assertEqual(response.status, 200)
        self.assertEqual(len(result["data"]), 3)
        for path in ("/admin/users", "/admin/categories"):
            response, _ = self.request("GET", path, token=self.store.login("writer", self.passwords["writer"]))
            self.assertEqual(response.status, 403)
        response, result = self.request("PATCH", "/admin/users/u_writer", {"base_version": 1, "active": False})
        self.assertEqual(response.status, 200)
        self.assertFalse(result["data"]["active"])
        response, _ = self.request("POST", "/admin/categories", {"name": "客户服务"}, extra={"X-CSRF-Token": "wrong"})
        self.assertEqual(response.status, 403)
        response, result = self.request("POST", "/admin/categories", {"name": "客户服务"})
        self.assertEqual(response.status, 200)
        cid = result["data"]["id"]
        response, result = self.request("PATCH", "/admin/categories/" + cid, {"base_version": 1, "active": False})
        self.assertEqual(response.status, 200)
        response, _ = self.request("DELETE", "/admin/categories/" + cid, {})
        self.assertEqual(response.status, 405)

    def test_account_edit_concurrency_only_one_current_version_wins(self):
        def change(index):
            try:
                return self.store.update_user(self.admin, self.writer["id"], {"base_version": 1, "name": "姓名" + str(index)}, self.key())["version"]
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(change, range(2)))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(results.count("VERSION_CONFLICT"), 1)

    def test_reset_disabled_account_does_not_enable_or_restore_it(self):
        self.update(self.writer, active=False)
        password = secrets.token_urlsafe(24)
        result = self.store.reset_user_password(self.admin, self.writer["id"], {"base_version": 2, "password": password}, self.key())
        self.assertFalse(result["active"])
        self.problem("LOGIN_FAILED", self.store.login, "writer", password)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT owner_id FROM sources WHERE id='weekly'").fetchone()[0], self.writer["id"])

    def test_old_disable_replay_reports_current_state_without_disabling_again(self):
        body, key = {"base_version": 1, "active": False}, self.key()
        self.store.update_user(self.admin, self.writer["id"], body, key)
        self.update(self.writer, active=True)
        replay = self.store.update_user(self.admin, self.writer["id"], body, key)
        self.assertTrue(replay["active"])
        self.assertEqual(replay["version"], 3)

    def test_catalog_rename_keeps_saved_evidence_identity_and_text(self):
        message = self.store.ask(self.writer, {"question": "企业 AI 工作台"}, self.key())
        match = next(item for item in message["matches"] if item["source_id"] == "product")
        evidence = self.store.get_evidence(self.writer, message["id"], match["evidence_id"])
        self.store.update_category(self.admin, "cat_product", {"base_version": 1, "name": "产品知识"}, self.key())
        refreshed = self.store.get_evidence(self.writer, message["id"], match["evidence_id"])
        self.assertEqual(refreshed["category"], "产品知识")
        self.assertEqual({key: value for key, value in evidence.items() if key != "category"}, {key: value for key, value in refreshed.items() if key != "category"})

    def test_create_account_api_and_missing_intent_never_echo_credentials(self):
        payload = {"username": "api.user", "name": "API同事", "role": "employee", "password": secrets.token_urlsafe(24)}
        response, result = self.request("POST", "/admin/users", payload)
        self.assertEqual(response.status, 200)
        self.assertNotIn(payload["password"], response.body.decode())
        self.assertNotIn("password_hash", response.body.decode())
        self.assertTrue(result["data"]["active"])
        self.assertEqual(self.store.login(payload["username"], payload["password"])[1]["name"], "API同事")
        self.problem("IDEMPOTENCY_REQUIRED", self.store.create_user, self.admin, {**payload, "username": "another.user"}, None)

    def test_patch_http_parser_without_creating_any_socket(self):
        class MemorySocket:
            def __init__(self, data):
                self.input, self.output = io.BytesIO(data), io.BytesIO()
            def makefile(self, *args):
                return self.input
            def sendall(self, data):
                self.output.write(data)
            def settimeout(self, value):
                pass
        token, user = self.store.login("admin", self.passwords["admin"])
        payload = dump({"base_version": 1, "name": "更新账号"}).encode()
        headers = {"Host": self.app.host, "Origin": self.app.origin, "Content-Type": "application/json", "Content-Length": str(len(payload)), "X-CSRF-Token": user["csrf"], "Cookie": self.app.cookie_name + "=" + token, "Idempotency-Key": self.key()}
        raw = b"PATCH /api/workbench/admin/users/u_writer HTTP/1.1\r\n" + "".join(f"{key}: {value}\r\n" for key, value in headers.items()).encode() + b"\r\n" + payload
        connection = MemorySocket(raw)
        Handler(connection, ("127.0.0.1", 12345), SimpleNamespace(app=self.app, trust_proxy=False))
        response = connection.output.getvalue()
        self.assertTrue(response.startswith(b"HTTP/1.0 200"))
        self.assertIn("更新账号".encode(), response)


if __name__ == "__main__":
    unittest.main()
