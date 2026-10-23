#!/usr/bin/env python3
"""Read-only, privacy-safe format inventory for existing UYAP Archive AI archives.

Classifies VERIFIED records by a FIXED allowlist of file extensions, never emits
paths, document IDs, user-defined labels, hashes, or actual document contents.
ZIP here means candidate attachment by filename ONLY, not integrity proof.
No UYAP network operations, files created, or Android transfers.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from archive_document_policy import archive_kind, resolved_extension
from audit_real_archive import MAX_AUDIT_ROWS

# Fixed labels prevent document-derived or unusually crafted "extensions"
# becoming user-visible data. Bucket totals include no individual paths.
FORMAT_BUCKETS = {
    ".zip": "ZIP",
    ".html": "HTML", ".htm": "HTML",
    ".json": "JSON", ".csv": "CSV", ".xml": "XML",
    ".txt": "TXT", ".rtf": "RTF",
    ".doc": "DOC", ".docx": "DOCX",
    ".xls": "XLS", ".xlsx": "XLSX",
    ".ppt": "PPT", ".pptx": "PPTX",
    ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
    ".gif": "GIF", ".bmp": "BMP", ".webp": "WEBP",
    ".msg": "MSG", ".eml": "EML",
    ".mp4": "MP4", ".mp3": "MP3",
}
SORTED_FORMAT_LABELS = tuple(sorted(set(FORMAT_BUCKETS.values()) |
                                      {"OTHER", "MISSING_EXTENSION", "EXTENSION_CONFLICT"}))


def select_root(selected: str) -> Path:
    root = Path(selected).expanduser().resolve(strict=True)
    if root.name.casefold() != "uyap_arsiv":
        root = (root / "UYAP_Arsiv").resolve(strict=True)
    db = root / "_archive.db"
    if (not root.is_dir() or db.is_symlink() or not db.is_file() or
            db.resolve(strict=True).parent != root):
        raise ValueError("No trusted UYAP_Arsiv/_archive.db root")
    return root


def unsupported_bucket(recorded: str | None, source_path: str, resolved: str) -> str:
    if recorded is not None and str(recorded).strip() and not resolved:
        return "EXTENSION_CONFLICT"
    if not resolved:
        return "MISSING_EXTENSION"
    return FORMAT_BUCKETS.get(resolved, "OTHER")


def inspect_formats(selected: str) -> dict:
    root = select_root(selected)
    dbfile = root / "_archive.db"
    uri = dbfile.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(documents)")}
        required = {"status", "local_path", "display_name"}
        if not required.issubset(columns):
            raise ValueError("Incompatible documents table")
        ext = "extension" if "extension" in columns else "NULL AS extension"
        rows = db.execute(
            "SELECT status, display_name, local_path, " + ext +
            " FROM documents LIMIT ?", (MAX_AUDIT_ROWS + 1,)
        ).fetchall()
    if len(rows) > MAX_AUDIT_ROWS:
        raise ValueError("Too many records")

    unsupported = Counter({k: 0 for k in SORTED_FORMAT_LABELS})
    total = len(rows)
    unverified = 0
    supported = 0
    internal = 0
    other = 0
    for status, name, path, recorded in rows:
        if status != "VERIFIED":
            unverified += 1
            continue
        resolved = resolved_extension(recorded, path)
        category = archive_kind(resolved, path, name)
        if category == "INTERNAL":
            internal += 1
        elif category.startswith("DOCUMENT_"):
            supported += 1
        elif category == "UNSUPPORTED":
            unsupported[unsupported_bucket(recorded, path, resolved)] += 1
        else:
            other += 1

    counts = {
        "records": total, "verified": total - unverified,
        "notVerified": unverified,
        "supportedBySuffixOnly": supported,
        "internalExcluded": internal,
        "unsupportedBySuffix": sum(unsupported.values()),
        "unsupportedByFormat": {k: unsupported[k] for k in SORTED_FORMAT_LABELS},
        "unclassified": other,
    }
    if (counts["verified"] != supported + internal +
            counts["unsupportedBySuffix"] + other):
        raise ValueError("Format breakdown inconsistent")
    return {
        "ok": True, "schemaVersion": 1,
        "mode": "READ_ONLY_FORMAT_INVENTORY",
        "dataSource": "LOCAL_SQLITE_METADATA_ONLY",
        "noDocumentContentRead": True,
        "noDocumentTransfer": True, "noUyapConnection": True,
        "counts": counts,
        "notes": [
            "Extension-only classification is not format, signature or integrity verification.",
            "ZIP counts are candidates only; nested content was not extracted or inspected.",
            "Only fixed type labels and aggregate numbers are reported; no paths or case information."
        ]
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only UYAP archive format breakdown; no personal data in output"
    )
    parser.add_argument("--archive-root", required=True)
    args = parser.parse_args(argv)
    try:
        report = inspect_formats(args.archive_root)
    except (sqlite3.Error, OSError, ValueError):
        # Never expose source paths or any exception's possibly personal contents.
        print(json.dumps({"ok": False, "error": "FORMAT_AUDIT_NOT_AVAILABLE"}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
