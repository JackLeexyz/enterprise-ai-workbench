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
from store import Problem, Store, dump


class EvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="evidence-tests-", dir=Path(__file__).parent)
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
        self.writer, self.reviewer, self.admin = [next(user for user in self.store.users() if user["username"] == name) for name in ("writer", "reviewer", "admin")]

    def tearDown(self):
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def problem(self, code, operation, *args):
        with self.assertRaises(Problem) as caught:
            operation(*args)
        self.assertEqual(caught.exception.code, code)

    def ask(self, question="交付验收", **extra):
        return self.store.ask(self.writer, {"question": question, **extra}, self.key())

    def feedback(self, message=None, **extra):
        return self.store.submit_feedback(self.writer, (message or self.ask())["id"], {"rating": "unhelpful", "reason": "missing", "note": "仅自己可见的备注", **extra}, self.key())

    def source(self, name, scope="internal", sections=None):
        item = self.store.create_source(self.writer, {"title": name, "text": "专用检索标识 evidenceunit"}, self.key())
        request = self.store.request_publication(self.writer, item["id"], {"base_version": 1, "admin_id": self.admin["id"], "scope": scope}, self.key())
        self.store.decide_publication(self.admin, request["id"], {"decision": "approved"}, self.key())
        if sections:
            with self.store.connection(write=True) as db:
                db.execute("UPDATE sources SET sections_json=? WHERE id=?", (dump(sections), item["id"]))
        return self.store.get_source(self.writer, item["id"])

    def request(self, method, endpoint, body=None, user="writer", extra=None):
        app = Application(self.store)
        token, session = self.store.login(user, self.passwords[user])
        headers = {"Host": app.host, "Cookie": app.cookie_name + "=" + token, "Origin": app.origin, "X-CSRF-Token": session["csrf"], "Content-Type": "application/json", "Idempotency-Key": self.key(), **(extra or {})}
        response = app.handle(method, "/api/workbench" + endpoint, headers, json.dumps(body or {}).encode() if method == "POST" else b"")
        return response.status, json.loads(response.body)

    def test_exact_section_and_full_text_not_only_excerpt(self):
        source = self.source("定位测试", sections=[{"title": "无关章节", "text": "其他内容"}, {"title": "目标章节", "text": "evidenceunit" + "正文" * 500}])
        message = self.ask("evidenceunit", scope=source["id"])
        ref = message["matches"][0]
        self.assertEqual(len(ref["excerpt"]), 700)
        evidence = self.store.get_evidence(self.writer, message["id"], ref["evidence_id"])
        self.assertEqual(evidence["section_index"], 1)
        self.assertEqual(evidence["section"]["text"], source["sections"][1]["text"])
        self.assertEqual(evidence["version_id"], source["version_id"])

    def test_locator_cannot_be_opened_by_other_employee_or_admin(self):
        message = self.ask()
        ref = message["matches"][0]
        for user in (self.reviewer, self.admin):
            self.problem("NOT_FOUND", self.store.get_evidence, user, message["id"], ref["evidence_id"])

    def test_withdrawn_evidence_hidden_from_history_and_endpoint(self):
        message = self.ask()
        ref = message["matches"][0]
        source = self.store.get_source(self.admin, ref["source_id"])
        self.store.withdraw_source(self.admin, source["id"], {"base_version": source["version"]}, self.key())
        self.problem("NOT_FOUND", self.store.get_evidence, self.writer, message["id"], ref["evidence_id"])
        fresh = self.store.messages(self.writer, message["id"])[0]
        self.assertNotIn(ref["evidence_id"], [item["evidence_id"] for item in fresh["matches"]])
        self.assertGreaterEqual(fresh["unavailable_count"], 1)

    def test_acl_change_revokes_even_saved_reference(self):
        source = self.source("权限测试")
        message = self.store.ask(self.reviewer, {"question": "evidenceunit", "scope": source["id"]}, self.key())
        with self.store.connection(write=True) as db:
            db.execute("UPDATE sources SET scope='private' WHERE id=?", (source["id"],))
        self.problem("NOT_FOUND", self.store.get_evidence, self.reviewer, message["id"], message["matches"][0]["evidence_id"])

    def test_saved_reference_does_not_retarget_new_version_or_changed_text(self):
        for change in ("version", "text", "title", "order"):
            with self.subTest(change=change):
                source = self.source("冻结版本" + change, sections=[{"title": "目标", "text": "evidenceunit"}, {"title": "其他", "text": "无关"}])
                message = self.ask("evidenceunit", scope=source["id"])
                with self.store.connection(write=True) as db:
                    sections = source["sections"]
                    if change == "version":
                        db.execute("UPDATE sources SET version_id=? WHERE id=?", ("ver_changed_" + change, source["id"]))
                    else:
                        if change == "text": sections[0]["text"] += "已变化"
                        if change == "title": sections[0]["title"] += "已变化"
                        if change == "order": sections.reverse()
                        db.execute("UPDATE sources SET sections_json=? WHERE id=?", (dump(sections), source["id"]))
                self.problem("NOT_FOUND", self.store.get_evidence, self.writer, message["id"], message["matches"][0]["evidence_id"])
                self.assertEqual(self.store.messages(self.writer, message["id"])[0]["matches"], [])

    def test_reference_identity_stays_stable_when_earlier_match_disappears(self):
        first, second = self.source("同词一"), self.source("同词二")
        message = self.ask("evidenceunit")
        self.assertEqual(len(message["matches"]), 2)
        lost, retained = message["matches"]
        source = self.store.get_source(self.writer, lost["source_id"])
        self.store.withdraw_source(self.admin, source["id"], {"base_version": source["version"]}, self.key())
        remaining = self.store.messages(self.writer, message["id"])[0]["matches"]
        self.assertEqual([ref["evidence_id"] for ref in remaining], [retained["evidence_id"]])
        self.assertEqual(self.store.get_evidence(self.writer, message["id"], retained["evidence_id"])["source_id"], retained["source_id"])

    def test_legacy_locator_without_fingerprint_requires_new_query(self):
        message = self.ask()
        with self.store.connection(write=True) as db:
            refs = [{key: ref[key] for key in ("source_id", "version_id", "locator")} for ref in message["matches"]]
            db.execute("UPDATE messages SET refs_json=? WHERE id=?", (dump(refs), message["id"]))
        fresh = self.store.messages(self.writer, message["id"])[0]
        self.assertEqual(fresh["matches"], [])
        self.assertEqual(fresh["unavailable_count"], len(refs))

    def test_unknown_or_mismatched_reference_never_returns_source(self):
        message = self.ask()
        self.problem("NOT_FOUND", self.store.get_evidence, self.writer, message["id"], "ev_" + "0" * 64)
        self.problem("NOT_FOUND", self.store.get_evidence, self.writer, "missing", message["matches"][0]["evidence_id"])

    def test_one_feedback_per_message_with_idempotent_replay(self):
        message = self.ask()
        body = {"rating": "helpful", "reason": "useful", "note": ""}
        key = self.key()
        first = self.store.submit_feedback(self.writer, message["id"], body, key)
        self.assertEqual(first, self.store.submit_feedback(self.writer, message["id"], body, key))
        self.problem("FEEDBACK_EXISTS", self.store.submit_feedback, self.writer, message["id"], body, self.key())
        self.assertEqual(self.store.messages(self.writer, message["id"])[0]["feedback"]["id"], first["id"])

    def test_feedback_cannot_be_written_for_someone_elses_message(self):
        message = self.ask()
        self.problem("NOT_FOUND", self.store.submit_feedback, self.reviewer, message["id"], {"rating": "helpful", "reason": "useful"}, self.key())

    def test_feedback_validation_and_note_limits(self):
        message = self.ask()
        for body in ({"rating": "bad", "reason": "other"}, {"rating": "helpful", "reason": "missing"}, {"rating": "unhelpful", "reason": "useful"}, {"rating": "unhelpful", "reason": "invented"}, {"rating": "unhelpful", "reason": "other", "note": "x" * 501}):
            self.problem("INVALID_INPUT", self.store.submit_feedback, self.writer, message["id"], body, self.key())

    def test_concurrent_feedback_creates_one_record(self):
        message = self.ask()
        def submit(_):
            try: return self.feedback(message)["id"]
            except Problem as error: return error.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, range(4)))
        self.assertEqual(results.count("FEEDBACK_EXISTS"), 3)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feedback").fetchone()[0], 1)

    def test_admin_feedback_metadata_never_includes_question_private_note_or_text(self):
        source = self.source("内部独有资料标题", scope="private")
        message = self.ask("evidenceunit 私有问题标记", scope=source["id"])
        feedback = self.feedback(message, note="私有备注标记")
        encoded = json.dumps(self.store.admin_feedback(self.admin), ensure_ascii=False)
        for private in ("私有问题标记", "私有备注标记", source["title"], "专用检索标识", message["id"], source["id"]):
            self.assertNotIn(private, encoded)
        self.assertIn(feedback["id"], encoded)
        self.problem("ADMIN_REQUIRED", self.store.admin_feedback, self.writer)
        audit = json.dumps(self.store.admin_summary(self.admin), ensure_ascii=False)
        self.assertNotIn("私有备注标记", audit)

    def test_admin_triage_and_owner_readback(self):
        feedback = self.feedback()
        self.store.update_feedback(self.admin, feedback["id"], {"base_version": 1, "status": "in_progress", "resolution": "已受理"}, self.key())
        self.store.update_feedback(self.admin, feedback["id"], {"base_version": 2, "status": "closed", "resolution": "已记录复现条件，仍未接模型"}, self.key())
        own = self.store.messages(self.writer)[-1]["feedback"]
        self.assertEqual((own["status"], own["version"]), ("closed", 3))
        self.assertIn("仍未接模型", own["resolution"])
        self.assertEqual(own["note"], "仅自己可见的备注")
        self.problem("ADMIN_REQUIRED", self.store.update_feedback, self.writer, feedback["id"], {"base_version": 3, "status": "closed"}, self.key())

    def test_triage_conflict_and_terminal_state(self):
        feedback = self.feedback()
        self.store.update_feedback(self.admin, feedback["id"], {"base_version": 1, "status": "in_progress"}, self.key())
        self.problem("VERSION_CONFLICT", self.store.update_feedback, self.admin, feedback["id"], {"base_version": 1, "status": "closed", "resolution": "完成"}, self.key())
        self.problem("INVALID_INPUT", self.store.update_feedback, self.admin, feedback["id"], {"base_version": 2, "status": "closed"}, self.key())
        self.store.update_feedback(self.admin, feedback["id"], {"base_version": 2, "status": "closed", "resolution": "已核对"}, self.key())
        self.problem("VERSION_CONFLICT", self.store.update_feedback, self.admin, feedback["id"], {"base_version": 3, "status": "in_progress"}, self.key())

    def test_feedback_allowed_after_evidence_withdrawal_without_copying_body(self):
        message = self.ask()
        for ref in message["matches"]:
            source = self.store.get_source(self.admin, ref["source_id"])
            if source["publication"] == "published":
                self.store.withdraw_source(self.admin, source["id"], {"base_version": source["version"]}, self.key())
        feedback = self.feedback(message, reason="locator")
        self.assertEqual(feedback["answer_type"], "no_evidence")

    def test_upgrade_is_idempotent_and_preserves_existing_rows(self):
        message = self.ask()
        self.feedback(message)
        Store(self.store.path)
        Store(self.store.path)
        with self.store.connection() as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("SELECT count(*) FROM feedback").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT count(*) FROM sources").fetchone()[0], 5)

    def test_old_database_upgrade_adds_only_empty_feedback_table(self):
        self.ask()
        with self.store.connection(write=True) as db:
            db.execute("DROP TABLE feedback")
            original = {name: [tuple(row) for row in db.execute(f'SELECT * FROM "{name}"')] for name in ("sources", "artifacts", "revisions", "messages")}
        Store(self.store.path)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM feedback").fetchone()[0], 0)
            for name, rows in original.items():
                self.assertEqual([tuple(row) for row in db.execute(f'SELECT * FROM "{name}"')], rows)

    def test_two_admin_updates_cannot_overwrite_same_version(self):
        feedback = self.feedback()
        def update(_):
            try:
                return self.store.update_feedback(self.admin, feedback["id"], {"base_version": 1, "status": "closed", "resolution": "已归档"}, self.key())["status"]
            except Problem as error:
                return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, range(2)))
        self.assertEqual(sorted(results), ["VERSION_CONFLICT", "closed"])

    def test_new_api_routes_auth_csrf_contract_and_live_readback(self):
        message = self.ask()
        endpoint = f"/messages/{message['id']}/evidence/{message['matches'][0]['evidence_id']}"
        status, result = self.request("GET", endpoint)
        self.assertEqual(status, 200)
        self.assertEqual(result["data"]["version_id"], message["matches"][0]["version_id"])
        self.assertEqual(self.request("GET", endpoint, user="admin")[0], 404)
        body = {"rating": "unhelpful", "reason": "irrelevant"}
        self.assertEqual(self.request("POST", f"/messages/{message['id']}/feedback", body, extra={"X-CSRF-Token": "bad"})[0], 403)
        status, result = self.request("POST", f"/messages/{message['id']}/feedback", body)
        self.assertEqual(status, 200)
        fid = result["data"]["id"]
        self.assertEqual(self.request("GET", "/admin/feedback")[0], 403)
        self.assertEqual(self.request("POST", f"/admin/feedback/{fid}/status", {"base_version": 1, "status": "closed", "resolution": "已受理记录"}, user="admin")[0], 200)
        status, result = self.request("GET", "/messages")
        self.assertEqual(result["data"][-1]["feedback"]["status"], "closed")

    def test_offline_benchmark_keeps_negative_results_and_no_runtime_database(self):
        from evaluate_retrieval import evaluate
        report = evaluate(ROOT / "tests/retrieval_cases.json", self.temp.name)
        self.assertEqual(report["case_count"], 21)
        self.assertEqual(report["model_calls"], 0)
        self.assertTrue(report["security_gate_passed"])
        self.assertEqual(sum(group["total"] for group in report["groups"].values()), 21)
        self.assertEqual(len(report["dataset_sha256"]), 64)
        self.assertEqual(report["contract_passed"], sum(item["contract_passed"] for item in report["cases"]))
        self.assertEqual({item["id"] for item in report["cases"]}, {f"q{number:02d}" for number in range(1, 22)})
        self.assertFalse(list(Path(self.temp.name).glob("keyword-eval-*")))


if __name__ == "__main__":
    unittest.main()
