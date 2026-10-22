"""Synthetic only. Real case document bytes must never enter repository fixtures."""
from contextlib import closing
import hashlib
import io
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

from archive_package_adapter import (
    VerifiedArchivePackage, make_archive_package_handler
)


def sample_udf():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("content.xml", "<template format_id='2'><content>sentetik</content></template>")
    return buffer.getvalue()


PDF = b"%PDF-1.4\n% dummy document\n%%EOF"
UDF = sample_udf()
TIF = b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00"


def build_package(target, entries, extra_members=()):
    """entries: (document ID, format extension, bytes, status, SHA override)."""
    db = sqlite3.connect(":memory:")
    try:
        db.execute("""CREATE TABLE documents (
            document_id TEXT, source_system TEXT, source_identity_hash TEXT,
            case_id TEXT, institution_name TEXT, display_name TEXT,
            local_path TEXT, extension TEXT, size INTEGER,
            sha256 TEXT, status TEXT
        )""")
        for id_, ext, payload, status, override in entries:
            name = "original_" + str(id_) + ext
            db.execute(
                "INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (id_, "UYAP", "identity-" + str(id_), "2026/100",
                 "Test Mahkemesi", name, "/old/windows/PC/" + name,
                 ext, len(payload), override or hashlib.sha256(payload).hexdigest(),
                 status),
            )
        db.commit()
        raw_db = db.serialize()
    finally:
        db.close()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Synthetic/UYAP_Arsiv/_archive.db", raw_db)
        for id_, ext, payload, status, override in entries:
            archive.writestr("Synthetic/UYAP_Arsiv/original_" + str(id_) + ext, payload)
        for name, payload in extra_members:
            archive.writestr(name, payload)


class ArchivePackageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "synthetic.zip"
        self.data = [("a", ".pdf", PDF, "VERIFIED", None),
                     ("b", ".udf", UDF, "VERIFIED", None),
                     ("c", ".tif", TIF, "VERIFIED", None)]
        build_package(self.file, self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_three_formats_with_preserved_source_identity(self):
        docs = VerifiedArchivePackage(str(self.file)).documents
        self.assertEqual(["PDF", "TIF", "UDF"], sorted(d.metadata()["format"] for d in docs))
        self.assertEqual(3, len({d.source_key for d in docs}))
        self.assertTrue(all(d.case_label == "2026/100" for d in docs))
        self.assertTrue(all("zip_member" not in d.metadata() for d in docs))

    def test_open_verified_bytes_are_unchanged(self):
        package = VerifiedArchivePackage(str(self.file))
        by_format = {d.extension: d for d in package.documents}
        self.assertEqual(PDF, package.bytes_for(by_format[".pdf"].source_key)[1])
        self.assertEqual(UDF, package.bytes_for(by_format[".udf"].source_key)[1])
        self.assertEqual(TIF, package.bytes_for(by_format[".tif"].source_key)[1])

    def test_identity_is_stable_across_scans(self):
        a = VerifiedArchivePackage(str(self.file))
        b = VerifiedArchivePackage(str(self.file))
        self.assertEqual([d.source_key for d in a.documents],
                         [d.source_key for d in b.documents])

    def test_verified_false_is_ignored(self):
        build_package(self.file, self.data + [
            ("d", ".pdf", PDF, "INVALID", None)
        ])
        self.assertEqual(3, len(VerifiedArchivePackage(str(self.file)).documents))

    def test_corrupted_byte_sha_rejected(self):
        build_package(self.file, [("a", ".pdf", PDF, "VERIFIED", "0" * 64)])
        self.assertEqual([], VerifiedArchivePackage(str(self.file)).documents)

    def test_same_bytes_different_source_identity_is_not_collapsed(self):
        build_package(self.file, [("a", ".pdf", PDF, "VERIFIED", None),
                                  ("b", ".pdf", PDF, "VERIFIED", None)])
        docs = VerifiedArchivePackage(str(self.file)).documents
        self.assertEqual(2, len(docs))
        self.assertEqual(docs[0].sha256, docs[1].sha256)
        self.assertNotEqual(docs[0].source_key, docs[1].source_key)

    def test_reject_zip_path_traversal(self):
        build_package(self.file, self.data, [("../escape.txt", b"bad")])
        with self.assertRaises(ValueError):
            VerifiedArchivePackage(str(self.file))

    def test_reject_duplicate_zip_path(self):
        build_package(self.file, self.data, [
            ("Synthetic/UYAP_Arsiv/original_a.pdf", b"duplicate")
        ])
        with self.assertRaises(ValueError):
            VerifiedArchivePackage(str(self.file))

    def test_source_modified_after_scan_refuses_transfer(self):
        package = VerifiedArchivePackage(str(self.file))
        key = package.documents[0].source_key
        build_package(self.file, [("a", ".pdf", b"%PDF-1.4\nCHANGED", "VERIFIED", None)])
        self.assertIsNone(package.bytes_for(key))

    def test_authenticated_http_lists_and_delivers_original(self):
        package = VerifiedArchivePackage(str(self.file))
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_archive_package_handler(package, "S" * 43))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = "http://127.0.0.1:" + str(server.server_port)
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + "/v2/documents", timeout=4)
            self.assertEqual(cm.exception.code, 401)
            def get(path):
                req = urllib.request.Request(base + path, headers={
                    "Authorization": "Bearer " + "S" * 43
                })
                with urllib.request.urlopen(req, timeout=4) as response:
                    return response.read()
            status = json.loads(get("/v2/status"))
            self.assertFalse(status["uyapDirectApi"])
            result = json.loads(get("/v2/documents?limit=2&offset=0"))
            self.assertEqual(2, len(result["documents"]))
            self.assertTrue(result["hasMore"])
            rest = json.loads(get("/v2/documents?limit=2&offset=2"))
            self.assertEqual(1, len(rest["documents"]))
            for document in result["documents"] + rest["documents"]:
                key = document["sourceKey"]
                payload = get("/v2/document/" + key)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), document["sha256"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
