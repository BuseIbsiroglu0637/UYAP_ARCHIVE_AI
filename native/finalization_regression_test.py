from pathlib import Path
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uyap_archive_agent as agent


def minimal_pdf() -> bytes:
    header = b"%PDF-1.4\n"
    obj = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    xref_offset = len(header) + len(obj)
    xref = (
        b"xref\n0 2\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
    )
    return header + obj + xref


def add_verified(connection, *, session_id, document_id, case_id, institution, path, target_path, fingerprint):
    digest = agent.sha256_file(path)
    size = path.stat().st_size
    now = "2026-09-18T14:00:00Z"
    connection.execute(
        """INSERT INTO documents
           (session_id, document_id, case_id, display_name, uyap_path, local_path,
            size, sha256, source_fingerprint, source_system, institution_name,
            source_document_id, archive_identity_key, document_date, target_path,
            status, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?)""",
        (
            session_id, document_id, case_id, document_id, "", str(path), size, digest,
            fingerprint, "UYAP_KURUM", institution, None, f"archive-{document_id}",
            "18/09/2026", target_path, now,
        ),
    )


def run_case(extra_folder: bool):
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        institution = "Kastamonu 2. Asliye Hukuk Mahkemesi"
        case_folder = "2026-221_Dosya"
        case_root = root / "UYAP_Arsiv" / institution / case_folder
        doc_dir = case_root / "Dilekceler" if extra_folder else case_root
        doc_dir.mkdir(parents=True, exist_ok=True)
        path = doc_dir / "Cevap_Dilekcesi_doc1.pdf"
        path.write_bytes(minimal_pdf())
        target_path = path.relative_to(root).as_posix()

        connection = agent.database(root)
        add_verified(
            connection,
            session_id="session-direct" if not extra_folder else "session-nested",
            document_id="doc1",
            case_id="2026/221",
            institution=institution,
            path=path,
            target_path=target_path,
            fingerprint="fp-doc1",
        )
        connection.commit()
        connection.close()

        session_id = "session-direct" if not extra_folder else "session-nested"
        result = agent.finalize_archive({
            "downloadsRoot": str(root),
            "sessionId": session_id,
            "expected": 1,
            "sourceInventoryTotal": 1,
            "documents": [{
                "documentId": "doc1",
                "sourceSystem": "UYAP_KURUM",
                "institutionName": institution,
                "courtName": institution,
                "caseId": "2026/221",
                "sourceFingerprint": "fp-doc1",
                "targetPath": target_path,
            }],
            "documentIds": ["doc1"],
            "reviewRequired": 0,
            "allowPartial": False,
        })
        assert result["archiveStatus"] == "VERIFIED_COMPLETE", result
        assert result["integrity"]["gatePassed"] is True, result
        assert result["zipPath"], result
        zip_path = Path(result["zipPath"])
        assert zip_path.is_file(), result
        assert Path(result["manifestPath"]).is_file(), result
        assert zip_path.parent.parent == case_root, (zip_path, case_root)
        assert zip_path.parent.name.startswith("Final_Paket_"), zip_path.parent
        assert Path(result["finalPackagePath"]) == zip_path.parent
        assert agent.sha256_file(zip_path) == result["zipSha256"]
        for name in ("_manifest.json", "_parts.json", "_verification.csv", "_archive-report.html", "_archive.db", "_checksums.sha256"):
            assert (zip_path.parent / name).is_file(), name


if __name__ == "__main__":
    run_case(extra_folder=False)
    run_case(extra_folder=True)
    print("FINALIZATION REGRESSION: PASS")


def run_poisoned_status_recovery():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        institution = "Kastamonu 2. Asliye Hukuk Mahkemesi"
        case_root = root / "UYAP_Arsiv" / institution / "2026-221_Dosya"
        case_root.mkdir(parents=True, exist_ok=True)
        path = case_root / "Karar_doc1.pdf"
        path.write_bytes(minimal_pdf())
        target_path = path.relative_to(root).as_posix()
        session_id = "session-poisoned"

        connection = agent.database(root)
        add_verified(connection, session_id=session_id, document_id="doc1", case_id="2026/221",
                     institution=institution, path=path, target_path=target_path, fingerprint="fp-doc1")
        connection.execute("UPDATE documents SET status='INTEGRITY_FAILED' WHERE session_id=? AND document_id=?", (session_id, "doc1"))
        connection.commit()
        connection.close()

        result = agent.finalize_archive({
            "downloadsRoot": str(root), "sessionId": session_id, "expected": 1,
            "sourceInventoryTotal": 1, "documents": [{
                "documentId": "doc1", "sourceSystem": "UYAP_KURUM", "institutionName": institution,
                "courtName": institution, "caseId": "2026/221", "sourceFingerprint": "fp-doc1",
                "targetPath": target_path,
            }], "documentIds": ["doc1"], "reviewRequired": 0, "allowPartial": False,
        })
        assert result["archiveStatus"] == "VERIFIED_COMPLETE", result
        connection = agent.database(root)
        status = connection.execute("SELECT status FROM documents WHERE session_id=? AND document_id=?", (session_id, "doc1")).fetchone()[0]
        connection.close()
        assert status == "VERIFIED", status


def run_user_excluded_extra_row():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        institution = "Kastamonu 2. Asliye Hukuk Mahkemesi"
        case_root = root / "UYAP_Arsiv" / institution / "2026-221_Dosya"
        case_root.mkdir(parents=True, exist_ok=True)
        path1 = case_root / "Karar_doc1.pdf"
        path2 = case_root / "HTML_Yanit_doc2.pdf"
        path1.write_bytes(minimal_pdf())
        path2.write_bytes(minimal_pdf())
        target1 = path1.relative_to(root).as_posix()
        target2 = path2.relative_to(root).as_posix()
        session_id = "session-excluded"

        connection = agent.database(root)
        add_verified(connection, session_id=session_id, document_id="doc1", case_id="2026/221",
                     institution=institution, path=path1, target_path=target1, fingerprint="fp-doc1")
        add_verified(connection, session_id=session_id, document_id="doc2", case_id="2026/221",
                     institution=institution, path=path2, target_path=target2, fingerprint="fp-doc2")
        connection.commit()
        connection.close()

        result = agent.finalize_archive({
            "downloadsRoot": str(root), "sessionId": session_id, "expected": 1,
            "sourceInventoryTotal": 2,
            "userExcludedDocuments": [{"documentId": "doc2", "reason": "USER_CONFIRMED_HTML_RESPONSE"}],
            "documents": [{
                "documentId": "doc1", "sourceSystem": "UYAP_KURUM", "institutionName": institution,
                "courtName": institution, "caseId": "2026/221", "sourceFingerprint": "fp-doc1",
                "targetPath": target1,
            }], "documentIds": ["doc1"], "reviewRequired": 0, "allowPartial": False,
        })
        assert result["archiveStatus"] == "VERIFIED_COMPLETE", result
        assert result["integrity"]["unexpectedCount"] == 0, result


if __name__ == "__main__":
    run_poisoned_status_recovery()
    run_user_excluded_extra_row()
    print("FINALIZATION LEGACY RECOVERY: PASS")
