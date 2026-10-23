"""Synthetic-only tests. No real UYAP records, passwords or document bytes."""
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
import zipfile

from audit_archive_zip import audit_zip, inspect_central_directory


def sample_zip(members, compression=zipfile.ZIP_DEFLATED):
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", compression=compression) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    return buff.getvalue()


class ArchiveZipSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "UYAP_Arsiv"
        self.root.mkdir()
        self.dbfile = self.root / "_archive.db"
        with closing(sqlite3.connect(self.dbfile)) as db:
            # Legacy schema deliberately has no extension column.
            db.execute("""CREATE TABLE documents (
                status TEXT, document_id TEXT, case_id TEXT, display_name TEXT,
                local_path TEXT, size INTEGER, sha256 TEXT
            )""")
            db.commit()

    def tearDown(self):
        self.temp.cleanup()

    def register(self, id_, filename, data, *, parent=None, sha=None,
                 status="VERIFIED"):
        folder = parent or self.root
        folder.mkdir(exist_ok=True, parents=True)
        path = folder / filename
        path.write_bytes(data)
        with closing(sqlite3.connect(self.dbfile)) as db:
            db.execute(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                (status, id_, "2026/100", "Synthetic attachment",
                 str(path), len(data), sha or hashlib.sha256(data).hexdigest())
            )
            db.commit()
        return path

    def test_safe_legacy_verified_zip(self):
        data = sample_zip([("evrak.txt", b"only fake contents")])
        self.register("id1", "attachment.zip", data)
        report = audit_zip(str(self.root))
        self.assertTrue(report["ok"])
        self.assertEqual(1, report["counts"]["zipCandidates"])
        self.assertEqual(1, report["counts"]["statuses"]["SAFE_CENTRAL_DIRECTORY"], report["counts"])
        self.assertTrue(report["noExtraction"])
        self.assertTrue(report["noMemberDecompression"])
        text = json.dumps(report)
        self.assertNotIn("attachment.zip", text)
        self.assertNotIn("2026/100", text)
        self.assertNotIn(str(self.root), text)

    def test_final_package_zip_excluded_even_if_verified(self):
        payload = sample_zip([("x.txt", b"a")])
        final = self.root / "_final" / "Final_Paket_2026"
        self.register("internal", "archive-part-001.zip", payload, parent=final)
        self.register("real", "court_ek.zip", payload)
        counts = audit_zip(str(self.root))["counts"]
        self.assertEqual(1, counts["zipCandidates"])
        self.assertEqual(1, counts["internalExcluded"])

    def test_traversal_and_windows_backslash_rejected(self):
        for path in ("../secrets.txt", "safe/../../secret.txt",
                     "/abs/root.txt", "C:/Windows/secret.txt",
                     r"nested\..\secret.txt"):
            with self.subTest(path=path):
                payload = sample_zip([(path, b"fake")])
                self.assertEqual(
                    "ZIP_UNSAFE_MEMBER_PATH",
                    inspect_central_directory(payload),
                )

    def test_duplicate_member_names_rejected(self):
        data = sample_zip([("x.txt", b"one"), ("X.txt", b"two")])
        self.assertEqual(
            "ZIP_DUPLICATE_MEMBER_NAME", inspect_central_directory(data)
        )

    def test_zip_bomb_ratio_rejected_without_decompression(self):
        data = sample_zip([("huge.txt", b"X" * 500_000)])
        self.assertEqual("ZIP_EXPANSION_RISK", inspect_central_directory(data))

    def test_source_sha_mismatch_cannot_pass_metadata_scan(self):
        data = sample_zip([("file.txt", b"fake")])
        self.register("id1", "record.zip", data, sha=hashlib.sha256(b"DIFFERENT").hexdigest())
        statuses = audit_zip(str(self.root))["counts"]["statuses"]
        self.assertEqual(1, statuses["SOURCE_HASH_MISMATCH"], statuses)
        self.assertEqual(0, statuses["SAFE_CENTRAL_DIRECTORY"])

    def test_external_path_is_rejected(self):
        data = sample_zip([("file.txt", b"fake")])
        self.register("id1", "outside.zip", data, parent=Path(self.temp.name))
        statuses = audit_zip(str(self.root))["counts"]["statuses"]
        self.assertEqual(1, statuses["SOURCE_MISSING_OR_UNSAFE"])

    def test_corrupted_zip_is_not_safe_even_if_database_hash_agrees(self):
        broken = b"PK\x03\x04garbage"
        self.register("id1", "bad.zip", broken)
        result = audit_zip(str(self.root))["counts"]["statuses"]
        self.assertEqual(1, result["ZIP_MALFORMED"], result)
        self.assertEqual(0, result["SAFE_CENTRAL_DIRECTORY"])

    def test_unverified_document_not_scanned(self):
        data = sample_zip([("fake.txt", b"fake")])
        self.register("id1", "pending.zip", data, status="QUEUED")
        result = audit_zip(str(self.root))["counts"]
        self.assertEqual(0, result["zipCandidates"])

    def test_zip_symlink_blocked(self):
        payload = io.BytesIO()
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr(link, "secret.txt")
        self.assertEqual(
            "ZIP_SYMLINK_MEMBER", inspect_central_directory(payload.getvalue())
        )

    def test_unrecognized_compression_rejected(self):
        data = sample_zip([("fake.txt", b"abc")], compression=zipfile.ZIP_BZIP2)
        self.assertEqual(
            "ZIP_UNSUPPORTED_COMPRESSION", inspect_central_directory(data)
        )


if __name__ == "__main__":
    unittest.main()
