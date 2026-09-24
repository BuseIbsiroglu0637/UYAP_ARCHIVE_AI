import hashlib
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = importlib.util.spec_from_file_location('agent_rc15916', HERE / 'uyap_archive_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def req(condition, message):
    if not condition:
        raise AssertionError(message)


def message_for(root: Path, source: Path, *, source_id='1234567890', session='s1', attempt='a1', download_id=71):
    payload = source.read_bytes() if source.exists() else b''
    return {
        'action': 'validate_download',
        'sessionId': session,
        'attemptId': attempt,
        'filename': str(source),
        'stagingPath': f'UYAP_Arsiv/_incoming/{session}/{attempt}.part',
        'targetPath': f'UYAP_Arsiv/Court/2026_1_Dosya/Folder/Document_{source_id}.xml',
        'extension': '.xml',
        'allowCanonicalAdoption': True,
        'allowLeaseAdoption': False,
        'directDownloadEvidence': True,
        'browserDownloadId': download_id,
        'browserState': 'complete',
        'browserExists': True,
        'browserFileSize': len(payload),
        'browserTotalBytes': len(payload),
        'document': {
            'sessionId': session,
            'attemptId': attempt,
            'documentId': 'doc-1',
            'downloadId': download_id,
            'attempts': 1,
            'bindingMethod': 'extension-download-id',
            'sourceDocumentId': source_id,
            'sourceSystem': 'UYAP_KURUM',
            'institutionName': 'Court',
            'caseId': '2026/1',
            'documentTitle': 'Document',
            'folderNames': ['Folder'],
            'targetPath': f'UYAP_Arsiv/Court/2026_1_Dosya/Folder/Document_{source_id}.xml',
            'sourceFingerprint': 'fp-1',
        },
    }


req(agent.AGENT_VERSION == '2.7.64', agent.AGENT_VERSION)

# 1) A stale Chrome history path with different byte size must not be adopted,
# moved to staging, or quarantined. Fresh redownload is the only action.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    source = root / 'noncanonical-current-file.xml'
    source.write_bytes(b'<root>' + b'x' * 4096 + b'</root>')
    original = source.read_bytes()
    msg = message_for(root, source)
    msg['browserFileSize'] = 244
    msg['browserTotalBytes'] = 244
    result = agent.validate_download(msg)
    req(result.get('valid') is False and result.get('errorCode') == 'DOWNLOAD_SIZE_MISMATCH', result)
    req(result.get('preAdoptionGuard') is True and result.get('sourcePreserved') is True, result)
    req(source.is_file() and source.read_bytes() == original, 'stale source was moved or modified')
    req(not (root / 'UYAP_Arsiv' / '_incoming' / 's1' / 'a1.part').exists(), 'stale source entered staging')
    bad = root / 'UYAP_Arsiv' / 'Hatalı Dosyalar'
    req(not bad.exists(), 'stale source was quarantined')

# 2) If the first native call committed successfully but its response was lost,
# the retry must replay from transfer_journal even though browser source vanished.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    source = root / 'weird-name.xml'
    source.write_bytes(b'<?xml version="1.0"?><root><ok>true</ok></root>')
    msg = message_for(root, source, session='replay-session', attempt='replay-attempt', download_id=99)
    first = agent.validate_download(msg)
    req(first.get('valid') is True, first)
    req(not source.exists(), 'successful validation did not consume browser source')
    second = agent.validate_download(msg)
    req(second.get('valid') is True and second.get('replayed') is True, second)
    req(second.get('replayMode') == 'missing-source-journal', second)
    req(Path(second['localPath']).is_file(), second)

# 3) Recent archive order uses physical file mtime, not a later DB reconciliation timestamp.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    base = root / 'UYAP_Arsiv' / 'Court' / '2026_1_Dosya' / 'Folder'
    base.mkdir(parents=True)
    newer = base / 'newer.xml'; older = base / 'older.xml'
    newer.write_text('<newer/>', encoding='utf-8'); older.write_text('<older/>', encoding='utf-8')
    now = time.time(); os.utime(newer, (now, now)); os.utime(older, (now - 3600, now - 3600))
    conn = agent.database(root)
    for doc_id, path, updated in [('newer', newer, '2026-09-19T00:00:00Z'), ('older', older, '2026-09-20T23:59:59Z')]:
        data = path.read_bytes()
        conn.execute(
            """INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ('session', doc_id, '2026/1', doc_id, 'Folder', str(path), len(data), hashlib.sha256(data).hexdigest(), 'VERIFIED', updated),
        )
    conn.commit(); conn.close()
    registry = agent.list_archive_registry({'downloadsRoot': str(root), 'limit': 10})
    docs = [d for d in registry.get('documents', []) if d.get('documentId') in {'newer','older'}]
    req(len(docs) == 2, registry)
    req(docs[0]['documentId'] == 'newer', docs)
    req(bool(docs[0].get('fileModifiedAt')), docs[0])

# 4) UI wiring is present without relying on an extension session.
popup_html = (ROOT / 'popup' / 'popup.html').read_text('utf-8')
popup_js = (ROOT / 'popup' / 'popup.js').read_text('utf-8')
workspace_html = (ROOT / 'workspace' / 'index.html').read_text('utf-8')
workspace_js = (ROOT / 'workspace' / 'workspace.js').read_text('utf-8')
manager = (ROOT / 'background' / 'download-manager.js').read_text('utf-8')
req('recentArchivePanel' in popup_html and 'GET_ARCHIVE_REGISTRY' in popup_js and 'renderRecentArchive' in popup_js, 'popup recent archive missing')
req('recentArchiveDocs' in workspace_html and 'renderRecentArchiveDocs' in workspace_js, 'workspace recent archive missing')
req('invalidRetryHint: null' in manager and 'Object.assign(doc, repairedChanges)' in manager, 'stale finalization state cleanup missing')

print('RC15.9.17 CASE HEADINGS + FINAL RECON REGRESSION: PASS')
