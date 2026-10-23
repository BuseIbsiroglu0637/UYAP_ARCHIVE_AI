#!/usr/bin/env python3
"""Privacy-preserving, strictly read-only diagnosis of an existing UYAP_Arsiv DB.

Outputs NO private paths, record names, case IDs, row values, passwords or hashes.
Unlike audit_real_archive.py this checks folder and DB *schema only*. It never
transfers documents and does not claim any individual evrak was verified.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3

REQUIRED = frozenset({
    "document_id", "case_id", "display_name", "local_path",
    "extension", "size", "sha256", "status"
})


def diagnose(selection: str) -> dict:
    result = {
        "schemaVersion": 1,
        "readOnly": True,
        "documentsRead": False,
        "documentsTransferred": False,
        "selectedFolder": False,
        "archiveFolder": False,
        "databaseFile": False,
        "sqliteHeader": False,
        "sqliteOpened": False,
        "documentsTable": False,
        "columnCount": 0,
        "missingRequiredColumns": sorted(REQUIRED),
        "status": "SELECTED_FOLDER_NOT_FOUND",
        "ok": False,
    }
    try:
        selected = Path(selection).expanduser()
        if not selected.is_dir():
            return result
        result["selectedFolder"] = True
        root = selected if selected.name.casefold() == "uyap_arsiv" else selected / "UYAP_Arsiv"
        if not root.is_dir():
            result["status"] = "ARCHIVE_FOLDER_NOT_FOUND"
            return result
        result["archiveFolder"] = True
        db_file = root / "_archive.db"
        if not db_file.is_file():
            result["status"] = "ARCHIVE_DB_NOT_FOUND"
            return result
        if db_file.is_symlink():
            result["status"] = "ARCHIVE_DB_SYMLINK_REJECTED"
            return result
        result["databaseFile"] = True
        with db_file.open("rb") as stream:
            header = stream.read(16)
        if header != b"SQLite format 3\x00":
            result["status"] = "ARCHIVE_DB_NOT_SQLITE"
            return result
        result["sqliteHeader"] = True
        uri = db_file.resolve(strict=True).as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
            conn.execute("PRAGMA query_only=ON")
            rows = conn.execute("PRAGMA table_info(documents)").fetchall()
        result["sqliteOpened"] = True
        columns = {str(row[1]) for row in rows}
        result["documentsTable"] = bool(columns)
        result["columnCount"] = len(columns)
        result["missingRequiredColumns"] = sorted(REQUIRED - columns)
        if not columns:
            result["status"] = "DOCUMENTS_TABLE_NOT_FOUND"
        elif result["missingRequiredColumns"]:
            result["status"] = "DOCUMENTS_SCHEMA_MISMATCH"
        else:
            result["status"] = "SCHEMA_COMPATIBLE"
            result["ok"] = True
        return result
    except (OSError, ValueError, sqlite3.Error) as exc:
        # Never serialize exception details: they may contain private locations.
        result["status"] = "READ_ONLY_DB_OPEN_FAILED"
        result["errorType"] = type(exc).__name__
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Inspect local archive DB presence and schema only; no personal data."
    )
    parser.add_argument(
        "--archive-root", required=True,
        help="Selected UYAP_Arsiv folder or containing folder",
    )
    args = parser.parse_args(argv)
    print(json.dumps(diagnose(args.archive_root), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
