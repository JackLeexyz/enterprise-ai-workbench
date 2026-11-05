import io
import json
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from application import Application
from document_import import ImportProblem, MAX_FILE_BYTES, parse_document
from ingestion_worker import IngestionWorker
from store import Problem, Store


def word_file(link=False):
    document = Document()
    document.add_heading("交付准备", 1)
    document.add_paragraph("应保留实施说明、功能验收清单与问题记录。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "材料", "责任人"
    table.cell(1, 0).text, table.cell(1, 1).text = "验收清单", "待确认"
    document.add_paragraph("随后完成培训与客户确认。")
    if link:
        document.part.relate_to("https://invalid.example/private", RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    result = io.BytesIO()
    document.save(result)
    return result.getvalue()


def multipart(filename, content, boundary="test_boundary_403"):
    return (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode() + content + f"\r\n--{boundary}--\r\n".encode(), "multipart/form-data; boundary=" + boundary)


class ParserTests(unittest.TestCase):
    def problem(self, code, filename, content):
        with self.assertRaises(ImportProblem) as error:
            parse_document(filename, content)
        self.assertEqual(error.exception.code, code)

    def test_utf8_markdown_and_utf16_text(self):
        result = parse_document("交付.md", "# 交付准备\n准备验收清单。".encode())
        self.assertIn("验收清单", result["sections"][0]["text"])
        result = parse_document("交付.txt", "准备培训记录。".encode("utf-16"))
        self.assertIn("培训记录", result["sections"][0]["text"])

    def test_word_paragraph_table_order_and_real_locators(self):
        result = parse_document("交付.docx", word_file())
        text = "\n".join(section["text"] for section in result["sections"])
        self.assertLess(text.index("实施说明"), text.index("材料"))
        self.assertLess(text.index("责任人"), text.index("随后"))
        self.assertIn("表1", " ".join(section["title"] for section in result["sections"]))
        self.assertNotIn("第 1 页", json.dumps(result, ensure_ascii=False))

    def test_empty_corrupt_and_binary_text_are_errors(self):
        self.problem("NO_TEXT", "empty.txt", b" \n\t")
        self.problem("INVALID_DOCX", "broken.docx", b"not a zip")
        self.problem("BINARY_TEXT", "binary.txt", b"abc\x00def")

    def test_macro_filename_paths_and_unsupported_types_are_rejected(self):
        for name in ("macro.docm", "archive.zip", "../safe.txt", "a/b.txt", "a\\b.txt", "a\r\n.txt"):
            self.problem("UNSUPPORTED_FILE", name, b"safe")

    def test_size_text_and_archive_expansion_limits(self):
        self.problem("FILE_TOO_LARGE", "large.txt", b"a" * (MAX_FILE_BYTES + 1))
        self.problem("TEXT_TOO_LARGE", "long.txt", b"a" * 40001)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", b"A" * 500000)
        self.problem("DOCX_ARCHIVE_LIMIT", "bomb.docx", output.getvalue())

    def test_active_content_and_entities_are_rejected(self):
        entity = '<!DOCTYPE x [<!ENTITY s SYSTEM "file:///private/file">]><x>&s;</x>'
        for filename, content in [("word/vbaProject.bin", b"macro"), ("word/embeddings/object.bin", b"object"), ("customXml/item.xml", entity.encode()), ("customXml/item.xml", entity.encode("utf-16"))]:
            output = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(word_file())) as original, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target:
                for item in original.infolist():
                    target.writestr(item.filename, original.read(item.filename))
                target.writestr(filename, content)
            self.problem("UNSAFE_DOCX", "unsafe.docx", output.getvalue())

    def test_unusual_zip_compression_is_rejected(self):
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(word_file())) as original, zipfile.ZipFile(output, "w", zipfile.ZIP_BZIP2) as target:
            for item in original.infolist():
                target.writestr(item.filename, original.read(item.filename))
        self.problem("UNSAFE_DOCX", "compressed.docx", output.getvalue())

    def test_external_links_are_not_requested_or_executed(self):
        content = word_file(link=True)
        with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
            result = parse_document("link.docx", content)
        self.assertTrue(any("外部链接" in warning for warning in result["warnings"]))


class ImportFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = tempfile.TemporaryDirectory(prefix="import-tests-", dir=Path(__file__).parent)
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
        self.writer, self.reviewer, self.admin = ({"id": "u_" + name, "role": "admin" if name == "admin" else "employee"} for name in ("writer", "reviewer", "admin"))
        self.worker = IngestionWorker(self.store)
        self.app = Application(self.store)
        self.sessions = {}

    def tearDown(self):
        self.worker.stop()
        self.temp.cleanup()

    def key(self):
        return secrets.token_hex(16)

    def request(self, method, path, body=None, name="writer", raw=None, extra=None):
        headers = {"Host": self.app.host}
        if name:
            if name not in self.sessions:
                self.sessions[name] = self.store.login(name, self.passwords[name])
            token, user = self.sessions[name]
            headers.update({"Cookie": self.app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"]})
        if method != "GET":
            headers.update({"Origin": self.app.origin, "Content-Type": "application/json", "Idempotency-Key": self.key()})
        headers.update(extra or {})
        payload = raw if raw is not None else json.dumps(body or {}).encode() if method != "GET" else b""
        response = self.app.handle(method, "/api/workbench" + path, headers, payload)
        content = json.loads(response.body) if response.headers.get("Content-Type", "").startswith("application/json") else response.body
        return response, content

    def problem(self, code, fn, *args):
        with self.assertRaises(Problem) as error:
            fn(*args)
        self.assertEqual(error.exception.code, code)

    def upload(self, content=None, user=None, key=None):
        return self.store.enqueue_import(user or self.writer, "交付.docx", content or word_file(), key or self.key())

    def preview(self):
        result = self.upload()
        self.assertTrue(self.worker.run_once())
        return self.store.get_import(self.writer, result["id"])

    def commit(self, job):
        return self.store.commit_import(self.writer, job["id"], {"title": "新导入交付资料", "category": "实施指南", "label": "v1.0"}, self.key())

    def test_upload_preview_and_confirmation_are_separate_states(self):
        queued = self.upload()
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(len(self.store.list_sources(self.writer)), 5)
        self.assertTrue(self.worker.run_once())
        parsed = self.store.get_import(self.writer, queued["id"])
        self.assertEqual(parsed["status"], "preview")
        self.assertTrue(parsed["sections"])
        self.assertEqual(len(self.store.list_sources(self.writer)), 5)
        saved = self.commit(parsed)
        source = self.store.get_source(self.writer, saved["source_id"])
        self.assertEqual((source["scope"], source["publication"]), ("private", "draft"))
        self.assertEqual(source["attachment"]["filename"], "交付.docx")
        self.assertEqual(Store(self.store.path).get_import(self.writer, queued["id"])["status"], "committed")

    def test_job_and_original_file_are_authorized(self):
        job = self.preview()
        for user in (self.reviewer, self.admin):
            self.problem("NOT_FOUND", self.store.get_import, user, job["id"])
            self.assertEqual(self.store.list_imports(user), [])
        source = self.commit(job)
        self.problem("NOT_FOUND", self.store.source_file, self.reviewer, source["source_id"])
        content, filename = self.store.source_file(self.writer, source["source_id"])
        self.assertEqual(content[:2], b"PK")
        self.assertEqual(filename, "交付.docx")

    def test_duplicates_are_per_owner_and_do_not_create_extra_sources(self):
        key, content = self.key(), word_file()
        first = self.upload(content, key=key)
        repeat = self.upload(content, key=key)
        another_key = self.upload(content)
        self.assertEqual(first["id"], repeat["id"])
        self.assertEqual(first["id"], another_key["id"])
        other = self.upload(content, self.reviewer)
        self.assertNotEqual(other["id"], first["id"])
        self.assertFalse(other["duplicate"])
        self.worker.run_once()
        preview = self.store.get_import(self.writer, first["id"])
        one, two = self.commit(preview), self.commit(preview)
        self.assertEqual(one["source_id"], two["source_id"])
        self.assertEqual(len(self.store.list_sources(self.writer)), 6)

    def test_failure_has_no_partial_source_and_retry_is_explicit(self):
        job = self.upload(b"broken")
        self.worker.run_once()
        failed = self.store.get_import(self.writer, job["id"])
        self.assertEqual((failed["status"], failed["error_code"]), ("failed", "INVALID_DOCX"))
        self.assertEqual(failed["sections"], [])
        self.problem("IMPORT_NOT_READY", self.store.commit_import, self.writer, job["id"], {"title": "错误资料"}, self.key())
        self.store.retry_import(self.writer, job["id"], {}, self.key())
        self.assertEqual(self.store.get_import(self.writer, job["id"])["status"], "queued")
        self.assertEqual(len(self.store.list_sources(self.writer)), 5)

    def test_retries_stop_at_three_attempts(self):
        job = self.upload(b"broken")
        for attempt in range(1, 4):
            self.worker.run_once()
            self.assertEqual(self.store.get_import(self.writer, job["id"])["attempts"], attempt)
            if attempt < 3:
                self.store.retry_import(self.writer, job["id"], {}, self.key())
        self.problem("IMPORT_RETRY_BLOCKED", self.store.retry_import, self.writer, job["id"], {}, self.key())
        self.assertFalse(self.worker.run_once())

    def test_pending_and_byte_quotas_do_not_leak_or_delete_records(self):
        for index in range(5):
            self.store.enqueue_import(self.writer, f"file{index}.txt", f"text {index}".encode(), self.key())
        self.problem("IMPORT_QUOTA", self.store.enqueue_import, self.writer, "six.txt", b"six", self.key())
        self.assertEqual(len(self.store.list_imports(self.writer)), 5)
        own = self.store.enqueue_import(self.reviewer, "other.txt", b"other", self.key())
        with self.store.connection(write=True) as db:
            db.execute("UPDATE import_jobs SET file_size=? WHERE id=?", (20 * 1024 * 1024, own["id"]))
        self.problem("IMPORT_QUOTA", self.store.enqueue_import, self.reviewer, "more.txt", b"more", self.key())
        self.assertEqual(len(self.store.list_imports(self.reviewer)), 1)

    def test_worker_recovery_preserves_original_and_requires_manual_retry(self):
        job = self.upload()
        claimed = self.store.claim_import()
        self.assertEqual(claimed["id"], job["id"])
        self.assertIsNone(self.store.claim_import())
        self.store.recover_imports()
        interrupted = self.store.get_import(self.writer, job["id"])
        self.assertEqual(interrupted["status"], "interrupted")
        self.assertFalse(self.worker.run_once())
        self.store.retry_import(self.writer, job["id"], {}, self.key())
        self.assertTrue(self.worker.run_once())

    def test_publication_and_withdrawal_control_original_download(self):
        source_id = self.commit(self.preview())["source_id"]
        request = self.store.request_publication(self.writer, source_id, {"base_version": 1, "admin_id": self.admin["id"], "scope": "internal"}, self.key())
        self.store.source_file(self.admin, source_id)
        self.store.decide_publication(self.admin, request["id"], {"decision": "approved"}, self.key())
        self.assertEqual(self.store.source_file(self.reviewer, source_id)[0][:2], b"PK")
        source = self.store.get_source(self.writer, source_id)
        self.store.withdraw_source(self.admin, source_id, {"base_version": source["version"]}, self.key())
        self.problem("NOT_FOUND", self.store.source_file, self.reviewer, source_id)
        self.problem("NOT_FOUND", self.store.get_source, self.reviewer, source_id)
        self.assertEqual(self.store.source_file(self.writer, source_id)[0][:2], b"PK")

    def test_confirmed_import_is_not_answerable_until_published(self):
        sid = self.commit(self.preview())["source_id"]
        reply = self.store.ask(self.writer, {"question": "验收清单", "scope": sid}, self.key())
        self.assertEqual(reply["matches"], [])

    def test_multipart_api_accepts_chinese_filename_and_enforces_csrf(self):
        app = Application(self.store)
        token, user = self.store.login("writer", self.passwords["writer"])
        payload, content_type = multipart("中文资料.docx", word_file())
        headers = {"Host": app.host, "Origin": app.origin, "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key(), "Content-Type": content_type}
        result = app.handle("POST", "/api/workbench/imports", headers, payload)
        self.assertEqual(result.status, 202, result.body.decode())
        job = json.loads(result.body)["data"]
        self.assertEqual(job["filename"], "中文资料.docx")
        rejected = app.handle("POST", "/api/workbench/imports", {**headers, "X-CSRF-Token": "bad"}, payload)
        self.assertEqual(rejected.status, 403)
        self.assertEqual(len(self.store.list_imports(self.writer)), 1)

    def test_multipart_rejects_missing_multiple_and_oversized_files(self):
        app = Application(self.store)
        token, user = self.store.login("writer", self.passwords["writer"])
        headers = {"Host": app.host, "Origin": app.origin, "Cookie": app.cookie_name + "=" + token, "X-CSRF-Token": user["csrf"], "Idempotency-Key": self.key()}
        first, content_type = multipart("one.txt", b"one")
        second, _ = multipart("two.txt", b"two")
        multiple = first.replace(b"--test_boundary_403--\r\n", second)
        for payload, content_type in [(b"", "multipart/form-data"), (multiple, content_type), multipart("too-large.txt", b"a" * (MAX_FILE_BYTES + 1))]:
            result = app.handle("POST", "/api/workbench/imports", {**headers, "Content-Type": content_type}, payload)
            self.assertIn(result.status, (400, 413, 422))
        self.assertEqual(self.store.list_imports(self.writer), [])

    def test_larger_upload_allowance_does_not_expand_json_endpoints(self):
        payload, content_type = multipart("large.txt", b"a" * (600 * 1024))
        response, _ = self.request("POST", "/imports", raw=payload, extra={"Content-Type": content_type})
        self.assertEqual(response.status, 202)
        response, _ = self.request("POST", "/sources", raw=b"a" * (600 * 1024))
        self.assertEqual(response.status, 413)
        self.assertEqual(Application.request_limit("POST", "/api/workbench/imports"), MAX_FILE_BYTES + 64 * 1024)
        self.assertEqual(Application.request_limit("POST", "/api/workbench/imports/anything"), 512 * 1024)

    def test_upload_to_reviewed_export_and_withdrawal_through_application(self):
        original = word_file()
        payload, content_type = multipart("中文交付资料.docx", original)
        response, uploaded = self.request("POST", "/imports", raw=payload, extra={"Content-Type": content_type})
        self.assertEqual(response.status, 202)
        jid = uploaded["data"]["id"]
        response, _ = self.request("POST", f"/imports/{jid}/confirm", {"title": "交付依据"})
        self.assertEqual(response.status, 409)
        self.worker.run_once()
        response, preview = self.request("GET", f"/imports/{jid}")
        self.assertEqual(preview["data"]["status"], "preview")
        response, listing = self.request("GET", "/imports")
        self.assertNotIn("file_bytes", listing["data"][0])
        self.assertNotIn("sections", listing["data"][0])
        response, _ = self.request("GET", f"/imports/{jid}", name="reviewer")
        self.assertEqual(response.status, 404)
        response, saved = self.request("POST", f"/imports/{jid}/confirm", {"title": "交付依据", "category": "实施指南", "label": "v1"})
        self.assertEqual(response.status, 200, saved)
        sid = saved["data"]["source_id"]
        response, source = self.request("GET", f"/sources/{sid}")
        self.assertEqual(source["data"]["attachment"]["filename"], "中文交付资料.docx")
        for name, expected in [(None, 401), ("reviewer", 404), ("admin", 404)]:
            response, _ = self.request("GET", f"/sources/{sid}/file", name=name)
            self.assertEqual(response.status, expected)
        response, data = self.request("GET", f"/sources/{sid}/file")
        self.assertEqual(data, original)
        self.assertIn("filename*=UTF-8''%", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        response, publication = self.request("POST", f"/sources/{sid}/publication-requests", {"base_version": 1, "admin_id": self.admin["id"], "scope": "internal"})
        rid = publication["data"]["id"]
        response, _ = self.request("POST", f"/admin/publications/{rid}/decision", {"decision": "approved"}, name="admin")
        self.assertEqual(response.status, 200)
        response, answer = self.request("POST", "/messages", {"question": "验收清单", "scope": sid})
        self.assertTrue(answer["data"]["matches"])
        response, created = self.request("POST", "/artifacts", {"title": "依据导入文件起草", "source_ids": [sid], "template": "delivery", "fields": {"subject": "演示客户", "content": "依据导入材料交付"}})
        aid = created["data"]["id"]
        response, review = self.request("POST", f"/artifacts/{aid}/reviews", {"base_revision": 1, "reviewer_id": self.reviewer["id"]})
        response, _ = self.request("POST", f'/reviews/{review["data"]["id"]}/decision', {"revision": 1, "decision": "approved", "note": "核对导入原件"}, name="reviewer")
        self.assertEqual(response.status, 200)
        response, exported = self.request("POST", f"/artifacts/{aid}/exports", {"revision": 1})
        eid = exported["data"]["id"]
        response, binary = self.request("GET", f"/exports/{eid}/download")
        self.assertEqual(response.status, 200)
        self.assertEqual(binary[:2], b"PK")
        response, source = self.request("GET", f"/sources/{sid}", name="admin")
        response, _ = self.request("POST", f"/sources/{sid}/withdraw", {"base_version": source["data"]["version"]}, name="admin")
        self.assertEqual(response.status, 200)
        response, _ = self.request("GET", f"/sources/{sid}/file", name="reviewer")
        self.assertEqual(response.status, 404)
        response, _ = self.request("GET", f"/exports/{eid}/download")
        self.assertEqual(response.status, 422)

    def test_background_thread_parses_without_http_listener(self):
        job = self.upload()
        self.assertTrue(self.worker.start())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            current = self.store.get_import(self.writer, job["id"])
            if current["status"] == "preview":
                break
            time.sleep(0.02)
        self.assertEqual(current["status"], "preview")

    def test_worker_start_failure_releases_file_lock(self):
        with patch.object(self.store, "recover_imports", side_effect=RuntimeError("test failure")):
            with self.assertRaises(RuntimeError):
                self.worker.start()
        other = IngestionWorker(self.store)
        try:
            self.assertTrue(other.start())
        finally:
            other.stop()

    def test_only_one_background_worker_owns_the_store(self):
        self.assertTrue(self.worker.start())
        other = IngestionWorker(self.store)
        try:
            self.assertFalse(other.start())
        finally:
            other.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
