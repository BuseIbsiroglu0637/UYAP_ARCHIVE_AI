import hashlib
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = importlib.util.spec_from_file_location('agent_rc15917', HERE / 'uyap_archive_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def req(condition, message):
    if not condition:
        raise AssertionError(message)


req(tuple(map(int, agent.AGENT_VERSION.split("."))) >= (2, 7, 64), agent.AGENT_VERSION)

# 1) Finalization must validate historical/local rows by detected content truth,
# not by a stale/generic suffix. A valid XML body stored as .bin remains valid.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'content-truth-final'
    doc_id = 'doc-xml-bin'
    path = root / 'UYAP_Arsiv' / 'Court' / '2026_1_Dosya' / '2026_1 (Hukuk Dava Dosyası)' / 'Folder' / 'document.bin'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b'<?xml version="1.0"?><root><value>ok</value></root>'
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    conn = agent.database(root)
    conn.execute(
        """INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
           source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,
           target_path,status,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session, doc_id, '2026/1', 'Document', 'Folder', str(path), len(payload), digest,
         'fp-xml', 'UYAP_KURUM', 'Court', '1001', 'identity-xml', '2026-01-01',
         path.relative_to(root).as_posix(), 'VERIFIED', '2026-09-20T00:00:00Z')
    )
    conn.commit(); conn.close()
    result = agent.finalize_archive({
        'downloadsRoot': str(root), 'sessionId': session, 'expected': 1,
        'sourceInventoryTotal': 1, 'documentIds': [doc_id], 'reviewRequired': 0,
        'allowPartial': False, 'partSizeBytes': 64 * 1024 * 1024, 'maxDocumentsPerPart': 500,
        'documents': [{
            'documentId': doc_id, 'caseId': '2026/1', 'sourceSystem': 'UYAP_KURUM',
            'institutionName': 'Court', 'sourceDocumentId': '1001', 'sourceDocumentIdOrigin': 'learned-download',
            'sourceFingerprint': 'fp-xml', 'documentDate': '2026-01-01', 'folderNames': ['Folder'],
            'targetPath': path.relative_to(root).as_posix(),
        }],
    })
    req(result.get('archiveStatus') == 'VERIFIED_COMPLETE', result)
    req(result.get('integrity', {}).get('invalidCount') == 0, result)
    req(result.get('integrity', {}).get('gatePassed') is True, result)

# 2) When two document identities point to the same local path, but only one
# matches the supplied canonical target identity, keep the owner and make the
# other document recoverable instead of blocking forever as duplicatePath.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'duplicate-path-recovery'
    path = root / 'UYAP_Arsiv' / 'Court' / '2026_2_Dosya' / '2026_2 (Hukuk Dava Dosyası)' / 'Folder' / 'owner_2001.xml'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b'<?xml version="1.0"?><root><owner>true</owner></root>'
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    conn = agent.database(root)
    rows = [
        ('owner', 'Owner', 'fp-owner', '2001', path.relative_to(root).as_posix()),
        ('other', 'Other', 'fp-other', '2002', 'UYAP_Arsiv/Court/2026_2_Dosya/2026_2 (Hukuk Dava Dosyası)/Folder/other_2002.xml'),
    ]
    for doc_id, title, fp, source_id, target in rows:
        conn.execute(
            """INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
               source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,
               target_path,status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session, doc_id, '2026/2', title, 'Folder', str(path), len(payload), digest,
             fp, 'UYAP_KURUM', 'Court', source_id, f'identity-{doc_id}', '2026-01-01', target,
             'VERIFIED', '2026-09-20T00:00:00Z')
        )
    conn.commit(); conn.close()
    result = agent.finalize_archive({
        'downloadsRoot': str(root), 'sessionId': session, 'expected': 2,
        'sourceInventoryTotal': 2, 'documentIds': ['owner','other'], 'reviewRequired': 0,
        'allowPartial': True, 'partSizeBytes': 64 * 1024 * 1024, 'maxDocumentsPerPart': 500,
        'documents': [
            {'documentId':'owner','caseId':'2026/2','sourceSystem':'UYAP_KURUM','institutionName':'Court','sourceDocumentId':'2001','sourceDocumentIdOrigin':'learned-download','sourceFingerprint':'fp-owner','documentDate':'2026-01-01','folderNames':['Folder'],'targetPath':path.relative_to(root).as_posix()},
            {'documentId':'other','caseId':'2026/2','sourceSystem':'UYAP_KURUM','institutionName':'Court','sourceDocumentId':'2002','sourceDocumentIdOrigin':'learned-download','sourceFingerprint':'fp-other','documentDate':'2026-01-01','folderNames':['Folder'],'targetPath':'UYAP_Arsiv/Court/2026_2_Dosya/2026_2 (Hukuk Dava Dosyası)/Folder/other_2002.xml'},
        ],
    })
    req(result.get('archiveStatus') == 'PARTIAL_WITH_ERRORS', result)
    req('other' in (result.get('invalidIds') or []), result)
    req(result.get('integrity', {}).get('duplicatePathCount') == 0, result)
    req(result.get('verified') == 1, result)

# 3) UI must group recent local archive entries by case file and show case titles,
# rather than individual document titles.
popup_html = (ROOT / 'popup' / 'popup.html').read_text('utf-8')
popup_js = (ROOT / 'popup' / 'popup.js').read_text('utf-8')
workspace_html = (ROOT / 'workspace' / 'index.html').read_text('utf-8')
workspace_js = (ROOT / 'workspace' / 'workspace.js').read_text('utf-8')
req('Kurum / Daire ve dosya indeksi' in popup_html, 'popup case index heading missing')
req('recentArchiveCaseGroups' in popup_js and 'archiveCaseDescriptor' in popup_js, 'popup case grouping missing')
req('recentArchiveRegistry?.caseIndex' in popup_js, 'popup native case index missing')
req('Kurum / Daire ve dosya indeksi' in workspace_html, 'workspace case index heading missing')
req('recentArchiveCaseGroups' in workspace_js and 'group.count' in workspace_js, 'workspace case grouping missing')

# 4) Local reconciliation must preserve HTML proof columns when importing an old
# VERIFIED row into the current session, so finalization does not forget it.
agent_src = (HERE / 'uyap_archive_agent.py').read_text('utf-8')
req('updated_at, validation_kind, validation_mode' in agent_src, 'sync SELECT does not carry validation proof')
req("(row_meta or {}).get(\"validation_kind\")" in agent_src, 'sync INSERT does not carry validation kind')
req('detected_extension = detect_extension(path, path.suffix).lower()' in agent_src, 'finalization content-truth detection missing')

print('RC15.9.17 CASE HEADINGS + FINAL RECON REGRESSION: PASS')
