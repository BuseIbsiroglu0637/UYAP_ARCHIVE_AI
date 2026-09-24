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


req(agent.AGENT_VERSION == "2.7.64", agent.AGENT_VERSION)
req(agent._canonical_case_id("2025_607") == "2025/607", "underscore case id not normalized")
req(agent._canonical_case_id("2025-607") == "2025/607", "dash case id not normalized")

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary).resolve()
    relative = "UYAP_Arsiv/Kastamonu_2_Aile_Mahkemesi/2025_607_Dosya/Evraklar/20_09_2026_Eski_Evrak_d-old.pdf"
    physical = root.joinpath(*relative.split("/"))
    physical.parent.mkdir(parents=True)
    payload = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"
    physical.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()

    old_document = {
        "sourceSystem": "UYAP", "institutionName": "Kastamonu 2. Aile Mahkemesi",
        "caseId": "2025_607", "folderNames": ["Evraklar"], "documentTitle": "Eski Evrak",
        "documentDate": "20/09/2026", "documentType": "PDF", "sender": "",
    }
    connection = agent.database(root)
    connection.execute(
        """INSERT INTO documents
           (session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
            source_fingerprint,source_system,institution_name,source_document_id,
            archive_identity_key,document_date,target_path,status,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("old-session", "old-render-id", "2025_607", "Eski Evrak", "Evraklar",
         r"Z:\\OldComputer\\UYAP_Arsiv\\missing\\old.pdf", len(payload), digest, "old-fingerprint",
         "UYAP", "Kastamonu 2. Aile Mahkemesi", None, None,
         "20/09/2026", relative, "VERIFIED", "2026-09-20T12:00:00Z")
    )
    connection.commit()
    connection.close()

    current = {
        **old_document,
        "caseId": "2025/607", "documentId": "new-render-id", "sourceFingerprint": "new-fingerprint",
        "targetPath": relative,
    }
    result = agent.sync_inventory({
        "action": "sync_inventory", "sessionId": "new-session",
        "downloadsRoot": str(root), "documents": [current],
    })
    req(result["unchanged"] == ["new-render-id"], result)
    proof = result["physicalProofs"]["new-render-id"]
    req(proof["level"] == "sha256" and proof["sha256"] == digest, proof)
    req(result["matchMethods"]["new-render-id"] == "verified-physical-path", result)

    registry = agent.list_archive_registry({"downloadsRoot": str(root), "limit": 100})
    req(registry["physicalReadyFiles"] == 1, registry)

manager = (HERE.parent / "background" / "download-manager.js").read_text(encoding="utf-8")
req("LOCAL_ARCHIVE_RECONCILIATION_VERSION = 4" in manager, "reconciliation cache version missing")
req("session?.localArchiveReconciliationVersion" in manager, "large-case reconciliation cache is not invalidated")

print("RC15.9.25 cross-PC archive reconciliation regression: PASS")
