import io
import json
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from document_import import ImportProblem, parse_document
from store import Store


def pdf_file(pages=("Fictional delivery acceptance evidence.",), password=None, active=False):
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for body in pages:
        page = writer.add_blank_page(width=500, height=700)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        if body:
            stream = DecodedStreamObject()
            safe = body.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            stream.set_data(("BT /F1 12 Tf 20 650 Td (" + safe + ") Tj ET").encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    if active:
        writer.add_js("app.alert('fictional test')")
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


class PDFImportTests(unittest.TestCase):
    def problem(self, code, raw):
        with self.assertRaises(ImportProblem) as error:
            parse_document("fictional.pdf", raw)
        self.assertEqual(error.exception.code, code)

    def test_text_pdf_has_physical_page_numbers_and_separate_page_sections(self):
        result = parse_document("fictional.PDF", pdf_file(("Fictional first-page acceptance.", "Fictional second-page training.")))
        self.assertEqual([item["page_number"] for item in result["sections"]], [1, 2])
        self.assertTrue(all(item["fragment_number"] == 1 for item in result["sections"]))
        self.assertIn("first-page", result["sections"][0]["text"])
        self.assertIn("second-page", result["sections"][1]["text"])
        self.assertTrue(any("版式" in value for value in result["warnings"]))

    def test_blank_pdf_is_not_successful_knowledge(self):
        self.problem("PDF_NO_TEXT", pdf_file(("", "")))

    def test_mixed_blank_page_warns_without_inventing_evidence(self):
        result = parse_document("mixed.pdf", pdf_file(("Acceptance text.", "", "Training evidence.")))
        self.assertEqual([item["page_number"] for item in result["sections"]], [1, 3])
        self.assertTrue(any("2" in value and "未提取" in value for value in result["warnings"]))

    def test_encrypted_pdf_is_rejected(self):
        self.problem("PDF_ENCRYPTED", pdf_file(password="fictional-test-only"))

    def test_javascript_actions_are_rejected_not_executed(self):
        self.problem("UNSAFE_PDF", pdf_file(active=True))

    def test_embedded_file_is_rejected(self):
        writer = PdfWriter(io.BytesIO(pdf_file()))
        writer.add_attachment("fictional.txt", b"not executed")
        output = io.BytesIO()
        writer.write(output)
        self.problem("UNSAFE_PDF", output.getvalue())

    def test_corrupt_pdf_or_mismatched_extension_is_rejected(self):
        self.problem("INVALID_PDF", b"not a PDF")
        self.problem("INVALID_PDF", b"%PDF-1.7\ninvalid")

    def test_page_and_text_limits(self):
        self.problem("PDF_PAGE_LIMIT", pdf_file(tuple("" for _ in range(101))))
        self.problem("TEXT_TOO_LARGE", pdf_file(("x" * 40001,)))

    def test_long_page_fragments_do_not_fabricate_new_pages(self):
        result = parse_document("long.pdf", pdf_file(("x" * 5000,)))
        self.assertEqual([item["page_number"] for item in result["sections"]], [1, 1])
        self.assertEqual([item["fragment_number"] for item in result["sections"]], [1, 2])

    def test_parser_deadline_and_crash_are_safe_errors(self):
        with patch("pdf_import.subprocess.run", side_effect=subprocess.TimeoutExpired("parser", 15)):
            self.problem("PDF_TIMEOUT", pdf_file())
        with patch("pdf_import.subprocess.run", return_value=subprocess.CompletedProcess([], 1, b"", b"private path must never be echoed")):
            self.problem("PDF_RESOURCE_LIMIT", pdf_file())

    def test_pdf_ingestion_evidence_uses_exact_page_not_section_pseudopage(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            store = Store(Path(temp) / "test.db")
            store.bootstrap({name: secrets.token_urlsafe(24) for name in ("writer", "reviewer", "admin")}, json.loads((ROOT / "fixtures.json").read_text()))
            writer, reader, admin = {"id": "u_writer", "role": "employee"}, {"id": "u_reviewer", "role": "employee"}, {"id": "u_admin", "role": "admin"}
            key = lambda: secrets.token_hex(16)
            raw = pdf_file(("First page fictional acceptance.", "Second page fictional acceptance evidence."))
            job = store.enqueue_import(writer, "fictional.pdf", raw, key())
            store.claim_import()
            store.finish_import(job["id"], parse_document("fictional.pdf", raw))
            sid = store.commit_import(writer, job["id"], {"title": "Fictional PDF", "category_id": "cat_other"}, key())["source_id"]
            source = store.get_source(writer, sid)
            request = store.request_publication(writer, sid, {"base_version": source["version"], "scope": "internal", "admin_id": "u_admin"}, key())
            store.decide_publication(admin, request["id"], {"decision": "approved"}, key())
            answer = store.ask(reader, {"question": "second page acceptance", "scope": sid}, key())
            match = next(item for item in answer["matches"] if item["section_index"] == 1)
            self.assertEqual(match["locator"], "第 2 页 / 片段 1")
            resolved = store.get_evidence(reader, answer["id"], match["evidence_id"])
            self.assertEqual(resolved["section"]["page_number"], 2)
            self.assertEqual(store.source_file(reader, sid)[0], raw)


if __name__ == "__main__":
    unittest.main()
