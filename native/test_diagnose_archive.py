"""Synthetic-only tests for path-independent privacy-safe DB diagnostics."""
from contextlib import closing
import sqlite3
import tempfile
import unittest
import json
from pathlib import Path

from diagnose_archive import REQUIRED, diagnose


class DiagnoseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name)
        self.root = self.parent / "UYAP_Arsiv"
        self.root.mkdir()
        self.db_file = self.root / "_archive.db"

    def tearDown(self):
        self.temp.cleanup()

    def make_db(self, fields):
        with closing(sqlite3.connect(self.db_file)) as db:
            ddl = ", ".join('"' + field + '" TEXT' for field in fields)
            db.execute("CREATE TABLE documents (" + ddl + ")")
            db.commit()

    def test_complete_schema_passes_without_reading_rows(self):
        self.make_db(sorted(REQUIRED))
        outcome = diagnose(str(self.parent))
        self.assertTrue(outcome["ok"])
        self.assertEqual("SCHEMA_COMPATIBLE", outcome["status"])
        self.assertEqual([], outcome["missingRequiredColumns"])
        self.assertFalse(outcome["documentsRead"])
        self.assertFalse(outcome["documentsTransferred"])
        self.assertTrue(outcome["readOnly"])
        self.assertNotIn(str(self.parent), json.dumps(outcome))

    def test_missing_table_is_reported_safely(self):
        with closing(sqlite3.connect(self.db_file)) as db:
            db.execute("CREATE TABLE other (id TEXT)")
            db.commit()
        outcome = diagnose(str(self.root))
        self.assertFalse(outcome["ok"])
        self.assertEqual("DOCUMENTS_TABLE_NOT_FOUND", outcome["status"])

    def test_columns_missing_reports_only_column_names(self):
        self.make_db(["document_id", "case_id"])
        outcome = diagnose(str(self.root))
        self.assertEqual("DOCUMENTS_SCHEMA_MISMATCH", outcome["status"])
        self.assertIn("sha256", outcome["missingRequiredColumns"])
        self.assertNotIn(str(self.root), json.dumps(outcome))

    def test_missing_database_is_explicit(self):
        self.assertEqual("ARCHIVE_DB_NOT_FOUND", diagnose(str(self.root))["status"])

    def test_corrupt_database_header_fails_closed(self):
        self.db_file.write_bytes(b"invalid database")
        outcome = diagnose(str(self.parent))
        self.assertEqual("ARCHIVE_DB_NOT_SQLITE", outcome["status"])
        self.assertFalse(outcome["ok"])

    def test_selected_folder_missing_is_explicit(self):
        outcome = diagnose(str(self.parent / "not-a-directory"))
        self.assertEqual("SELECTED_FOLDER_NOT_FOUND", outcome["status"])


if __name__ == "__main__":
    unittest.main()
