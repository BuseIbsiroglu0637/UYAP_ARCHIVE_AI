import hashlib
import importlib.util
import sqlite3
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('uyap_archive_agent', HERE / 'uyap_archive_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        archive_root = root / 'UYAP_Arsiv'
        archive_root.mkdir(parents=True, exist_ok=True)
        conn = agent.database(root)
        docs = []
        expected = 320
        try:
            for i in range(expected):
                document_id = hashlib.sha256(f'doc-{i}'.encode()).hexdigest()
                title = f'Belge {i}'
                rel = f'UYAP_Arsiv/Test Mahkemesi/2026_1_Esas/Klasor/2026-09-19_{title}_d-{document_id}.bin'
                path = root / Path(*Path(rel).parts)
                path.parent.mkdir(parents=True, exist_ok=True)
                body = f'archive-bytes-{i}'.encode()
                path.write_bytes(body)
                doc = {
                    'sourceSystem': 'UYAP',
                    'institutionName': 'Test Mahkemesi',
                    'courtName': 'Test Mahkemesi',
                    'documentId': document_id,
                    'sourceDocumentId': None,
                    'caseId': '2026/1',
                    'documentTitle': title,
                    'documentDate': '2026-09-19',
                    'documentType': 'Test',
                    'unitDocumentNumber': str(i),
                    'sender': 'Test',
                    'folderNames': ['Klasor'],
                    'sourceFingerprint': f'fp-{i}',
                    'targetPath': rel,
                }
                archive_key = agent._archive_identity_key(doc)
                conn.execute(
                    '''INSERT OR REPLACE INTO documents
                       (session_id, document_id, case_id, display_name, uyap_path, local_path,
                        size, sha256, source_fingerprint, source_system, institution_name,
                        source_document_id, archive_identity_key, document_date, target_path,
                        status, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?)''',
                    ('old-session', document_id, '2026/1', title, 'Klasor', str(path), len(body), sha(body),
                     f'fp-{i}', 'UYAP', 'Test Mahkemesi', None, archive_key, '2026-09-19', rel,
                     '2026-09-19T00:00:00Z')
                )
                docs.append(doc)
            conn.commit()
        finally:
            conn.close()

        result = agent.sync_inventory({
            'action': 'sync_inventory',
            'sessionId': 'new-session',
            'downloadsRoot': str(root),
            'documents': docs,
        })
        assert result['ok'] is True
        assert len(result['unchanged']) == expected, (len(result['unchanged']), result.get('notFound'))
        assert result['notFound'] == 0
        assert result['archiveRootCount'] >= 1
        assert result['scopeCandidateRows'] >= expected
        assert all(result['matchMethods'][doc['documentId']] == 'document-id+fingerprint' for doc in docs)

        # Cross-session semantic identity must survive a changed persistence id.
        original = docs[0]
        semantic_doc = dict(original)
        semantic_doc['documentId'] = hashlib.sha256(b'changed-persistence-id').hexdigest()
        semantic_doc['sourceFingerprint'] = 'new-fingerprint'
        semantic = agent.sync_inventory({
            'action': 'sync_inventory',
            'sessionId': 'semantic-session',
            'downloadsRoot': str(root),
            'documents': [semantic_doc],
        })
        assert semantic['unchanged'] == [semantic_doc['documentId']]
        assert semantic['matchMethods'][semantic_doc['documentId']] == 'archive-identity'

    print('LOCAL BULK RECONCILIATION: PASS')


if __name__ == '__main__':
    main()
