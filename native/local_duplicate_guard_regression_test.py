from pathlib import Path
import hashlib, tempfile, time
import uyap_archive_agent as agent


def sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def insert_verified(root: Path, *, session_id, document_id, source_id, case_id, institution, local_path, digest):
    con = agent.database(root)
    try:
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        con.execute(
            """INSERT OR REPLACE INTO documents
            (session_id, document_id, case_id, display_name, uyap_path, local_path, size, sha256,
             source_fingerprint, source_system, institution_name, source_document_id,
             archive_identity_key, document_date, target_path, status, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?)""",
            (session_id, document_id, case_id, 'Test Evrak', 'Klasor', str(local_path), local_path.stat().st_size,
             digest, 'fp-old', 'UYAP_KURUM', institution, source_id, 'archive-key-test', '2026-01-01',
             f'{case_id}/Klasor/Test_Evrak_{source_id}.pdf', now)
        )
        con.commit()
    finally:
        con.close()


def main():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        target = root / 'UYAP_Arsiv' / 'Test Mahkemesi' / '2026_1' / 'Klasor' / 'Test_Evrak_12345.pdf'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'%PDF-1.4\n% duplicate guard regression\n%%EOF\n')
        digest = sha(target)
        insert_verified(root, session_id='old', document_id='old-doc', source_id='12345', case_id='2026/1', institution='Test Mahkemesi', local_path=target, digest=digest)
        msg = {
            'sessionId': 'new', 'downloadsRoot': str(root),
            'documents': [{
                'sourceSystem': 'UYAP_KURUM', 'institutionName': 'Test Mahkemesi', 'courtName': 'Test Mahkemesi',
                'documentId': 'new-doc', 'sourceDocumentId': '12345', 'sourceIdentityHash': 'x', 'caseId': '2026/1',
                'documentTitle': 'Test Evrak', 'documentDate': '2026-01-01', 'folderNames': ['Klasor'],
                'sourceFingerprint': 'fp-new', 'targetPath': '2026_1/Klasor/Test_Evrak_12345.pdf'
            }]
        }
        result = agent.sync_inventory(msg)
        assert result['ok'] is True, result
        assert result['unchanged'] == ['new-doc'], result
        assert result['hashes']['new-doc'] == digest, result
        print('LOCAL DUPLICATE GUARD NATIVE MATCH: PASS')

    source = (Path(__file__).parent.parent / 'background' / 'download-manager.js').read_text(encoding='utf-8')
    guard_pos = source.find('guardDocumentAgainstLocalArchive(next, session)')
    download_pos = source.find('await startDocumentDownload(next, { queueLookupMs });', guard_pos)
    assert guard_pos >= 0 and download_pos > guard_pos
    assert 'DOWNLOAD_BLOCKED_LOCAL_DUPLICATE' in source
    assert 'LOCAL_ARCHIVE_AMBIGUOUS' in source
    print('LOCAL DUPLICATE GUARD QUEUE ORDER: PASS')


if __name__ == '__main__':
    main()
