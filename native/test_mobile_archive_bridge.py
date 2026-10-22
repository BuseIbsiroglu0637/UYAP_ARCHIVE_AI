"""Synthetic-data-only tests for the local read-only mobile bridge."""
import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from mobile_archive_bridge import find_archive_root, make_handler, list_visible, verified_pdf


class MobileBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "UYAP_Arsiv"
        self.root.mkdir()
        self.pdf = self.root / "sentetik-2026_100.pdf"
        self.payload = b"%PDF-1.4\n% synthetic dummy\n1 0 obj <<>> endobj\n%%EOF\n"
        self.pdf.write_bytes(self.payload)
        self.sha = hashlib.sha256(self.payload).hexdigest()
        self.db = self.root / "_archive.db"
        with sqlite3.connect(self.db) as connection:
            connection.execute(
                """CREATE TABLE documents (
                    session_id TEXT, document_id TEXT, case_id TEXT,
                    display_name TEXT, institution_name TEXT,
                    local_path TEXT, size INTEGER, sha256 TEXT,
                    status TEXT, updated_at TEXT)"""
            )
            self.add_row(connection, "doc-1", self.pdf, self.sha, "VERIFIED")
            self.add_row(connection, "doc-2", self.pdf, self.sha, "INVALID")
        self.token = "S" * 43
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.root, self.token)
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)

    @staticmethod
    def add_row(connection, doc_id, path, sha, status):
        connection.execute(
            "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("test-session", doc_id, "2026/100", "Test.pdf",
             "Test Mahkemesi", str(path), path.stat().st_size,
             sha, status, "2026-10-22T00:00:00Z"),
        )

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.tmp.cleanup()

    def get(self, path, token=None):
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(self.url + path, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read()

    def test_explicit_archive_root(self):
        self.assertEqual(self.root, find_archive_root(str(self.root)))
        self.assertEqual(self.root, find_archive_root(self.tmp.name))

    def test_missing_or_wrong_token_rejected(self):
        for token in (None, "invalid"):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.get("/v1/documents", token)
            self.assertEqual(401, ctx.exception.code)

    def test_status_is_not_direct_uyap_api(self):
        status = json.loads(self.get("/v1/status", self.token))
        self.assertTrue(status["readOnly"])
        self.assertFalse(status["uyapDirectApi"])
        self.assertEqual("LOCAL_ARCHIVE_ONLY", status["mode"])

    def test_verified_document_is_listed_and_streamed(self):
        listed = json.loads(self.get("/v1/documents", self.token))
        self.assertEqual(1, len(listed["documents"]))
        doc = listed["documents"][0]
        self.assertEqual(self.sha, doc["sha256"])
        self.assertNotIn("local_path", doc)
        self.assertEqual(
            self.payload, self.get("/v1/document/" + doc["id"], self.token)
        )

    def test_changed_bytes_fail_integrity(self):
        doc = json.loads(self.get("/v1/documents", self.token))["documents"][0]
        self.pdf.write_bytes(self.payload.replace(b"synthetic", b"corrupted"))
        self.assertIsNone(verified_pdf(self.root, next(iter(self._verified_rows()))))
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/v1/document/" + doc["id"], self.token)
        self.assertEqual(404, ctx.exception.code)

    def _verified_rows(self):
        with sqlite3.connect(self.db) as connection:
            return connection.execute(
                "SELECT session_id, document_id, case_id, display_name,"
                " institution_name, local_path, size, sha256 FROM documents"
                " WHERE status='VERIFIED'"
            ).fetchall()

    def test_outside_root_path_never_exposed(self):
        outside = Path(self.tmp.name) / "other.pdf"
        outside.write_bytes(self.payload)
        with sqlite3.connect(self.db) as connection:
            self.add_row(connection, "external", outside, self.sha, "VERIFIED")
        docs = list_visible(self.root, b"testkey")
        self.assertEqual(1, len(docs))


if __name__ == "__main__":
    unittest.main()
