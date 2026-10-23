"""Synthetic tests for privacy-safe audit and artifact exclusions, Windows/Linux."""
from contextlib import closing
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from archive_document_policy import archive_kind
from audit_real_archive import audit
from create_mobile_live_demo import create
from live_archive_adapter import VerifiedLiveArchive
from mobile_archive_bridge import main as bridge_main


class ArchiveReadinessTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = create(str(Path(self.tmp.name) / "generated"))
        self.db_path = self.root / "_archive.db"

    def tearDown(self):
        self.tmp.cleanup()

    def add_row(self, doc_id, name, fullpath, payload=None, ext=".pdf",
                status="VERIFIED", sha=None):
        size = len(payload) if payload is not None else 20
        actual_sha = sha or hashlib.sha256(payload or b"not on disk").hexdigest()
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("""INSERT INTO documents (
                document_id, source_system, source_identity_hash, case_id,
                institution_name, display_name, local_path, extension, size,
                sha256, status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                doc_id, "UYAP", "identity-" + doc_id, "2026/100",
                "Test Court", name, str(fullpath), ext, size, actual_sha, status
            ))
            db.commit()

    def test_debug_bridge_refuses_an_unmarked_archive(self):
        marker = self.root / ".hukukarsivim-synthetic-only"
        self.assertTrue(marker.is_file())
        marker.unlink()
        with self.assertRaises(SystemExit) as denial:
            bridge_main(["--live-archive-root", str(self.root), "--port", "0"])
        self.assertIn("LIVE_BRIDGE_DEBUG_SYNTHETIC_ONLY", str(denial.exception))

    def test_three_verified_sources_are_proven_without_document_data_in_result(self):
        report = audit(str(self.root))
        counts = report["counts"]
        self.assertEqual(3, counts["records"])
        self.assertEqual(3, counts["verifiedInDatabase"])
        self.assertEqual(3, counts["provenOriginals"])
        self.assertEqual(1, counts["provenPDF"])
        self.assertEqual(1, counts["provenUDF"])
        self.assertEqual(1, counts["provenTIF"])
        self.assertTrue(report["noDocumentTransfer"])
        serialized = json.dumps(report)
        self.assertNotIn("synthetic_sample", serialized)
        self.assertNotIn("2026/100", serialized)
        self.assertNotIn(str(self.root), serialized)

    def test_management_metadata_excluded_even_if_db_says_verified(self):
        final = self.root / "_final" / "Final_Paket_20261013"
        final.mkdir(parents=True)
        pdf = final / "report.pdf"
        payload = b"%PDF-1.4\ninternal synthetic report\n%%EOF"
        pdf.write_bytes(payload)
        self.add_row("internal-1", "internal report", pdf, payload)
        result = audit(str(self.root))["counts"]
        self.assertEqual(1, result["internalExcluded"])
        self.assertEqual(3, result["provenOriginals"])
        self.assertEqual(3, len(VerifiedLiveArchive(str(self.root)).documents))

    def test_checksum_and_manifest_are_not_court_documents(self):
        self.assertEqual("INTERNAL", archive_kind(".pdf", "/archive/_final/a.pdf", "x"))
        self.assertEqual("INTERNAL", archive_kind(".zip", "C:\\a\\Final_Paket_10\\archive-part-001.zip", "x"))
        self.assertEqual("INTERNAL", archive_kind(".json", "/a/_manifest.json", "_manifest"))
        self.assertEqual("INTERNAL", archive_kind(".pdf", "/a/report.pdf", "_archive-report"))
        self.assertEqual("DOCUMENT_PDF", archive_kind(".pdf", "/a/evrak.pdf", "Evrak"))
        self.assertEqual("DOCUMENT_UDF", archive_kind(".udf", "/a/evrak.udf", "Evrak"))
        self.assertEqual("UNSUPPORTED", archive_kind(".zip", "/a/court-attachment.zip", "Ek"))

    def test_unsupported_zip_is_counted_but_not_exposed(self):
        file = self.root / "archive-attachment.zip"
        contents = b"PK\x03\x04dummy"
        file.write_bytes(contents)
        self.add_row("unsupported-1", "UYAP attachment", file, contents, ext=".zip")
        counts = audit(str(self.root))["counts"]
        self.assertEqual(1, counts["unsupportedExcluded"])
        self.assertEqual(3, counts["provenOriginals"])

    def test_wrong_hash_refused_despite_verified_db_status(self):
        target = self.root / "bad.pdf"
        payload = b"%PDF-1.4\nmy fake PDF\n%%EOF"
        target.write_bytes(payload)
        self.add_row("wrong-hash", "Bad", target, payload,
                     sha=hashlib.sha256(b"DIFFERENT").hexdigest())
        counts = audit(str(self.root))["counts"]
        self.assertEqual(1, counts["integrityFailed"])
        self.assertEqual(3, counts["provenOriginals"])

    def test_stale_or_external_path_is_not_proven(self):
        outside = Path(self.tmp.name) / "other.pdf"
        payload = b"%PDF-1.4\nexternal data\n%%EOF"
        outside.write_bytes(payload)
        self.add_row("external-1", "External", outside, payload)
        counts = audit(str(self.root))["counts"]
        self.assertEqual(1, counts["missingOrUnsafe"])
        self.assertEqual(3, counts["provenOriginals"])

    def test_unverified_records_never_proven(self):
        target = self.root / "unverified.pdf"
        payload = b"%PDF-1.4\nnot verified\n%%EOF"
        target.write_bytes(payload)
        self.add_row("unverified-1", "Pending", target, payload, status="QUEUED")
        counts = audit(str(self.root))["counts"]
        self.assertEqual(1, counts["notVerified"])
        self.assertEqual(3, counts["provenOriginals"])


if __name__ == "__main__":
    unittest.main()
