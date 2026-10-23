#!/usr/bin/env python3
"""Privacy-safe, read-only safety inventory of already VERIFIED local ZIP candidates.

- No UYAP network access, credential use, extraction, or document transfers.
- Verifies each ZIP source file's existing DB SHA-256 *before* inspecting its
  central directory from an immutable, size-limited in-memory snapshot.
- Does not decompress individual ZIP entries or assert that their CRCs match.
- Emits a closed set of aggregate status counters, never names or paths.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import hmac
import io
import json
import re
import sqlite3
import stat
from pathlib import Path
import zipfile

from archive_document_policy import archive_kind, resolved_extension
from audit_archive_formats import select_root, MAX_AUDIT_ROWS

MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_SCANNED_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 2000
MAX_MEMBER_EXPANDED = 32 * 1024 * 1024
MAX_TOTAL_EXPANDED = 128 * 1024 * 1024
MAX_EXPANSION_RATIO = 100

STATUS_LABELS = (
    "SAFE_CENTRAL_DIRECTORY",
    "SOURCE_INVALID_METADATA",
    "SOURCE_MISSING_OR_UNSAFE",
    "SOURCE_TOO_LARGE",
    "SOURCE_HASH_MISMATCH",
    "SOURCE_NOT_ZIP",
    "ZIP_MALFORMED",
    "ZIP_TOO_MANY_ENTRIES",
    "ZIP_UNSAFE_MEMBER_PATH",
    "ZIP_DUPLICATE_MEMBER_NAME",
    "ZIP_ENCRYPTED_MEMBER",
    "ZIP_SYMLINK_MEMBER",
    "ZIP_UNSUPPORTED_COMPRESSION",
    "ZIP_MEMBER_TOO_LARGE",
    "ZIP_EXPANSION_RISK",
    "SCAN_BUDGET_REACHED",
)
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def safe_zip_source(root: Path, value: str, expected_size: int) -> Path | None:
    """Resolve Windows 8.3 aliases without permitting a symlink/junction escape.

    On Windows the tempfile root sometimes uses a short-name alias (RUNNER~1)
    whereas Path.resolve() returns its expanded spelling. Compare resolved
    ancestors with resolved root, but check each *original* ancestor for links.
    """
    try:
        root = root.resolve(strict=True)
        candidate = Path(value)
        if not candidate.is_absolute() or candidate.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
        if (resolved == root or not resolved.is_relative_to(root) or
                not resolved.is_file() or resolved.suffix.lower() != ".zip" or
                resolved.stat().st_size != expected_size):
            return None
        parent = candidate.parent
        for _ in range(100):
            if parent.is_symlink():
                return None
            if hasattr(parent, "is_junction") and parent.is_junction():
                return None
            actual_parent = parent.resolve(strict=True)
            if actual_parent == root:
                return resolved
            if not actual_parent.is_relative_to(root) or parent == parent.parent:
                return None
            parent = parent.parent
        return None
    except (OSError, RuntimeError, ValueError, TypeError):
        return None


def safe_member_name(name: str) -> bool:
    """Unsafe for future extraction; NO extraction is performed by this tool."""
    if not name or "\x00" in name or "\\" in name or name.startswith("/"):
        return False
    parts = name.rstrip("/").split("/")
    if not parts or any(part in ("", ".", "..") for part in parts):
        return False
    if ":" in parts[0] or any("\x00" in p for p in parts):
        return False
    return True


def inspect_central_directory(data: bytes) -> str:
    if not data.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return "SOURCE_NOT_ZIP"
    try:
        with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
            entries = archive.infolist()
            if len(entries) > MAX_MEMBERS:
                return "ZIP_TOO_MANY_ENTRIES"
            total_expanded = 0
            seen = set()
            for info in entries:
                if not safe_member_name(info.filename):
                    return "ZIP_UNSAFE_MEMBER_PATH"
                folded = info.filename.rstrip("/").casefold()
                if folded in seen:
                    return "ZIP_DUPLICATE_MEMBER_NAME"
                seen.add(folded)
                if info.flag_bits & 0x1:
                    return "ZIP_ENCRYPTED_MEMBER"
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode):
                    return "ZIP_SYMLINK_MEMBER"
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    return "ZIP_UNSUPPORTED_COMPRESSION"
                if (info.file_size < 0 or info.compress_size < 0 or
                        info.file_size > MAX_MEMBER_EXPANDED):
                    return "ZIP_MEMBER_TOO_LARGE"
                total_expanded += info.file_size
                if total_expanded > MAX_TOTAL_EXPANDED:
                    return "ZIP_EXPANSION_RISK"
                if info.file_size and (
                    info.compress_size <= 0 or
                    info.file_size > info.compress_size * MAX_EXPANSION_RATIO
                ):
                    return "ZIP_EXPANSION_RISK"
            return "SAFE_CENTRAL_DIRECTORY"
    except (zipfile.BadZipFile, OSError, ValueError, RuntimeError, OverflowError):
        return "ZIP_MALFORMED"


def audit_zip(selected: str) -> dict:
    root = select_root(selected)
    db_file = root / "_archive.db"
    with closing(sqlite3.connect(db_file.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        db.execute("PRAGMA query_only=ON")
        columns = {c[1] for c in db.execute("PRAGMA table_info(documents)")}
        required = {"status", "document_id", "case_id", "display_name",
                    "local_path", "size", "sha256"}
        if not required.issubset(columns):
            raise ValueError("Unexpected UYAP archive DB schema")
        extension_sql = "extension" if "extension" in columns else "NULL AS extension"
        rows = db.execute(
            "SELECT status,document_id,case_id,display_name,local_path,size,sha256,"
            + extension_sql + " FROM documents LIMIT ?", (MAX_AUDIT_ROWS + 1,)
        ).fetchall()
    if len(rows) > MAX_AUDIT_ROWS:
        raise ValueError("Archive row limit exceeded")

    counters = Counter({s: 0 for s in STATUS_LABELS})
    verified_count = 0
    candidates = 0
    ignored_internal = 0
    scanned_bytes = 0
    for status, doc_id, case_id, name, local_path, size, digest, recorded_ext in rows:
        if status != "VERIFIED":
            continue
        verified_count += 1
        extension = resolved_extension(recorded_ext, local_path)
        kind = archive_kind(extension, local_path, name)
        if kind == "INTERNAL":
            ignored_internal += 1
            continue
        if extension != ".zip" or kind != "UNSUPPORTED":
            continue
        candidates += 1
        sha = str(digest or "").lower()
        if (not doc_id or not case_id or not local_path or
                type(size) is not int or size < 4 or
                not SHA256_PATTERN.fullmatch(sha)):
            counters["SOURCE_INVALID_METADATA"] += 1
            continue
        if size > MAX_SOURCE_BYTES:
            counters["SOURCE_TOO_LARGE"] += 1
            continue
        if scanned_bytes + size > MAX_TOTAL_SCANNED_BYTES:
            counters["SCAN_BUDGET_REACHED"] += 1
            continue
        path = safe_zip_source(root, str(local_path), size)
        if path is None:
            counters["SOURCE_MISSING_OR_UNSAFE"] += 1
            continue
        # A validated immutable snapshot avoids re-opening a mutable source
        # between SHA verification and central-directory inspection.
        try:
            with path.open("rb") as source:
                data = source.read(MAX_SOURCE_BYTES + 1)
        except OSError:
            counters["SOURCE_MISSING_OR_UNSAFE"] += 1
            continue
        if len(data) != size:
            counters["SOURCE_MISSING_OR_UNSAFE"] += 1
            continue
        scanned_bytes += len(data)
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), sha):
            counters["SOURCE_HASH_MISMATCH"] += 1
            continue
        counters[inspect_central_directory(data)] += 1

    if sum(counters.values()) != candidates:
        raise ValueError("ZIP audit categories are inconsistent")
    return {
        "ok": True,
        "mode": "LOCAL_ZIP_CENTRAL_DIRECTORY_AUDIT",
        "readOnly": True,
        "noExtraction": True,
        "noMemberDecompression": True,
        "noDocumentTransfer": True,
        "noUyapConnection": True,
        "counts": {
            "databaseRecords": len(rows),
            "verifiedInDatabase": verified_count,
            "internalExcluded": ignored_internal,
            "zipCandidates": candidates,
            "scannedBytes": scanned_bytes,
            "statuses": {k: counters[k] for k in STATUS_LABELS},
        },
        "notes": [
            "SAFE_CENTRAL_DIRECTORY means the ZIP source SHA and bounded metadata checks passed.",
            "Individual member bytes, CRC values, signatures and ZIP attachments' legal validity were not verified.",
            "Only fixed category labels and aggregate counts are included, not names, paths or document hashes.",
            "Real case data was not copied, uploaded or sent to Android DEBUG."
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read-only ZIP safety audit of local archive")
    parser.add_argument("--archive-root", required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_zip(args.archive_root)
    except (sqlite3.Error, OSError, ValueError):
        print(json.dumps({"ok": False, "error": "ZIP_AUDIT_NOT_AVAILABLE"}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
