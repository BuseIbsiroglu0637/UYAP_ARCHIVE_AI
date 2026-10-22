#!/usr/bin/env python3
"""Read-only UYAP Archive AI *live filesystem* adapter for a user-selected root.

This adapter never accesses UYAP, network credentials, cookies or Chrome tabs.
Live means the verified LOCAL archive changes when the desktop collector writes.
Only localhost + Android emulator / synthetic data are in scope.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import hashlib
import hmac
import json
import sqlite3
import threading

from archive_package_adapter import (
    ArchivePackageDocument, DOC_KEY_PATTERN, EXTENSIONS, HASH_PATTERN,
    MAX_DOCUMENT_BYTES,
)


class VerifiedLiveArchive:
    mode = "LOCAL_ARCHIVE_LIVE"
    MAX_ROWS = 5000

    def __init__(self, selected: str):
        self.root = Path(selected).expanduser().resolve(strict=True)
        if self.root.name != "UYAP_Arsiv":
            self.root = (self.root / "UYAP_Arsiv").resolve(strict=True)
        self.db_path = self.root / "_archive.db"
        if (not self.root.is_dir() or not self.db_path.is_file() or
                self.db_path.is_symlink() or
                self.db_path.resolve(strict=True).parent != self.root):
            raise ValueError("Expected UYAP_Arsiv/_archive.db")
        self._lock = threading.RLock()
        self.documents: list[ArchivePackageDocument] = []
        self.refresh()

    def _contained_path(self, value: str, ext: str, size: int) -> Path | None:
        try:
            # Fail closed on copied/stale DB paths from a different PC.
            candidate = Path(value)
            if not candidate.is_absolute() or candidate.is_symlink():
                return None
            resolved = candidate.resolve(strict=True)
            if (resolved == self.root or not resolved.is_relative_to(self.root)
                    or not resolved.is_file() or resolved.suffix.lower() != ext
                    or resolved.stat().st_size != size):
                return None
            # No symlink in the ancestor chain inside the selected root.
            parent = candidate.parent
            while parent != self.root:
                if parent.is_symlink() or not parent.is_relative_to(self.root):
                    return None
                parent = parent.parent
            return resolved
        except (OSError, RuntimeError, ValueError, TypeError):
            return None

    def refresh(self):
        """Re-read metadata read-only. Documents appear only after VERIFIED."""
        uri = self.db_path.as_uri() + "?mode=ro"
        result = []
        with closing(sqlite3.connect(uri, uri=True, timeout=5)) as db:
            db.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
            required = {"document_id", "case_id", "institution_name", "display_name",
                        "local_path", "extension", "size", "sha256", "status"}
            if not required.issubset(columns):
                raise ValueError("Unexpected live archive schema")
            extras = [col for col in ("source_system", "source_identity_hash") if col in columns]
            sql = ("SELECT document_id,case_id,institution_name,display_name,local_path,"
                   "extension,size,sha256," + ",".join(extras + ["status"]) +
                   " FROM documents WHERE status='VERIFIED' ORDER BY document_id LIMIT ?")
            rows = db.execute(sql, (self.MAX_ROWS + 1,)).fetchall()
            if len(rows) > self.MAX_ROWS:
                raise ValueError("Live archive record limit exceeded")
            for row in rows:
                doc_id, case_id, institution, name, source_path, ext, size, sha = row[:8]
                tail = row[8:]
                ext = str(ext or "").lower()
                digest = str(sha or "").lower()
                if (ext not in EXTENSIONS or not HASH_PATTERN.fullmatch(digest) or
                    type(size) is not int or not 5 <= size <= MAX_DOCUMENT_BYTES or
                    not doc_id or not case_id or not source_path):
                    continue
                path = self._contained_path(str(source_path), ext, size)
                if path is None:
                    continue
                system = str(tail[extras.index("source_system")] or "") if "source_system" in extras else ""
                identity = str(tail[extras.index("source_identity_hash")] or "") if "source_identity_hash" in extras else ""
                source_value = json.dumps(
                    [system, str(case_id), str(doc_id), identity],
                    ensure_ascii=False, separators=(",", ":")
                )
                key = hashlib.sha256(source_value.encode("utf-8")).hexdigest()
                result.append(ArchivePackageDocument(
                    source_key=key, name=str(name or path.name),
                    case_label=str(case_id), institution=str(institution or ""),
                    extension=ext, size=size, sha256=digest,
                    zip_member=str(path)  # PRIVATE internal locator, never in JSON.
                ))
        keys = [d.source_key for d in result]
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate live source document identities")
        result.sort(key=lambda d: (d.case_label, d.source_key))
        with self._lock:
            self.documents = result
        return result

    def bytes_for(self, source_key: str):
        if not DOC_KEY_PATTERN.fullmatch(source_key):
            return None
        self.refresh()  # Guard against DB value change/removal after listing.
        with self._lock:
            doc = next((x for x in self.documents if x.source_key == source_key), None)
        if doc is None:
            return None
        path = self._contained_path(doc.zip_member, doc.extension, doc.size)
        if path is None:
            return None
        hasher = hashlib.sha256()
        chunks = bytearray()
        try:
            with path.open("rb") as stream:
                while segment := stream.read(64 * 1024):
                    chunks.extend(segment)
                    if len(chunks) > MAX_DOCUMENT_BYTES or len(chunks) > doc.size:
                        return None
                    hasher.update(segment)
        except (OSError, ValueError):
            return None
        if len(chunks) != doc.size or not hmac.compare_digest(
            hasher.hexdigest(), doc.sha256
        ):
            return None
        data = bytes(chunks)  # immutable checked snapshot; no file reopening.
        if doc.extension == ".pdf" and not data.startswith(b"%PDF-"):
            return None
        if doc.extension in {".tif", ".tiff"} and not data.startswith(
            (b"II*\x00", b"MM\x00*")
        ):
            return None
        if doc.extension == ".udf" and not data.lstrip().startswith(
            (b"PK\x03\x04", b"<?xml", b"<template")
        ):
            return None
        return doc, data
