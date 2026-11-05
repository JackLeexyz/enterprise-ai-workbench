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
from store import Store, Problem


class ConversationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="conversations-", dir=Path(__file__).parent)
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
        self.writer, self.reader, self.admin = ({"id": "u_" + name, "role": "admin" if name == "admin" else "employee"} for name in ("writer", "reviewer", "admin"))

    def tearDown(self):
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def problem(self, code, fn, *args):
        with self.assertRaises(Problem) as caught: fn(*args)
        self.assertEqual(caught.exception.code, code)

    def create(self, title="资料咨询", user=None):
        return self.store.create_conversation(user or self.writer, {"title": title}, self.key())

    def send(self, cid, question="验收需要哪些材料？", **changes):
        conversation = self.store.get_conversation(self.writer, cid)
        return self.store.ask_conversation(self.writer, cid, {"text": question, "scope": "all", "base_version": conversation["version"], **changes}, self.key())

    def test_creation_is_private_empty_and_idempotent(self):
        key = self.key()
        a = self.store.create_conversation(self.writer, {}, key)
        b = self.store.create_conversation(self.writer, {}, key)
        self.assertEqual(a["id"], b["id"]); self.assertEqual(a["state"], "ready")
        self.assertEqual(a["message_count"], 0); self.assertEqual(len(self.store.list_conversations(self.writer)), 1)

    def test_other_employee_or_admin_cannot_read_update_or_send(self):
        item = self.create()
        for user in (self.reader, self.admin):
            self.assertEqual(self.store.list_conversations(user), [])
            self.problem("NOT_FOUND", self.store.get_conversation, user, item["id"])
            self.problem("NOT_FOUND", self.store.conversation_messages, user, item["id"])
            self.problem("NOT_FOUND", self.store.update_conversation, user, item["id"], {"base_version": 1, "title": "越权"}, self.key())
            self.problem("NOT_FOUND", self.store.ask_conversation, user, item["id"], {"base_version": 1, "text": "验收材料"}, self.key())

    def test_messages_are_isolated_and_legacy_records_are_not_reassigned(self):
        legacy = self.store.ask(self.writer, {"question": "旧问题验收材料"}, self.key())
        a, b = self.create("甲"), self.create("乙")
        msg = self.send(a["id"])["message"]
        self.assertEqual(self.store.conversation_messages(self.writer, b["id"])["messages"], [])
        self.assertEqual([x["id"] for x in self.store.conversation_messages(self.writer, a["id"])["messages"]], [msg["id"]])
        self.assertEqual([x["id"] for x in self.store.messages(self.writer)], [legacy["id"]])

    def test_vague_request_needs_information_without_search_evidence_or_task(self):
        item = self.create(); result = self.send(item["id"], "怎么办？")
        self.assertEqual(result["message"]["reply_type"], "needs_info")
        self.assertTrue(result["message"]["clarification_prompt"])
        self.assertEqual(result["message"]["matches"], [])
        self.assertEqual(result["conversation"]["state"], "waiting_for_info")
        with self.store.connection() as db: self.assertEqual(db.execute("SELECT count(*) FROM tasks").fetchone()[0], 0)

    def test_explicit_same_conversation_reply_uses_only_the_pending_question(self):
        item = self.create(); previous = self.send(item["id"], "怎么办")
        result = self.send(item["id"], "交付验收清单和用户培训记录", reply_to=previous["message"]["id"])
        self.assertEqual(result["message"]["clarification_for"], previous["message"]["id"])
        self.assertEqual(result["message"]["reply_type"], "evidence_excerpt")
        self.assertEqual(result["conversation"]["state"], "ready")

    def test_reply_cannot_reference_another_conversation_or_an_older_turn(self):
        a, b = self.create(), self.create(); parent = self.send(a["id"], "怎么办")["message"]
        self.problem("CLARIFICATION_CHANGED", self.store.ask_conversation, self.writer, b["id"], {"base_version": 1, "text": "验收材料", "reply_to": parent["id"]}, self.key())
        self.send(a["id"], "验收材料")
        current = self.store.get_conversation(self.writer, a["id"])
        self.problem("CLARIFICATION_CHANGED", self.store.ask_conversation, self.writer, a["id"], {"base_version": current["version"], "text": "验收材料", "reply_to": parent["id"]}, self.key())

    def test_new_question_does_not_implicitly_carry_previous_context(self):
        item = self.create(); self.send(item["id"], "怎么办")
        result = self.send(item["id"], "zxqv_no_matching_88219")
        self.assertIsNone(result["message"]["clarification_for"])
        self.assertEqual(result["message"]["reply_type"], "no_evidence")

    def test_duplicate_send_with_lost_response_does_not_create_two_messages(self):
        item = self.create(); payload = {"base_version": 1, "text": "验收需要哪些材料？"}; key = self.key()
        a = self.store.ask_conversation(self.writer, item["id"], payload, key)
        b = self.store.ask_conversation(self.writer, item["id"], payload, key)
        self.assertEqual(a["message"]["id"], b["message"]["id"])
        self.assertEqual(b["conversation"]["message_count"], 1)

    def test_stale_and_concurrent_pages_do_not_double_append(self):
        item = self.create(); payload = {"base_version": 1, "text": "验收材料"}
        def send(_):
            try: self.store.ask_conversation(self.writer, item["id"], payload, self.key()); return "ok"
            except Problem as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool: self.assertCountEqual(list(pool.map(send, range(2))), ["ok", "CONVERSATION_CONFLICT"])

    def test_rename_archive_and_old_retry_read_current_state(self):
        item = self.create(); key = self.key(); payload = {"base_version": 1, "title": "更名"}
        self.store.update_conversation(self.writer, item["id"], payload, key)
        self.store.update_conversation(self.writer, item["id"], {"base_version": 2, "status": "archived"}, self.key())
        current = self.store.update_conversation(self.writer, item["id"], payload, key)
        self.assertEqual(current["status"], "archived"); self.assertFalse(current["change_result"]["is_current"])
        self.problem("CONVERSATION_ARCHIVED", self.send, item["id"])
        self.store.update_conversation(self.writer, item["id"], {"base_version": 3, "status": "active"}, self.key())
        self.assertEqual(self.send(item["id"])["conversation"]["message_count"], 1)

    def test_message_pagination_is_chronological_stable_and_bounded(self):
        item = self.create()
        for number in range(25): self.send(item["id"], "验收材料 " + str(number))
        latest = self.store.conversation_messages(self.writer, item["id"])
        older = self.store.conversation_messages(self.writer, item["id"], latest["next_cursor"])
        self.assertEqual([x["sequence"] for x in latest["messages"]], list(range(6, 26)))
        self.assertEqual([x["sequence"] for x in older["messages"]], list(range(1, 6)))
        self.assertIsNone(older["next_cursor"])
        self.problem("INVALID_CURSOR", self.store.conversation_messages, self.writer, item["id"], "invalid")

    def test_replay_and_history_recheck_withdrawn_evidence(self):
        item = self.create(); payload = {"base_version": 1, "text": "验收清单用户培训记录", "scope": "delivery"}; key = self.key()
        first = self.store.ask_conversation(self.writer, item["id"], payload, key)
        source = self.store.get_source(self.admin, "delivery")
        self.store.withdraw_source(self.admin, "delivery", {"base_version": source["version"]}, self.key())
        replay = self.store.ask_conversation(self.writer, item["id"], payload, key)
        self.assertEqual(replay["message"]["matches"], []); self.assertGreater(replay["message"]["unavailable_count"], 0)
        self.assertEqual(replay["message"]["reply_type"], "no_evidence")
        self.assertEqual(first["message"]["id"], replay["message"]["id"])

    def test_unpublished_scope_is_rejected_without_storing_a_turn(self):
        item = self.create()
        self.problem("NOT_FOUND", self.store.ask_conversation, self.writer, item["id"], {"base_version": 1, "text": "周报", "scope": "weekly"}, self.key())
        self.assertEqual(self.store.get_conversation(self.writer, item["id"])["message_count"], 0)

    def test_clarification_is_not_an_answer_feedback_target(self):
        item = self.create(); msg = self.send(item["id"], "怎么办")["message"]
        self.problem("FEEDBACK_NOT_APPLICABLE", self.store.submit_feedback, self.writer, msg["id"], {"rating": "helpful", "reason": "useful"}, self.key())

    def test_disabled_account_loses_conversation_read_and_cached_replay(self):
        key = self.key(); payload = {"title": "仅本人"}; item = self.store.create_conversation(self.writer, payload, key)
        with self.store.connection(write=True) as db: db.execute("UPDATE users SET enabled=0 WHERE id='u_writer'")
        self.problem("SESSION_EXPIRED", self.store.get_conversation, self.writer, item["id"])
        self.problem("SESSION_EXPIRED", self.store.create_conversation, self.writer, payload, key)

    def test_store_restart_and_idempotent_migration_keep_old_messages(self):
        legacy = self.store.ask(self.writer, {"question": "验收材料"}, self.key())
        item = self.create(); self.send(item["id"])
        reloaded = Store(self.store.path)
        self.assertEqual(reloaded.get_conversation(self.writer, item["id"])["message_count"], 1)
        self.assertEqual(reloaded.messages(self.writer)[0]["id"], legacy["id"])

    def test_conversation_quota_is_per_owner_and_archive_does_not_reclaim_it(self):
        first = self.create()
        self.store.update_conversation(self.writer, first["id"], {"base_version": 1, "status": "archived"}, self.key())
        for _ in range(49): self.create()
        self.problem("CONVERSATION_LIMIT", self.create)
        self.assertEqual(len(self.store.list_conversations(self.writer)), 50)
        self.assertEqual(self.create(user=self.reader)["message_count"], 0)

    def test_message_limit_preserves_the_last_turn_and_idempotent_retry(self):
        item = self.create()
        for _ in range(99): self.send(item["id"], "无依据数据xyz")
        key = self.key(); payload = {"text": "最后一条", "base_version": 100}
        last = self.store.ask_conversation(self.writer, item["id"], payload, key)
        self.assertEqual(last["message"]["sequence"], 100)
        self.problem("MESSAGE_LIMIT", self.send, item["id"])
        self.assertEqual(self.store.ask_conversation(self.writer, item["id"], payload, key)["message"]["id"], last["message"]["id"])

    def test_unknown_context_and_invalid_cursors_do_not_change_conversation(self):
        item = self.create()
        self.problem("INVALID_INPUT", self.store.create_conversation, self.writer, {"user_id": self.admin["id"]}, self.key())
        for change in ({"artifact_id": "proposal"}, {"reply_to": True}, {"scope": []}, {"base_version": True}):
            self.problem("INVALID_INPUT", lambda: self.send(item["id"], **change))
        for cursor in (True, 0, -1, 1.5, "01", "1000000", "abc"):
            self.problem("INVALID_CURSOR", self.store.conversation_messages, self.writer, item["id"], cursor)
        self.assertEqual(self.store.get_conversation(self.writer, item["id"])["version"], 1)

    def test_http_conversation_routes_and_csrf(self):
        app = Application(self.store); token, user = self.store.login("writer", self.passwords["writer"])
        headers = {"Host": app.host, "Origin": app.origin, "Cookie": app.cookie_name + "=" + token, "Content-Type": "application/json", "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key()}
        prefix = "/api/workbench/conversations"
        response = app.handle("POST", prefix, headers, b"{}")
        self.assertEqual(response.status, 200); cid = json.loads(response.body)["data"]["id"]
        response = app.handle("GET", prefix + "/" + cid + "/messages", headers, b"")
        self.assertEqual(response.status, 200)
        headers["Idempotency-Key"] = self.key()
        response = app.handle("POST", prefix + "/" + cid + "/messages", headers, json.dumps({"text": "怎么办", "base_version": 1}).encode())
        self.assertEqual(json.loads(response.body)["data"]["message"]["reply_type"], "needs_info")
        headers["X-CSRF-Token"] = "wrong"
        self.assertEqual(app.handle("PATCH", prefix + "/" + cid, headers, b"{}").status, 403)
