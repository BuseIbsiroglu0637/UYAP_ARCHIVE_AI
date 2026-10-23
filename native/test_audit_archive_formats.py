"""No real-case fixtures; tests never read, copy or emit document bytes."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from audit_archive_formats import inspect_formats, main


class FormatInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "UYAP_Arsiv"
        self.root.mkdir()
        self.db = self.root / "_archive.db"

    def tearDown(self):
        self.temp.cleanup()

    def make_table(self, legacy=False):
        fields = """status TEXT, display_name TEXT, local_path TEXT"""
        if not legacy:
            fields += ", extension TEXT"
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE documents (" + fields + ")")
            conn.commit()

    def add(self, status, name, path, extension=None, legacy=False):
        with closing(sqlite3.connect(self.db)) as conn:
            if legacy:
                conn.execute(
                    "INSERT INTO documents VALUES (?, ?, ?)",
                    (status, name, path)
                )
            else:
                conn.execute(
                    "INSERT INTO documents VALUES (?, ?, ?, ?)",
                    (status, name, path, extension)
                )
            conn.commit()

    def test_legacy_extension_fallback_reconciles_counts(self):
        self.make_table(legacy=True)
        self.add("VERIFIED", "Private family matter", r"C:\private\doc.pdf", legacy=True)
        self.add("VERIFIED", "Another private item", r"C:\private\document.udf", legacy=True)
        self.add("VERIFIED", "Yet another item", r"C:\private\extra.zip", legacy=True)
        self.add("VERIFIED", "Office attachment", r"C:\private\extra.html", legacy=True)
        self.add("VERIFIED", "Weird extension", r"C:\private\secret.abcdef", legacy=True)
        self.add("VERIFIED", "No extension", r"C:\private\truncated", legacy=True)
        self.add("VERIFIED", "_manifest", r"C:\private\_manifest.json", legacy=True)
        self.add("PENDING", "private note", r"C:\private\other.zip", legacy=True)
        out = inspect_formats(str(self.root))
        counts = out["counts"]
        self.assertEqual(8, counts["records"])
        self.assertEqual(7, counts["verified"])
        self.assertEqual(1, counts["notVerified"])
        self.assertEqual(2, counts["supportedBySuffixOnly"])
        self.assertEqual(1, counts["internalExcluded"])
        self.assertEqual(4, counts["unsupportedBySuffix"])
        self.assertEqual(1, counts["unsupportedByFormat"]["ZIP"])
        self.assertEqual(1, counts["unsupportedByFormat"]["HTML"])
        self.assertEqual(1, counts["unsupportedByFormat"]["OTHER"])
        self.assertEqual(1, counts["unsupportedByFormat"]["MISSING_EXTENSION"])
        self.assertTrue(out["noDocumentContentRead"])
        self.assertTrue(out["noDocumentTransfer"])
        text = json.dumps(out)
        for private in ["private", "doc.pdf", "secret.abcdef", "family matter"]:
            self.assertNotIn(private, text)
        self.assertNotIn(str(self.root), text)

    def test_db_extension_conflict_does_not_claim_supported_pdf(self):
        self.make_table()
        self.add("VERIFIED", "Mismatch", "/test/strange.zip", ".pdf")
        self.add("VERIFIED", "Correct", "/test/normal.pdf", ".pdf")
        counts = inspect_formats(str(self.root))["counts"]
        self.assertEqual(1, counts["supportedBySuffixOnly"])
        self.assertEqual(1, counts["unsupportedByFormat"]["EXTENSION_CONFLICT"])

    def test_archive_final_pack_zip_is_not_counted_as_court_zip(self):
        self.make_table(legacy=True)
        self.add("VERIFIED", "zip bundle",
                 r"C:\archive\_final\archive-part-001.zip", legacy=True)
        self.add("VERIFIED", "real zip attachment",
                 r"C:\archive\folder\ek_10.zip", legacy=True)
        counts = inspect_formats(str(self.root))["counts"]
        self.assertEqual(1, counts["internalExcluded"])
        self.assertEqual(1, counts["unsupportedByFormat"]["ZIP"])

    def test_unsupported_file_labels_are_closed_vocabulary(self):
        self.make_table(legacy=True)
        self.add("VERIFIED", "Very sensitive title",
                 "/archive/family_case/private.privateclue", legacy=True)
        raw = json.dumps(inspect_formats(str(self.root)))
        self.assertNotIn("privateclue", raw)
        self.assertNotIn("sensitive", raw)
        self.assertEqual(1, inspect_formats(str(self.root))["counts"]["unsupportedByFormat"]["OTHER"])

    def test_missing_db_raises_for_cli_generic_error(self):
        output = None
        with self.assertRaises(ValueError):
            inspect_formats(str(self.root))


if __name__ == "__main__":
    unittest.main()
