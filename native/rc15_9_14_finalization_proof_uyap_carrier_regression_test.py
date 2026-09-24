import json
import tempfile
from pathlib import Path

import uyap_archive_agent as agent


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def message(root: Path, payload: bytes, identifier: str, suffix: str):
    source = root / f"evrak_{identifier}{suffix}"
    source.write_bytes(payload)
    return {
        "action": "validate_download",
        "sessionId": f"session-{identifier}",
        "attemptId": f"attempt-{identifier}",
        "filename": str(source),
        "stagingPath": f"UYAP_Arsiv/_incoming/session-{identifier}/{identifier}.part",
        "targetPath": f"UYAP_Arsiv/Court/Case/Documents/Document_{identifier}.pdf",
        "extension": suffix,
        "allowCanonicalAdoption": True,
        "directDownloadEvidence": True,
        "browserDownloadId": 17,
        "browserState": "complete",
        "browserExists": True,
        "browserFileSize": len(payload),
        "browserTotalBytes": len(payload),
        "document": {
            "sessionId": f"session-{identifier}",
            "documentId": identifier,
            "attemptId": f"attempt-{identifier}",
            "downloadId": 17,
            "bindingMethod": "extension-download-id",
            "attempts": 1,
            "sourceSystem": "UYAP_KURUM",
            "institutionName": "Court",
            "caseId": "Case",
            "documentTitle": "KEP",
            "sourceFingerprint": f"fp-{identifier}",
        },
    }


def final_message(root: Path, result: dict, identifier: str):
    return {
        "downloadsRoot": str(root),
        "sessionId": f"session-{identifier}",
        "expected": 1,
        "sourceInventoryTotal": 1,
        "documents": [{
            "documentId": identifier,
            "sourceSystem": "UYAP_KURUM",
            "institutionName": "Court",
            "caseId": "Case",
            "sourceFingerprint": f"fp-{identifier}",
            "targetPath": result["targetPath"],
        }],
        "documentIds": [identifier],
        "reviewRequired": 0,
        "allowPartial": False,
    }


# 1) Regression: a tiny legitimate HTML document validated during download must
# remain valid at finalization even though context-free HTML validation rejects it.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    identifier = "1234567890"
    payload = b"<html><body><b>KEP Paketi:</b> tarihinde gonderilen KEP iletisi ektedir.</body></html>"
    result = agent.dispatch(message(root, payload, identifier, ".html"))
    require(result.get("valid") is True, result)
    require(result.get("htmlDocumentValidated") is True, result)
    path = Path(result["localPath"])
    require(agent.validate_content(path, ".html")[0] is False, "test fixture must require preserved provenance")

    con = agent.database(root)
    row = con.execute(
        "SELECT validation_kind, validation_mode FROM documents WHERE session_id=? AND document_id=?",
        (f"session-{identifier}", identifier),
    ).fetchone()
    require(row and row[0] == "HTML_DOCUMENT" and row[1], row)

    # Simulate an RC15.9.13 database: the new persisted columns did not exist,
    # but transfer_journal already contained htmlDocumentValidated + exact hash.
    con.execute(
        "UPDATE documents SET validation_kind=NULL, validation_mode=NULL, status='INTEGRITY_FAILED' WHERE session_id=? AND document_id=?",
        (f"session-{identifier}", identifier),
    )
    con.commit()
    con.close()

    final = agent.finalize_archive(final_message(root, result, identifier))
    require(final["archiveStatus"] == "VERIFIED_COMPLETE", final)
    require(final["integrity"]["gatePassed"] is True, final)
    require(final["integrity"]["invalidCount"] == 0, final)


# 2) Security regression: the journal proof is byte-bound. If its SHA is wrong
# and the DB-native proof is absent, finalization must fail closed.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    identifier = "1234567891"
    payload = b"<html><body><b>KEP Paketi:</b> byte-bound proof test</body></html>"
    result = agent.dispatch(message(root, payload, identifier, ".html"))
    require(result.get("valid") is True, result)
    con = agent.database(root)
    con.execute(
        "UPDATE documents SET validation_kind=NULL, validation_mode=NULL WHERE session_id=? AND document_id=?",
        (f"session-{identifier}", identifier),
    )
    rows = con.execute(
        "SELECT attempt_id, result_json FROM transfer_journal WHERE committed=1"
    ).fetchall()
    require(len(rows) == 1, rows)
    attempt_id, raw = rows[0]
    journal = json.loads(raw)
    journal["sha256"] = "0" * 64
    con.execute(
        "UPDATE transfer_journal SET result_json=? WHERE attempt_id=?",
        (json.dumps(journal, ensure_ascii=False), attempt_id),
    )
    con.commit()
    con.close()

    final = agent.finalize_archive(final_message(root, result, identifier))
    require(final["integrity"]["gatePassed"] is False, final)
    require(final["integrity"]["invalidCount"] == 1, final)


# 3) UYAP carrier filenames are identity evidence, not the archive type. A real
# evrak_<id>.uyap file must be adopted, then named by detected content bytes.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    identifier = "1234567892"
    payload = b'<?xml version="1.0" encoding="UTF-8"?><root><document>uyap</document></root>'
    result = agent.dispatch(message(root, payload, identifier, ".uyap"))
    require(result.get("valid") is True, result)
    require(result.get("sourceDocumentId") == identifier, result)
    require(result.get("extension") == ".xml", result)
    require(Path(result["localPath"]).suffix.lower() == ".xml", result)
    require(Path(result["localPath"]).read_bytes() == payload, "carrier bytes changed")


manager = (Path(__file__).resolve().parents[1] / "background" / "download-manager.js").read_text(encoding="utf-8")
require("isFinalizationProofRecoverableHtml" in manager, "legacy poisoned-HTML repair guard missing")
require("FINALIZATION_HTML_PROOF_RECOVERED" in manager, "legacy repair diagnostic missing")

print("RC15.9.15 FINALIZATION PROOF + .UYAP CARRIER REGRESSION: PASS")
