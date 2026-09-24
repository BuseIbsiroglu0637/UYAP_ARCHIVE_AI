#!/usr/bin/env python3
import hashlib
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("uyap_archive_agent", HERE / "uyap_archive_agent.py")
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def req(value, message):
    if not value:
        raise AssertionError(message)


def insert_row(root, document_id, local_path, payload, digest, *, title, target, validation_kind=None, source_id=None):
    connection = agent.database(root)
    connection.execute(
        """INSERT INTO documents
           (session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
            source_fingerprint,source_system,institution_name,source_document_id,
            archive_identity_key,document_date,target_path,status,updated_at,validation_kind)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("old-session", document_id, "2025_607", title, "Evraklar", local_path,
         len(payload), digest, "old-fingerprint", "UYAP", "Kastamonu 2. Aile Mahkemesi",
         source_id, None, "20/09/2026", target, "VERIFIED", "2026-09-20T12:00:00Z", validation_kind),
    )
    connection.commit()
    connection.close()


def current(document_id, title, target):
    return {
        "sourceSystem": "UYAP", "institutionName": "Kastamonu 2. Aile Mahkemesi",
        "caseId": "2025/607", "folderNames": ["Evraklar"], "documentTitle": title,
        "documentDate": "20/09/2026", "documentType": "XML", "sender": "",
        "documentId": document_id, "sourceFingerprint": "new-fingerprint", "targetPath": target,
    }


req(agent.AGENT_VERSION == "2.7.64", agent.AGENT_VERSION)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    (root / "UYAP_Arsiv").mkdir()

    payload = b'<?xml version="1.0"?><evrak><metin>guvenli</metin></evrak>'
    digest = hashlib.sha256(payload).hexdigest()
    loose = root / "evrak_123456.xml"
    loose.write_bytes(payload)
    target = "UYAP_Arsiv/Kastamonu_2_Aile_Mahkemesi/2025_607_Dosya/Evraklar/20_09_2026_Eski_Evrak_d-new.xml"
    insert_row(
        root, "old-id", r"Z:\\OldPC\\UYAP_Arsiv\\Kurum\\old-canonical.xml",
        payload, digest, title="Eski Evrak", target=target, source_id="123456"
    )

    result = agent.sync_inventory({
        "sessionId": "new-session", "downloadsRoot": str(root),
        "documents": [current("new-id", "Eski Evrak", target)],
    })
    req(result["unchanged"] == ["new-id"], result)
    req(result["matchMethods"]["new-id"] == "legacy-loose-import", result)
    req(result["legacyLooseImported"] == 1, result)
    canonical = root.joinpath(*target.split("/"))
    req(loose.is_file() and loose.read_bytes() == payload, "legacy source must be preserved")
    req(canonical.is_file() and canonical.read_bytes() == payload, "canonical copy/link missing")
    req(result["physicalProofs"]["new-id"]["sha256"] == digest, result)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    (root / "UYAP_Arsiv").mkdir()
    login = b"<html><body><form><input type='password'></form>UYAP Giris</body></html>"
    digest = hashlib.sha256(login).hexdigest()
    (root / "evrak_654321.html").write_bytes(login)
    target = "UYAP_Arsiv/Kurum/2025_607_Dosya/Evraklar/Oturum_d-new.html"
    insert_row(root, "old-html", r"D:\\UYAP\\evrak_654321.html", login, digest, title="Oturum", target=target)
    result = agent.sync_inventory({
        "sessionId": "html-session", "downloadsRoot": str(root),
        "documents": [current("new-html", "Oturum", target)],
    })
    req(result["unchanged"] == [] and result["notFound"] == 1, result)
    req(not root.joinpath(*target.split("/")).exists(), "login HTML must not be imported")

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    (root / "UYAP_Arsiv").mkdir()
    payload = b"<evrak>changed</evrak>"
    (root / "evrak_777777.xml").write_bytes(payload)
    target = "UYAP_Arsiv/Kurum/2025_607_Dosya/Evraklar/Hash_d-new.xml"
    insert_row(root, "old-hash", r"E:\\UYAP\\evrak_777777.xml", payload, "0" * 64, title="Hash", target=target)
    result = agent.sync_inventory({
        "sessionId": "hash-session", "downloadsRoot": str(root),
        "documents": [current("new-hash", "Hash", target)],
    })
    req(result["unchanged"] == [], result)
    req(not root.joinpath(*target.split("/")).exists(), "hash mismatch must not be imported")

manager = (HERE.parent / "background" / "download-manager.js").read_text(encoding="utf-8")
bridge = (HERE.parent / "background" / "native-agent.js").read_text(encoding="utf-8")
req("LOCAL_ARCHIVE_RECONCILIATION_VERSION = 4" in manager, "scan cache version missing")
req("legacy_loose_archive_import_v2" in bridge, "required capability missing")

print("RC15.9.27 source-id loose archive import regression: PASS")
