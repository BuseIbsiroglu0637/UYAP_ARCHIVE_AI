import hashlib
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "native"))
import uyap_archive_agent as agent  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)


bridge = (ROOT / "background" / "native-agent.js").read_text("utf-8")
manager = (ROOT / "background" / "download-manager.js").read_text("utf-8")
manifest = (ROOT / "manifest.json").read_text("utf-8")

require(agent.AGENT_VERSION == "2.7.64", agent.AGENT_VERSION)
ping = agent.dispatch({"action": "ping"})
require("verified_root_orphan_cleanup_v1" in ping.get("capabilities", []), ping)
require("bridgeResponseReceived:true" in bridge, "native reply/transport proof missing")
require("nativeBridgeTransportFailed(agent)" in manager, "transport classifier missing")
require("NATIVE_OPERATION_RECOVERY" in manager, "same-file operation recovery missing")
require("MAX_NATIVE_OPERATION_RETRIES = 3" in manager, "bounded retry missing")
require("preservedNativeRetryDownloads" in manager, "browser download preservation missing")
require("ROOT_DOWNLOAD_ORPHANS_RECONCILED" in manager, "cleanup diagnostic missing")
require("RC15.9.37 QUEUE-RECOVERY" in manifest, "release identity mismatch")

db_error = agent.operation_error_payload(sqlite3.OperationalError("database is locked"))
require(db_error["errorCode"] == "NATIVE_DB_BUSY" and db_error["operationFailure"], db_error)
file_error = agent.operation_error_payload(PermissionError("used by another process"))
require(file_error["errorCode"] == "NATIVE_FILE_BUSY", file_error)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    archive_dir = root / "UYAP_Arsiv" / "case" / "documents"
    archive_dir.mkdir(parents=True)
    archived = archive_dir / "verified.pdf"
    payload = b"%PDF-1.4\nverified-root-orphan-repair\n%%EOF\n"
    archived.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    connection = agent.database(root)
    connection.execute(
        """INSERT INTO documents
           (session_id, document_id, local_path, size, sha256, source_document_id, status, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'VERIFIED', ?)""",
        ("session", "document", str(archived), len(payload), digest, "1234567890", "2026-09-20T00:00:00Z"),
    )
    legacy_archived = archive_dir / "legacy.pdf"
    legacy_payload = b"%PDF-1.4\nlegacy-hash-proof\n%%EOF\n"
    legacy_archived.write_bytes(legacy_payload)
    connection.execute(
        """INSERT INTO documents
           (session_id, document_id, local_path, size, sha256, source_document_id, status, updated_at)
           VALUES (?, ?, ?, ?, ?, NULL, 'VERIFIED', ?)""",
        ("legacy-session", "legacy-document", str(legacy_archived), len(legacy_payload),
         hashlib.sha256(legacy_payload).hexdigest(), "2026-09-20T00:00:00Z"),
    )
    connection.commit()

    proven_duplicate = root / "evrak_1234567890.pdf"
    proven_duplicate.write_bytes(payload)
    different_bytes = root / "evrak_1234567890 (1).pdf"
    different_bytes.write_bytes(payload + b"different")
    legacy_duplicate = root / "evrak_7777777777.pdf"
    legacy_duplicate.write_bytes(legacy_payload)
    unverified = root / "evrak_9999999999.zip"
    unverified.write_bytes(b"unverified")
    user_file = root / "important-contract.pdf"
    user_file.write_bytes(payload)
    settled = time.time() - 600
    for path in (proven_duplicate, different_bytes, legacy_duplicate, unverified, user_file):
        path.touch()
        path.chmod(0o600)
        os.utime(path, (settled, settled))

    outcome = agent._reconcile_verified_root_downloads(root, connection, minimum_age_ms=0)
    connection.close()

    require(outcome["removed"] == 2, outcome)
    require(not proven_duplicate.exists(), "byte-identical verified duplicate retained")
    require(archived.is_file(), "archive proof file was removed")
    require(different_bytes.is_file(), "different bytes were removed")
    require(not legacy_duplicate.exists(), "legacy hash-proven duplicate retained")
    require(unverified.is_file(), "unverified canonical file was removed")
    require(user_file.is_file(), "non-UYAP user file was removed")
    require(outcome["retained"] == 2, outcome)

print("RC15.9.15 NATIVE OPERATION + ORPHAN REPAIR REGRESSION: PASS")
