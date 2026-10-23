#!/usr/bin/env python3
"""Development-only, read-only Android emulator bridge to locally VERIFIED PDF files.

This is NOT a UYAP API or a portal login client. The user explicitly selects an
existing UYAP Archive AI archive. It never retrieves UYAP session credentials.

Security boundary:
- loopback-only (127.0.0.1); Android emulator reaches the host via 10.0.2.2
- short-lived bearer token generated fresh on every invocation
- read-only SQLite, only VERIFIED, locally contained and hash-proven PDFs
- no CORS, no HTTP POST/PUT/DELETE, no sensitive request logging
- never use across untrusted networks or with real case data in debug builds
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from archive_package_adapter import VerifiedArchivePackage, make_archive_package_handler
from live_archive_adapter import VerifiedLiveArchive
from mobile_pairing import PairingGate, PAIRING_TTL

PORT = 18743
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_RESULTS = 20


def find_archive_root(selected: str) -> Path:
    root = Path(selected).expanduser().resolve(strict=True)
    if root.name != "UYAP_Arsiv":
        root = (root / "UYAP_Arsiv").resolve(strict=True)
    if not root.is_dir() or not (root / "_archive.db").is_file():
        raise ValueError("Seçilen yolda UYAP_Arsiv/_archive.db bulunamadı")
    return root


def read_rows(root: Path, limit: int = MAX_RESULTS, offset: int = 0):
    root = root.resolve(strict=True)
    if limit < 1 or limit > MAX_RESULTS or offset < 0 or offset > 100000:
        raise ValueError("Geçersiz sayfalama")
    db = (root / "_archive.db").resolve(strict=True)
    if db.parent != root:
        raise ValueError("Veritabanı arşiv kökünde değil")
    uri = db.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as connection:
        columns = {r[1] for r in connection.execute("PRAGMA table_info(documents)")}
        needed = {"session_id", "document_id", "case_id", "display_name",
                  "institution_name", "local_path", "size", "sha256", "status"}
        if not needed.issubset(columns):
            raise ValueError("Arşiv tablosu beklenen şemada değil")
        return connection.execute(
            """SELECT session_id, document_id, case_id, display_name,
                      institution_name, local_path, size, sha256
               FROM documents WHERE status='VERIFIED'
                    AND lower(local_path) LIKE '%.pdf'
                    AND size > 5 AND size <= ?
                    AND sha256 IS NOT NULL AND sha256 != ''
               ORDER BY updated_at DESC, session_id ASC, document_id ASC LIMIT ? OFFSET ?""",
            (MAX_PDF_BYTES, limit, offset),
        ).fetchall()


def safe_pdf_path(root: Path, row) -> Path | None:
    try:
        root = root.resolve(strict=True)
        value = str(row[5] or "")
        path = Path(value)
        if not path.is_absolute() or path.is_symlink():
            return None
        actual = path.resolve(strict=True)
        if not actual.is_relative_to(root):
            return None
        if not actual.is_file() or actual.suffix.lower() != ".pdf":
            return None
        if actual.stat().st_size != int(row[6]):
            return None
        return actual
    except (OSError, ValueError, TypeError):
        return None


def public_id(row, secret: bytes) -> str:
    key = "\x1f".join(str(row[i] or "") for i in (0, 1, 7))
    return hmac.new(secret, key.encode("utf-8"), hashlib.sha256).hexdigest()[:40]


def list_visible(root: Path, secret: bytes, limit=20, offset=0):
    result = []
    for row in read_rows(root, limit, offset):
        path = safe_pdf_path(root, row)
        if path is None:
            continue
        result.append({
            "id": public_id(row, secret),
            "caseLabel": str(row[2] or "")[:120],
            "institution": str(row[4] or "")[:120],
            "name": str(row[3] or "Evrak.pdf")[:160],
            "size": int(row[6]),
            "sha256": str(row[7]).lower(),
        })
    return result


def verified_pdf(root: Path, row) -> Path | None:
    path = safe_pdf_path(root, row)
    if path is None:
        return None
    stored = str(row[7] or "").lower()
    if len(stored) != 64 or any(c not in "0123456789abcdef" for c in stored):
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            return None
        source.seek(0)
        while chunk := source.read(64 * 1024):
            digest.update(chunk)
    return path if hmac.compare_digest(digest.hexdigest(), stored) else None



def verified_pdf_snapshot(root: Path, row):
    """Return an immutable byte snapshot after proving size, header and SHA-256.

    The HTTP response reads from this checked snapshot rather than reopening
    a live PDF after verification. Spill over 1 MB to a private temporary file.
    """
    if row is None:
        return None
    path = safe_pdf_path(root, row)
    if path is None:
        return None
    expected = str(row[7] or "").lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        return None
    snapshot = tempfile.SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b")
    try:
        digest = hashlib.sha256()
        copied = 0
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                snapshot.close()
                return None
            source.seek(0)
            while chunk := source.read(64 * 1024):
                copied += len(chunk)
                if copied > MAX_PDF_BYTES:
                    snapshot.close()
                    return None
                digest.update(chunk)
                snapshot.write(chunk)
        if copied != int(row[6]) or not hmac.compare_digest(
            digest.hexdigest(), expected
        ):
            snapshot.close()
            return None
        snapshot.seek(0)
        return snapshot
    except Exception:
        snapshot.close()
        raise

def make_handler(root: Path, token: str):
    key = secrets.token_bytes(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "UYAPArchiveMobileBridge/0.1"
        protocol_version = "HTTP/1.0"

        def log_message(self, format, *args):
            # Paths, document identifiers and auth credentials must not enter logs.
            return

        def _send(self, code: int, data: dict) -> None:
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            auth = self.headers.get("Authorization", "")
            if not hmac.compare_digest(auth, "Bearer " + token):
                self._send(401, {"error": "UNAUTHORIZED"})
                return
            parsed = urlparse(self.path)
            if parsed.path == "/v1/status":
                self._send(200, {
                    "schemaVersion": 1, "mode": "LOCAL_ARCHIVE_ONLY",
                    "uyapDirectApi": False, "readOnly": True,
                    "agent": "UYAP Archive AI", "maxPdfBytes": MAX_PDF_BYTES,
                })
                return
            if parsed.path == "/v1/documents":
                try:
                    args = parse_qs(parsed.query)
                    limit = int(args.get("limit", ["20"])[0])
                    offset = int(args.get("offset", ["0"])[0])
                    rows = list_visible(root, key, limit, offset)
                    self._send(200, {
                        "documents": rows, "offset": offset,
                        "limit": limit, "hasMore": bool(read_rows(root, 1, offset + limit)),
                    })
                except (ValueError, sqlite3.Error, OSError):
                    self._send(400, {"error": "INVALID_ARCHIVE_REQUEST"})
                return
            if parsed.path.startswith("/v1/document/"):
                requested = parsed.path.removeprefix("/v1/document/")
                if len(requested) != 40 or any(c not in "0123456789abcdef" for c in requested):
                    self._send(404, {"error": "NOT_FOUND"})
                    return
                try:
                    # Limit scope to 100 latest VERIFIED PDFs in this development version.
                    rows = []
                    for offset in range(0, 100, MAX_RESULTS):
                        rows.extend(read_rows(root, MAX_RESULTS, offset))
                    selected = next((r for r in rows if public_id(r, key) == requested), None)
                    snapshot = verified_pdf_snapshot(root, selected)
                    if snapshot is None:
                        self._send(404, {"error": "NOT_FOUND_OR_UNVERIFIED"})
                        return
                    with snapshot:
                        self.send_response(200)
                        self.send_header("Content-Type", "application/pdf")
                        self.send_header("Content-Length", str(int(selected[6])))
                        self.send_header("Cache-Control", "no-store")
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        while chunk := snapshot.read(64 * 1024):
                            self.wfile.write(chunk)
                except (sqlite3.Error, OSError, ValueError, BrokenPipeError):
                    # No disclosure of archive paths or private content in error responses.
                    return
                return
            self._send(404, {"error": "NOT_FOUND"})

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local UYAP Archive AI -> Android emulator bridge")
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--archive-root", help="UYAP_Arsiv folder or its parent")
    sources.add_argument("--archive-package", help="Explicit offline UYAP Archive AI ZIP (debug only)")
    sources.add_argument("--live-archive-root", help="Read-only live UYAP_Arsiv folder (debug/synthetic only)")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--pairing", action="store_true",
                        help="Ephemeral single-device pairing, synthetic live demo only")
    args = parser.parse_args(argv)
    if args.pairing and not args.live_archive_root:
        parser.error("--pairing requires --live-archive-root synthetic demo")
    token = secrets.token_urlsafe(32)
    if args.archive_package:
        package = VerifiedArchivePackage(args.archive_package)
        handler = make_archive_package_handler(package, token)
        print("OFFLINE ZIP PACKAGE: " + str(len(package.documents)) +
              " VERIFIED PDF/UDF/TIF records; no UYAP network access")
    elif args.live_archive_root:
        # Accidental live REAL case transfer to a screenshot-enabled DEBUG emulator
        # is disallowed; use audit_real_archive.py instead. Marker = safety interlock
        # for a generated synthetic fixture, not cryptographic attestation.
        test_root = Path(args.live_archive_root).expanduser().resolve(strict=True)
        if test_root.name != "UYAP_Arsiv":
            test_root = (test_root / "UYAP_Arsiv").resolve(strict=True)
        marker = test_root / ".hukukarsivim-synthetic-only"
        if marker.is_symlink() or not marker.is_file() or marker.read_text(
            encoding="ascii"
        ) != "SYNTHETIC_TEST_FIXTURE_V1\n":
            raise SystemExit(
                "LIVE_BRIDGE_DEBUG_SYNTHETIC_ONLY: "
                "Real archives must be checked with audit_real_archive.py; "
                "no actual documents are sent."
            )
        live = VerifiedLiveArchive(args.live_archive_root)
        gate = PairingGate() if args.pairing else None
        handler = make_archive_package_handler(live, token, pairing_gate=gate)
        if gate is not None:
            print("EMULATOR-ONLY PAIRING CODE (10 minute validity): " + gate.code)
            print("Code is one-use; new credentials on every restart. Do not photograph.")
        print("LIVE LOCAL ARCHIVE: " + str(len(live.documents)) +
              " VERIFIED PDF/UDF/TIF; no ZIP and no UYAP API")
    else:
        root = find_archive_root(args.archive_root)
        handler = make_handler(root, token)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print("LOCAL ARCHIVE BRIDGE: localhost only; NOT direct UYAP API access")
    print("STATUS: running on 127.0.0.1:" + str(args.port))
    if not args.pairing:
        print("ONE-TIME CONNECTION KEY: " + token)
    print("Use only synthetic documents in the Android DEBUG build.")
    print("Ctrl+C to stop; a new key is generated on every start.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
