from http.cookies import SimpleCookie
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application import Application
from auth import REMEMBER_SECONDS, SESSION_SECONDS, token_hash
from store import Problem, Store


class LoginConvenienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="login-tests-", dir=Path(__file__).parent)
        cls.seed = Path(cls.workspace.name) / "seed.db"
        cls.passwords = {name: secrets.token_urlsafe(24) for name in ("writer", "reviewer", "admin")}
        Store(cls.seed).bootstrap(cls.passwords, json.loads((ROOT / "fixtures.json").read_text()))

    @classmethod
    def tearDownClass(cls):
        cls.workspace.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(dir=self.workspace.name)
        self.database = Path(self.case.name) / "test.db"
        shutil.copy2(self.seed, self.database)
        self.store = Store(self.database, demo_mode=True)
        self.app = Application(self.store, "https://demo.example.com")
        self.admin = next(user for user in self.store.users() if user["username"] == "admin")

    def tearDown(self):
        self.case.cleanup()

    def request(self, method, endpoint, body=None, token="", csrf="", extra=None):
        headers = {"Host": self.app.host, "Origin": self.app.origin, "Content-Type": "application/json",
                   "Cookie": self.app.cookie_name + "=" + token, "X-CSRF-Token": csrf,
                   "Idempotency-Key": secrets.token_hex(16), **(extra or {})}
        response = self.app.handle(method, "/api/workbench" + endpoint, headers, json.dumps(body or {}).encode())
        return response, json.loads(response.body)

    def login(self, remember=False, name="writer"):
        response, result = self.request("POST", "/session", {"username": name, "password": self.passwords[name], "remember": remember})
        self.assertEqual(response.status, 200)
        cookie = SimpleCookie(response.headers["Set-Cookie"])
        return cookie[self.app.cookie_name].value, result["data"], response

    def test_default_config_keeps_strict_policy_and_no_public_accounts(self):
        self.store = Store(self.database)
        self.app = Application(self.store)
        response, result = self.request("GET", "/login-options")
        self.assertEqual(response.status, 200)
        self.assertEqual(result["data"], {"demo": False, "accounts": [], "password_min_length": 20, "remember_seconds": SESSION_SECONDS})
        with self.assertRaises(Problem):
            self.store.account_password(secrets.token_hex(4))

    def test_demo_options_list_only_fixed_accounts_without_passwords(self):
        with self.store.connection() as db:
            before = db.execute("SELECT count(*) FROM sessions").fetchone()[0]
        response, result = self.request("GET", "/login-options")
        self.assertEqual(response.status, 200)
        self.assertEqual([item["username"] for item in result["data"]["accounts"]], ["writer", "reviewer", "admin"])
        self.assertEqual(result["data"]["password_min_length"], 6)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        for password in self.passwords.values():
            self.assertNotIn(password, response.body.decode())
        self.assertNotIn("password_hash", response.body.decode())
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], before)

    def test_disabled_or_changed_role_is_not_advertised(self):
        with self.store.connection(write=True) as db:
            db.execute("UPDATE users SET enabled=0 WHERE username='reviewer'")
            db.execute("UPDATE users SET role='employee' WHERE username='admin'")
        _, result = self.request("GET", "/login-options")
        self.assertEqual([item["username"] for item in result["data"]["accounts"]], ["writer"])

    def test_custom_user_is_not_exposed_by_public_options(self):
        self.store.create_user(self.admin, {"username": "private_user", "name": "私有姓名", "role": "employee", "password": secrets.token_urlsafe(24)}, secrets.token_hex(16))
        response, _ = self.request("GET", "/login-options")
        self.assertNotIn("private_user", response.body.decode())
        self.assertNotIn("私有姓名", response.body.decode())

    def test_remember_option_matches_cookie_and_server_expiry(self):
        for remember, seconds in [(False, SESSION_SECONDS), (True, REMEMBER_SECONDS)]:
            with self.subTest(remember=remember), patch("store.time.time", return_value=1_800_000_000):
                token, user, response = self.login(remember)
                with self.store.connection() as db:
                    expiry = db.execute("SELECT expires FROM sessions WHERE token_hash=?", (token_hash(token),)).fetchone()[0]
                self.assertEqual(expiry, 1_800_000_000 + seconds)
                self.assertIn(f"Max-Age={seconds}", response.headers["Set-Cookie"])
                self.assertIn("__Host-workbench_session=", response.headers["Set-Cookie"])
                for flag in ["HttpOnly", "SameSite=Strict", "Secure", "Path=/"]:
                    self.assertIn(flag, response.headers["Set-Cookie"])
                self.assertNotIn("Domain=", response.headers["Set-Cookie"])
                self.assertNotIn("password", user)

    def test_non_demo_does_not_extend_session_even_if_client_requests_it(self):
        self.store = Store(self.database)
        self.app = Application(self.store)
        _, _, response = self.login(True)
        self.assertIn(f"Max-Age={SESSION_SECONDS}", response.headers["Set-Cookie"])

    def test_invalid_remember_values_do_not_create_session(self):
        for value in ["true", 1, None, [], {}]:
            with self.subTest(value=value):
                response, result = self.request("POST", "/session", {"username": "writer", "password": self.passwords["writer"], "remember": value})
                self.assertEqual(response.status, 422)
                self.assertEqual(result["error"]["code"], "INVALID_INPUT")
                self.assertNotIn("Set-Cookie", response.headers)

    def test_remember_does_not_bypass_password_or_login_rate_limit(self):
        for attempt in range(13):
            response, _ = self.request("POST", "/session", {"username": "admin", "password": "incorrect", "remember": True})
            self.assertEqual(response.status, 401 if attempt < 12 else 429)
        self.assertNotIn("Set-Cookie", response.headers)

    def test_remembered_cookie_expires_without_sliding_refresh(self):
        with patch("store.time.time", return_value=1_800_000_000):
            token, user, _ = self.login(True)
        with patch("store.time.time", return_value=1_800_000_000 + REMEMBER_SECONDS - 1):
            response, _ = self.request("GET", "/session", token=token)
            self.assertEqual(response.status, 200)
            self.assertNotIn("Set-Cookie", response.headers)
        with patch("store.time.time", return_value=1_800_000_000 + REMEMBER_SECONDS + 1):
            response, _ = self.request("GET", "/session", token=token)
            self.assertEqual(response.status, 401)

    def test_logout_revokes_remembered_session_and_clears_cookie(self):
        token, user, _ = self.login(True)
        response, _ = self.request("POST", "/session/logout", token=token, csrf=user["csrf"])
        self.assertEqual(response.status, 200)
        self.assertIn("Max-Age=0", response.headers["Set-Cookie"])
        self.assertEqual(self.request("GET", "/session", token=token)[0].status, 401)

    def test_demo_password_reset_allows_six_chars_and_revokes_all_sessions(self):
        token, _, _ = self.login(True)
        second = self.store.login("writer", self.passwords["writer"], True)[0]
        new_password = secrets.token_hex(3)
        account = next(item for item in self.store.admin_users(self.admin) if item["username"] == "writer")
        result = self.store.reset_user_password(self.admin, account["id"], {"base_version": account["version"], "password": new_password}, secrets.token_hex(16))
        self.assertNotIn(new_password, json.dumps(result))
        for old in (token, second):
            with self.assertRaises(Problem):
                self.store.session(old)
        self.assertEqual(self.store.login("writer", new_password)[1]["username"], "writer")
        with self.store.connection() as db:
            self.assertNotEqual(db.execute("SELECT password_hash FROM users WHERE username='writer'").fetchone()[0], new_password)

    def test_demo_still_rejects_too_short_blank_and_control_char_passwords(self):
        for password in ["short", "abc", " " * 6, "abcd\nefgh", "abcd\x7fefgh"]:
            with self.subTest(password=repr(password)), self.assertRaises(Problem):
                self.store.account_password(password)

    def test_client_cannot_enable_demo_password_policy(self):
        self.store = Store(self.database)
        self.app = Application(self.store)
        token, user, _ = self.login(name="admin")
        response, result = self.request("POST", "/admin/users", {"username": "new_test", "name": "测试", "role": "employee", "password": secrets.token_hex(3), "demo_mode": True, "password_min_length": 6}, token, user["csrf"])
        self.assertEqual(response.status, 422)
        self.assertEqual(result["error"]["code"], "INVALID_ACCOUNT")

    def test_demo_does_not_bypass_admin_csrf_or_origin(self):
        token, user, _ = self.login(True)
        body = {"username": "new_test", "name": "测试", "role": "employee", "password": secrets.token_hex(4)}
        self.assertEqual(self.request("POST", "/admin/users", body, token, user["csrf"])[0].status, 403)
        admin_token, admin, _ = self.login(True, "admin")
        for headers, csrf in [({}, "wrong"), ({"Origin": "https://other.example"}, admin["csrf"])]:
            self.assertEqual(self.request("POST", "/admin/users", body, admin_token, csrf, headers)[0].status, 403)

    def test_disabling_account_revokes_remembered_session(self):
        token, _, _ = self.login(True)
        item = next(user for user in self.store.admin_users(self.admin) if user["username"] == "writer")
        self.store.update_user(self.admin, item["id"], {"base_version": item["version"], "name": item["name"], "role": item["role"], "active": False}, secrets.token_hex(16))
        self.assertEqual(self.request("GET", "/session", token=token)[0].status, 401)

    def test_three_demo_accounts_can_share_a_user_chosen_password_without_changing_roles(self):
        sessions = {name: self.login(True, name) for name in ("writer", "reviewer", "admin")}
        token, admin, _ = sessions["admin"]
        before = {item["username"]: item for item in self.store.admin_users(self.admin)}
        new_password = secrets.token_hex(3)
        for name in ("writer", "reviewer", "admin"):
            item = before[name]
            response, result = self.request("POST", f"/admin/users/{item['id']}/password",
                                            {"base_version": item["version"], "password": new_password}, token, admin["csrf"])
            self.assertEqual(response.status, 200)
            self.assertNotIn(new_password, json.dumps(result))
            self.assertEqual(self.request("GET", "/session", token=sessions[name][0])[0].status, 401)
            response, result = self.request("POST", "/session", {"username": name, "password": new_password})
            self.assertEqual(response.status, 200)
            self.assertEqual(result["data"]["id"], item["id"])
            self.assertEqual(result["data"]["role"], item["role"])
            response, _ = self.request("POST", "/session", {"username": name, "password": self.passwords[name]})
            self.assertEqual(response.status, 401)
        after = {item["username"]: item for item in self.store.admin_users(self.admin)}
        self.assertEqual(set(before), set(after))
        for name, item in before.items():
            for field in ("id", "username", "name", "role", "active"):
                self.assertEqual(after[name][field], item[field])
            self.assertEqual(after[name]["version"], item["version"] + 1)


if __name__ == "__main__":
    unittest.main()
