#!/usr/bin/env python3
"""LOCAL privacy-preserving readiness audit of a user-selected UYAP_Arsiv.

Reads a live SQLite DB and original file bytes without changing or uploading
anything. Outputs *aggregate counts only*, no paths, names, case numbers,
document IDs, hashes, or UYAP credentials. This is NOT mobile transfer approval.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3

from archive_document_policy import archive_kind
from archive_package_adapter import EXTENSIONS, HASH_PATTERN, MAX_DOCUMENT_BYTES

MAX_AUDIT_ROWS = 50000
BUFFER = 64 * 1024


def safe_local_file(root: Path, raw: str, extension: str, expected_size: int):
    try:
        candidate = Path(raw)
        if not candidate.is_absolute() or candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        if (resolved == root or not resolved.is_relative_to(root) or
                not resolved.is_file() or resolved.suffix.lower() != extension or
                resolved.stat().st_size != expected_size):
            return None
        parent = candidate.parent
        while parent != root:
            if parent.is_symlink() or not parent.is_relative_to(root):
                return None
            parent = parent.parent
        return resolved
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def matching_bytes(path: Path, expected_size: int, sha: str, extension: str) -> bool:
    total = 0
    digest = hashlib.sha256()
    try:
        with path.open("rb") as fp:
            magic = fp.read(64)
            fp.seek(0)
            while chunk := fp.read(BUFFER):
                total += len(chunk)
                if total > MAX_DOCUMENT_BYTES or total > expected_size:
                    return False
                digest.update(chunk)
    except (OSError, ValueError):
        return False
    if extension == ".pdf" and not magic.startswith(b"%PDF-"):
        return False
    if extension in (".tif", ".tiff") and not magic.startswith(
        (b"II*\x00", b"MM\x00*")
    ):
        return False
    if extension == ".udf" and not magic.lstrip().startswith(
        (b"PK\x03\x04", b"<?xml", b"<template")
    ):
        return False
    return total == expected_size and hmac.compare_digest(digest.hexdigest(), sha)


def audit(selected: str) -> dict:
    root = Path(selected).expanduser().resolve(strict=True)
    if root.name != "UYAP_Arsiv":
        root = (root / "UYAP_Arsiv").resolve(strict=True)
    db_path = root / "_archive.db"
    if (not root.is_dir() or db_path.is_symlink() or not db_path.is_file() or
            db_path.resolve(strict=True).parent != root):
        raise ValueError("Expected a local UYAP_Arsiv/_archive.db")

    fields = ("document_id", "case_id", "display_name", "local_path",
              "extension", "size", "sha256", "status")
    uri = db_path.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
        if not set(fields).issubset(columns):
            raise ValueError("Archive documents schema is not supported")
        rows = db.execute(
            "SELECT " + ",".join(fields) + " FROM documents LIMIT ?",
            (MAX_AUDIT_ROWS + 1,),
        ).fetchall()
    if len(rows) > MAX_AUDIT_ROWS:
        raise ValueError("Archive audit row limit exceeded")

    counts = dict(
        records=len(rows), verifiedInDatabase=0,
        notVerified=0, internalExcluded=0, unsupportedExcluded=0,
        invalidMetadata=0, missingOrUnsafe=0, integrityFailed=0,
        provenOriginals=0,
        provenPDF=0, provenUDF=0, provenTIF=0
    )
    for doc_id, case_id, name, local, ext, size, sha, status in rows:
        if str(status or "") != "VERIFIED":
            counts["notVerified"] += 1
            continue
        counts["verifiedInDatabase"] += 1
        kind = archive_kind(ext, local, name)
        if kind == "INTERNAL":
            counts["internalExcluded"] += 1
            continue
        if kind == "UNSUPPORTED":
            counts["unsupportedExcluded"] += 1
            continue
        extension = str(ext or "").lower()
        digest = str(sha or "").lower()
        if (extension not in EXTENSIONS or not HASH_PATTERN.fullmatch(digest) or
                type(size) is not int or not 5 <= size <= MAX_DOCUMENT_BYTES or
                not doc_id or not case_id or not local):
            counts["invalidMetadata"] += 1
            continue
        candidate = safe_local_file(root, str(local), extension, size)
        if candidate is None:
            counts["missingOrUnsafe"] += 1
            continue
        if not matching_bytes(candidate, size, digest, extension):
            counts["integrityFailed"] += 1
            continue
        counts["provenOriginals"] += 1
        counts["proven" + kind.removeprefix("DOCUMENT_")] += 1

    return {
        "schemaVersion": 1,
        "mode": "LOCAL_READINESS_AUDIT_ONLY",
        "readsOnly": True,
        "noDocumentTransfer": True,
        "noUyapConnection": True,
        "counts": counts,
        "notes": [
            "DB VERIFIED plus byte-level SHA-256 is local evidence, not UYAP signature validation.",
            "No personal document names, paths, hashes or court identifiers are included.",
            "This audit does not authorize Android DEBUG transfer of real documents."
        ]
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Count/prove local archive records without exporting documents")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--archive-root",
                           help="Local UYAP_Arsiv directory or its parent")
    selection.add_argument(
        "--selected-archive", action="store_true",
        help="Read the existing Archive AI selected local archive (no new selection)"
    )
    args = parser.parse_args(argv)
    try:
        if args.selected_archive:
            # Use the user's existing Archive AI selection, read-only.
            # Avoid exposing its private Windows path in the summary or errors.
            from uyap_archive_agent import load_selected_archive_root
            selected_root = load_selected_archive_root()
            if selected_root is None:
                raise ValueError("No current selected archive")
        else:
            selected_root = args.archive_root
        summary = audit(str(selected_root))
    except (ValueError, OSError, sqlite3.Error):
        # Avoid printing user-specific paths or document identifiers in errors.
        print(json.dumps({"ok": False, "error": "ARCHIVE_AUDIT_NOT_AVAILABLE"}))
        return 2
    print(json.dumps({"ok": True, **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
