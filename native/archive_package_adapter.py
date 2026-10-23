#!/usr/bin/env python3
"""Read-only UYAP Archive AI ZIP adapter for an explicit user-selected package.

Supports VERIFIED PDF/UDF/TIF/ZIP source bytes and their original archive IDs.
Never performs UYAP requests, conversion, login, extraction or data mutation.
Test builds only: do not move case data to an Android DEBUG application.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse, parse_qs
import hashlib
from archive_document_policy import archive_kind, resolved_extension
import hmac
import json
import re
import sqlite3
import stat
import zipfile

MAX_ENTRIES = 5000
MAX_TOTAL_UNCOMPRESSED = 256 * 1024 * 1024
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
MAX_DB_BYTES = 16 * 1024 * 1024
EXTENSIONS = {".pdf", ".udf", ".tif", ".tiff", ".zip"}
MIME = {".pdf": "application/pdf", ".udf": "application/octet-stream",
        ".tif": "image/tiff", ".tiff": "image/tiff", ".zip": "application/zip"}
HASH_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
DOC_KEY_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def safe_entry_name(name: str) -> str:
    if not name or "\\" in name or name.startswith(("/", "\\")) or "\x00" in name:
        raise ValueError("Unsafe ZIP member path")
    path = PurePosixPath(name)
    if any(part in (".", "..", "") for part in name.split("/")) or ":" in path.parts[0]:
        raise ValueError("Unsafe ZIP member path")
    return str(path)


@dataclass(frozen=True)
class ArchivePackageDocument:
    source_key: str
    name: str
    case_label: str
    institution: str
    extension: str
    size: int
    sha256: str
    zip_member: str

    def metadata(self):
        return {
            "sourceKey": self.source_key,
            "id": self.source_key[:40],
            "name": self.name[:160],
            "caseLabel": self.case_label[:120],
            "institution": self.institution[:120],
            "format": self.extension.lstrip(".").upper(),
            "size": self.size,
            "sha256": self.sha256,
        }


class VerifiedArchivePackage:
    """Fail closed on ambiguities. ZIP filenames are never used as identity."""
    mode = "OFFLINE_ARCHIVE_PACKAGE"

    def __init__(self, filename: str):
        self.filename = Path(filename).expanduser().resolve(strict=True)
        if not self.filename.is_file() or self.filename.suffix.lower() != ".zip":
            raise ValueError("Select a ZIP archive package")
        self.documents: list[ArchivePackageDocument] = []
        with zipfile.ZipFile(self.filename) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError("Too many ZIP entries")
            if sum(i.file_size for i in entries) > MAX_TOTAL_UNCOMPRESSED:
                raise ValueError("ZIP expansion exceeds limit")
            seen = set()
            members = []
            for info in entries:
                path = safe_entry_name(info.filename.rstrip("/"))
                normalized = path.casefold()
                if normalized in seen:
                    raise ValueError("Duplicate ZIP member names")
                seen.add(normalized)
                mode = (info.external_attr >> 16) & 0xFFFF
                if stat.S_ISLNK(mode) or info.flag_bits & 0x1:
                    raise ValueError("Symlinks or encrypted ZIP entries not allowed")
                if not info.is_dir():
                    if info.file_size > MAX_DOCUMENT_BYTES and not path.endswith("_archive.db"):
                        raise ValueError("Oversized archive entry")
                    members.append(info)
            db_files = [i for i in members if PurePosixPath(i.filename).name == "_archive.db"]
            if len(db_files) != 1 or db_files[0].file_size > MAX_DB_BYTES:
                raise ValueError("Expected one bounded _archive.db")
            raw_db = archive.read(db_files[0])
            db = sqlite3.connect(":memory:")
            try:
                db.deserialize(raw_db)
                db.execute("PRAGMA query_only=ON")
                fields = {r[1] for r in db.execute("PRAGMA table_info(documents)")}
                required = {"document_id", "case_id", "institution_name", "display_name",
                            "local_path", "size", "sha256", "status"}
                if not required.issubset(fields):
                    raise ValueError("Unexpected archive metadata schema")
                source_fields = [
                    col for col in ("source_system", "source_identity_hash") if col in fields
                ]
                extension_expr = "extension" if "extension" in fields else "NULL AS extension"
                sql = ("SELECT document_id, case_id, institution_name, display_name, "
                       "local_path, " + extension_expr + ", size, sha256, " +
                       ", ".join(source_fields + ["status"]) + " FROM documents")
                rows = db.execute(sql).fetchmany(MAX_ENTRIES + 1)
                if len(rows) > MAX_ENTRIES:
                    raise ValueError("Too many archive metadata records")
            finally:
                db.close()
            candidates_by_name = {}
            for entry in members:
                candidates_by_name.setdefault(PurePosixPath(entry.filename).name.casefold(), []).append(entry)
            for row in rows:
                doc_id, case_id, institution, display, source_path, extension, size, sha = row[:8]
                tail = row[8:]
                if tail[-1] != "VERIFIED":
                    continue
                ext = resolved_extension(extension, source_path)
                canonical_sha = str(sha or "").lower()
                if (ext not in EXTENSIONS or not HASH_PATTERN.fullmatch(canonical_sha) or
                    not isinstance(size, int) or size < 5 or size > MAX_DOCUMENT_BYTES or
                    not doc_id or not case_id or not source_path):
                    continue
                if archive_kind(ext, str(source_path), str(display or "")) == "INTERNAL":
                    continue
                # Source paths in exported packages may refer to a different PC.
                # Resolve by verified *bytes*, size, suffix and unambiguous basename;
                # never open source_path on this machine or trust it as a ZIP path.
                basename = str(source_path).replace("\\", "/").rsplit("/", 1)[-1]
                if not basename or not basename.lower().endswith(ext):
                    continue
                possibilities = candidates_by_name.get(basename.casefold(), [])
                valid = []
                for info in possibilities:
                    if info.file_size != size:
                        continue
                    if hashlib.sha256(archive.read(info)).hexdigest() == canonical_sha:
                        valid.append(info)
                if len(valid) != 1:
                    continue  # Missing or ambiguous content must not be exposed.
                source_system = str(tail[source_fields.index("source_system")] or "") if "source_system" in source_fields else ""
                identity_hash = str(tail[source_fields.index("source_identity_hash")] or "") if "source_identity_hash" in source_fields else ""
                identity = json.dumps(
                    [source_system, str(case_id), str(doc_id), identity_hash],
                    ensure_ascii=False, separators=(",", ":")
                )
                source_key = hashlib.sha256(identity.encode("utf-8")).hexdigest()
                self.documents.append(ArchivePackageDocument(
                    source_key=source_key, name=str(display or basename),
                    case_label=str(case_id), institution=str(institution or ""),
                    extension=ext, size=size, sha256=canonical_sha,
                    zip_member=valid[0].filename
                ))
            if len({d.source_key for d in self.documents}) != len(self.documents):
                raise ValueError("Duplicate source document identities")
            self.documents.sort(key=lambda doc: (doc.case_label, doc.source_key))

    def bytes_for(self, source_key: str) -> tuple[ArchivePackageDocument, bytes] | None:
        if not DOC_KEY_PATTERN.fullmatch(source_key):
            return None
        item = next((d for d in self.documents if d.source_key == source_key), None)
        if item is None:
            return None
        try:
            with zipfile.ZipFile(self.filename) as archive:
                payload = archive.read(item.zip_member)
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            # The selected archive changed since the scan. Fail closed.
            return None
        if len(payload) != item.size or not hmac.compare_digest(
            hashlib.sha256(payload).hexdigest(), item.sha256
        ):
            return None
        if item.extension == ".pdf" and not payload.startswith(b"%PDF-"):
            return None
        if item.extension in (".tif", ".tiff") and not payload.startswith(
            (b"II*\x00", b"MM\x00*")
        ):
            return None
        if item.extension == ".udf" and not payload.lstrip().startswith(
            (b"PK\x03\x04", b"<?xml", b"<template")
        ):
            return None
        if item.extension == ".zip":
            # Treat ZIP as opaque bytes; check its bounded central directory.
            # Import lazily to avoid a helper module dependency cycle.
            from audit_archive_zip import inspect_central_directory
            if inspect_central_directory(payload) != "SAFE_CENTRAL_DIRECTORY":
                return None
        return item, payload


def make_archive_package_handler(package: VerifiedArchivePackage, token: str):
    """Isolated, temporary localhost HTTP API; never expose to public networks."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "UYAPArchivePackagePreview/0.1"

        def log_message(self, *args):
            pass  # Do not log user document names, paths, request tokens.

        def send_json(self, code, body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not hmac.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            ):
                self.send_json(401, {"error": "UNAUTHORIZED"})
                return
            parsed = urlparse(self.path)
            if parsed.path == "/v2/status":
                try:
                    if hasattr(package, "refresh"):
                        package.refresh()
                except (sqlite3.Error, OSError, ValueError):
                    self.send_json(503, {"error": "ARCHIVE_NOT_READY"})
                    return
                self.send_json(200, {
                    "schemaVersion": 2, "mode": package.mode,
                    "readOnly": True, "uyapDirectApi": False,
                    "formats": ["PDF", "UDF", "TIF", "ZIP"], "count": len(package.documents),
                })
                return
            if parsed.path == "/v2/documents":
                try:
                    if hasattr(package, "refresh"):
                        package.refresh()
                except (sqlite3.Error, OSError, ValueError):
                    self.send_json(503, {"error": "ARCHIVE_NOT_READY"})
                    return
                try:
                    query = parse_qs(parsed.query)
                    limit = int(query.get("limit", ["20"])[0])
                    offset = int(query.get("offset", ["0"])[0])
                    if not 1 <= limit <= 20 or not 0 <= offset <= 10000:
                        raise ValueError("Invalid page")
                except (ValueError, TypeError):
                    self.send_json(400, {"error": "INVALID_PAGE"})
                    return
                docs = package.documents[offset:offset + limit]
                self.send_json(200, {
                    "documents": [d.metadata() for d in docs],
                    "offset": offset, "limit": limit,
                    "hasMore": offset + limit < len(package.documents)
                })
                return
            if parsed.path.startswith("/v2/document/"):
                key = parsed.path.removeprefix("/v2/document/")
                try:
                    proof = package.bytes_for(key)
                except (sqlite3.Error, OSError, ValueError):
                    self.send_json(503, {"error": "ARCHIVE_NOT_READY"})
                    return
                if proof is None:
                    self.send_json(404, {"error": "MISSING_OR_UNVERIFIED"})
                    return
                document, payload = proof
                self.send_response(200)
                self.send_header("Content-Type", MIME[document.extension])
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_json(404, {"error": "NOT_FOUND"})

    return Handler
