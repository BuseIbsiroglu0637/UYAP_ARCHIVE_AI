"""Synthetic-only tests for v0.11, with Windows/Linux portable local paths."""
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
import io
import zipfile
from contextlib import closing

from archive_package_adapter import make_archive_package_handler
from create_mobile_live_demo import create
from live_archive_adapter import VerifiedLiveArchive


class LiveArchiveTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = create(str(Path(self.temp.name) / "demo"))
        self.adapter = VerifiedLiveArchive(str(self.root))
        self.db = self.root / "_archive.db"

    def tearDown(self):
        self.temp.cleanup()

    def test_three_real_export_schema_formats(self):
        docs = self.adapter.documents
        self.assertEqual(3, len(docs))
        self.assertEqual({"PDF", "UDF", "TIF"}, {
            d.metadata()["format"] for d in docs
        })
        for doc in docs:
            self.assertNotIn("zip_member", doc.metadata())
            self.assertNotIn("local_path", doc.metadata())
            self.assertEqual(doc.sha256,
                             hashlib.sha256(self.adapter.bytes_for(doc.source_key)[1]).hexdigest())

    def test_fingerprint_identity_is_stable_after_rescan(self):
        original = {d.source_key for d in self.adapter.documents}
        self.adapter.refresh()
        self.assertEqual(original, {d.source_key for d in self.adapter.documents})

    def test_new_verified_document_is_visible_without_new_zip(self):
        previous = len(self.adapter.documents)
        other = self.root / "new_sample.pdf"
        data = b"%PDF-1.4\n% new synthetic\n%%EOF"
        other.write_bytes(data)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(
                """INSERT INTO documents SELECT
                    'new-evrak', source_system, source_context,
                    institution_name, court_name, 'new-evrak',
                    'new-identity', source_fingerprint,
                    case_id, folder_names, document_date,
                    ?, uyap_path, target_path,
                    ?, '.pdf', ?, ?, verified_at, 'VERIFIED',
                    archive_part, archive_part_index FROM documents
                    WHERE document_id='sample-1'""",
                ("new_sample.pdf", str(other), len(data), hashlib.sha256(data).hexdigest())
            )
            db.commit()
        self.assertEqual(previous + 1, len(self.adapter.refresh()))

    def test_live_opaque_zip_available_without_extraction(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("evrak.txt", b"SYNTHETIC")
        data = output.getvalue()
        source = self.root / "synthetic_extra.zip"
        source.write_bytes(data)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(
                """INSERT INTO documents SELECT
                    'zip-new', source_system, source_context,
                    institution_name, court_name, 'zip-new',
                    'zip-identity', source_fingerprint,
                    case_id, folder_names, document_date,
                    ?, uyap_path, target_path,
                    ?, '.zip', ?, ?, verified_at, 'VERIFIED',
                    archive_part, archive_part_index FROM documents
                    WHERE document_id='sample-1'""",
                ("synthetic_extra.zip", str(source), len(data),
                 hashlib.sha256(data).hexdigest())
            )
            db.commit()
        items = self.adapter.refresh()
        one = next(x for x in items if x.extension == ".zip")
        self.assertEqual(data, self.adapter.bytes_for(one.source_key)[1])
        self.assertEqual("ZIP", one.metadata()["format"])

    def test_live_opaque_zip_unsafe_member_is_denied(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("../escape.txt", b"SYNTHETIC")
        data = output.getvalue()
        source = self.root / "synthetic_unsafe.zip"
        source.write_bytes(data)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(
                """INSERT INTO documents SELECT
                    'zip-bad', source_system, source_context,
                    institution_name, court_name, 'zip-bad',
                    'zip-bad-id', source_fingerprint,
                    case_id, folder_names, document_date,
                    ?, uyap_path, target_path,
                    ?, '.zip', ?, ?, verified_at, 'VERIFIED',
                    archive_part, archive_part_index FROM documents
                    WHERE document_id='sample-1'""",
                ("synthetic_unsafe.zip", str(source), len(data),
                 hashlib.sha256(data).hexdigest())
            )
            db.commit()
        one = next(x for x in self.adapter.refresh() if x.extension == ".zip")
        self.assertIsNone(self.adapter.bytes_for(one.source_key))

    def test_nonverified_records_never_appear(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE documents SET status='INVALID' WHERE document_id='sample-2'")
            db.commit()
        self.assertEqual(2, len(self.adapter.refresh()))

    def test_path_outside_archive_rejected(self):
        outside = Path(self.temp.name) / "private.pdf"
        outside.write_bytes((self.root / "synthetic_sample-1.pdf").read_bytes())
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE documents SET local_path=? WHERE document_id='sample-1'",
                       (str(outside),))
            db.commit()
        self.assertEqual(2, len(self.adapter.refresh()))

    def test_symlink_to_other_document_is_rejected(self):
        original = self.root / "synthetic_sample-1.pdf"
        outside = Path(self.temp.name) / "other.pdf"
        outside.write_bytes(original.read_bytes())
        original.unlink()
        try:
            original.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("OS does not allow test symlinks")
        self.assertEqual(2, len(self.adapter.refresh()))

    def test_old_verified_file_modified_fails_transfer(self):
        pdf = next(d for d in self.adapter.documents if d.extension == ".pdf")
        (self.root / "synthetic_sample-1.pdf").write_bytes(b"%PDF-1.4\n% corrupt bytes")
        self.assertIsNone(self.adapter.bytes_for(pdf.source_key))
        self.assertEqual(2, len(self.adapter.refresh()))

    def test_repeated_identity_fails_closed(self):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute(
                """INSERT INTO documents SELECT
                    document_id, source_system, source_context, institution_name,
                    court_name, source_document_id, source_identity_hash,
                    source_fingerprint, case_id, folder_names, document_date,
                    display_name, uyap_path, target_path, local_path, extension,
                    size, sha256, verified_at, status, archive_part, archive_part_index
                    FROM documents WHERE document_id='sample-1'"""
            )
            db.commit()
        with self.assertRaises(ValueError):
            self.adapter.refresh()

    def test_http_new_records_and_authorization(self):
        token = "T" * 43
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_archive_package_handler(self.adapter, token)
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        root = "http://127.0.0.1:" + str(server.server_port)
        def get(url, credential=True):
            req = urllib.request.Request(root + url, headers={
                "Authorization": "Bearer " + token
            } if credential else {})
            with urllib.request.urlopen(req, timeout=5) as result:
                return result.read()
        try:
            with self.assertRaises(urllib.error.HTTPError) as denied:
                get("/v2/documents", False)
            self.assertEqual(401, denied.exception.code)
            status = json.loads(get("/v2/status"))
            self.assertEqual("LOCAL_ARCHIVE_LIVE", status["mode"])
            self.assertFalse(status["uyapDirectApi"])
            before = json.loads(get("/v2/documents"))
            self.assertEqual(3, len(before["documents"]))
            first = before["documents"][0]
            self.assertEqual(first["sha256"], hashlib.sha256(
                get("/v2/document/" + first["sourceKey"])
            ).hexdigest())
            with closing(sqlite3.connect(self.db)) as db:
                db.execute("UPDATE documents SET status='INVALID' WHERE document_id='sample-2'")
                db.commit()
            after = json.loads(get("/v2/documents"))
            self.assertEqual(2, len(after["documents"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
