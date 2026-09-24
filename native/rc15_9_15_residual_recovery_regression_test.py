import hashlib
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('agent_rc15915', HERE / 'uyap_archive_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def req(condition, message):
    if not condition:
        raise AssertionError(message)


def exact_message(root: Path, source: Path, source_id='1234567890', attempt='attempt-1', session='session-1', download_id=41):
    return {
        'sessionId': session,
        'attemptId': attempt,
        'stagingPath': f'UYAP_Arsiv/_incoming/{session}/{attempt}.part',
        'allowCanonicalAdoption': True,
        'allowLeaseAdoption': False,
        'directDownloadEvidence': True,
        'browserDownloadId': download_id,
        'browserState': 'complete',
        'document': {
            'sessionId': session,
            'attemptId': attempt,
            'downloadId': download_id,
            'bindingMethod': 'extension-download-id',
            'sourceDocumentId': source_id,
        },
    }


req(agent.AGENT_VERSION == '2.7.64', agent.AGENT_VERSION)

# 1) Exact browser-download identity is stronger provenance than a non-canonical
# local filename. The bytes must still pass content validation later.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    source = root / 'kurum-response.xml'
    source.write_text('<root><ok>true</ok></root>', encoding='utf-8')
    msg = exact_message(root, source)
    staged, discovered_root = agent.normalize_source_to_staging(source.resolve(), msg)
    req(discovered_root == root.resolve(), discovered_root)
    req(staged.is_file(), staged)
    req(staged.read_text(encoding='utf-8').startswith('<root>'), 'exact-bound XML was not staged')

# Same basename mismatch without exact DownloadItem/attempt/session proof must fail closed.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    source = root / 'untrusted-name.xml'
    source.write_text('<root/>', encoding='utf-8')
    msg = exact_message(root, source)
    msg['directDownloadEvidence'] = False
    failed = False
    try:
        agent.normalize_source_to_staging(source.resolve(), msg)
    except ValueError:
        failed = True
    req(failed, 'non-canonical filename bypassed without exact browser evidence')

# 2) If an exact-bound download meets a different stale file in the same staging
# slot, preserve both and continue with the exact-bound bytes in a sibling slot.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'session-collision'
    attempt = 'attempt-collision'
    source = root / 'evrak_9876543210.xml'
    source.write_bytes(b'<new>browser-bound</new>')
    staging = root / 'UYAP_Arsiv' / '_incoming' / session / f'{attempt}.part'
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(b'<old>stale</old>')
    msg = exact_message(root, source, source_id='9876543210', attempt=attempt, session=session, download_id=77)
    moved, _ = agent.normalize_source_to_staging(source.resolve(), msg)
    req(staging.read_bytes() == b'<old>stale</old>', 'stale staged bytes were overwritten')
    req(moved != staging, 'collision did not receive a sibling staging path')
    req(moved.read_bytes() == b'<new>browser-bound</new>', 'exact-bound bytes were not preserved')
    req('__bound_' in moved.name, moved.name)

# 3) Finalization must trust its own byte revalidation over stale browser/UI state.
# A valid archived file marked INTEGRITY_FAILED is repaired to VERIFIED, and an old
# reviewRequired=1 must not block the full gate once the bytes are proven.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'session-final-reconcile'
    document_id = 'doc-1'
    path = root / 'UYAP_Arsiv' / 'Court' / '2026_1_Dosya' / 'Folder' / 'document.xml'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b'<?xml version="1.0"?><root><value>1</value></root>'
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    conn = agent.database(root)
    conn.execute(
        """INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
           source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,
           target_path,validation_kind,validation_mode,status,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session, document_id, '2026/1', 'Document', 'Folder/Document', str(path), len(payload), digest,
         'fp-1', 'UYAP_KURUM', 'Court', '1234567890', 'identity-1', '2026-01-01',
         path.relative_to(root).as_posix(), None, None, 'INTEGRITY_FAILED', '2026-09-20T00:00:00Z')
    )
    conn.commit()
    conn.close()

    result = agent.finalize_archive({
        'downloadsRoot': str(root),
        'sessionId': session,
        'expected': 1,
        'sourceInventoryTotal': 1,
        'documentIds': [document_id],
        'reviewRequired': 1,  # deliberately stale browser-side observation
        'allowPartial': False,
        'partSizeBytes': 64 * 1024 * 1024,
        'maxDocumentsPerPart': 500,
        'documents': [{
            'documentId': document_id,
            'caseId': '2026/1',
            'sourceSystem': 'UYAP_KURUM',
            'institutionName': 'Court',
            'sourceDocumentId': '1234567890',
            'sourceDocumentIdOrigin': 'learned-download',
            'identityMethod': 'parent-and-label',
            'sourceFingerprint': 'fp-1',
            'documentDate': '2026-01-01',
            'folderNames': ['Folder'],
        }],
    })
    req(result.get('ok') is True, result)
    req(result.get('archiveStatus') == 'VERIFIED_COMPLETE', result)
    req(result.get('integrity', {}).get('gatePassed') is True, result)
    req(result.get('integrity', {}).get('reviewRequiredCount') == 0, result)
    req(result.get('integrity', {}).get('reportedReviewRequiredCount') == 1, result)
    req(document_id in (result.get('repairedIds') or []), result)

print('RC15.9.15 RESIDUAL RECOVERY REGRESSION: PASS')
