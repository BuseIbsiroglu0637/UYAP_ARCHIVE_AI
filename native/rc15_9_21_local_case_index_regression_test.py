import hashlib
import importlib.util
import os
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = importlib.util.spec_from_file_location('agent_rc15921', HERE / 'uyap_archive_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def req(condition, message):
    if not condition:
        raise AssertionError(message)


req(agent.AGENT_VERSION == '2.7.64', agent.AGENT_VERSION)

# 1) Case index must be complete even when the caller asks for only one document.
#    It must use DB institution/case truth and collapse duplicate session links to
#    the same physical file.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    case_a = root / 'UYAP_Arsiv' / 'Ankara 3. Genel İcra Dairesi' / '2024_61122_Dosya' / '2024_61122 (İcra Dosyası)' / 'Evrak'
    case_b = root / 'UYAP_Arsiv' / 'Kastamonu İcra Dairesi' / '2024_14998_Dosya' / '2024_14998 (İcra Dosyası)' / 'Evrak'
    case_a.mkdir(parents=True)
    case_b.mkdir(parents=True)
    a1 = case_a / 'a1.pdf'; a2 = case_a / 'a2.udf'; b1 = case_b / 'b1.pdf'
    a1.write_bytes(b'%PDF-1.4\nA1\n%%EOF')
    a2.write_bytes(b'PK\x03\x04' + b'A2' * 40)
    b1.write_bytes(b'%PDF-1.4\nB1\n%%EOF')
    now = time.time()
    os.utime(a1, (now - 120, now - 120)); os.utime(a2, (now - 60, now - 60)); os.utime(b1, (now, now))

    conn = agent.database(root)
    rows = [
        ('s1','a1','2024/61122','A1',a1,'UYAP_KURUM','Ankara 3. Genel İcra Dairesi','VERIFIED','2026-09-20T10:00:00Z'),
        ('s1','a2','2024/61122','A2',a2,'UYAP_KURUM','Ankara 3. Genel İcra Dairesi','VERIFIED','2026-09-20T10:01:00Z'),
        ('s2','a1','2024/61122','A1 duplicate run',a1,'UYAP_KURUM','Ankara 3. Genel İcra Dairesi','VERIFIED','2026-09-20T10:02:00Z'),
        ('s3','b1','2024/14998','B1',b1,'UYAP_KURUM','Kastamonu İcra Dairesi','VERIFIED','2026-09-20T10:03:00Z'),
    ]
    for session, doc_id, case_id, title, path, source, institution, status, updated in rows:
        payload = path.read_bytes()
        target = path.relative_to(root).as_posix()
        conn.execute(
            """INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
               source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,
               target_path,validation_kind,validation_mode,status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session,doc_id,case_id,title,'Evrak',str(path),len(payload),hashlib.sha256(payload).hexdigest(),
             f'fp-{doc_id}',source,institution,f'src-{doc_id}',f'id-{doc_id}','2026-09-20',target,
             'BINARY_DOCUMENT','test',status,updated)
        )
    conn.commit(); conn.close()

    db_path = root / 'UYAP_Arsiv' / '_archive.db'
    before_mtime = db_path.stat().st_mtime_ns
    registry = agent.list_archive_registry({'downloadsRoot': str(root), 'limit': 1})
    after_mtime = db_path.stat().st_mtime_ns
    req(registry.get('schemaVersion') == 4, registry)
    req(registry.get('totalCases') == 2, registry)
    req(len(registry.get('documents') or []) == 1, registry)
    req(len(registry.get('caseIndex') or []) == 2, registry)
    req(before_mtime == after_mtime, 'read-only registry scan modified _archive.db')
    by_case = {item['caseId']: item for item in registry['caseIndex']}
    req(by_case['2024/61122']['institutionName'] == 'Ankara 3. Genel İcra Dairesi', by_case)
    req(by_case['2024/61122']['documentCount'] == 2, by_case['2024/61122'])
    req(by_case['2024/61122']['verifiedCount'] == 2, by_case['2024/61122'])
    req(by_case['2024/61122']['physicalReadyCount'] == 2, by_case['2024/61122'])
    req(by_case['2024/14998']['documentCount'] == 1, by_case['2024/14998'])
    req(registry.get('duplicateRunLinks') >= 1, registry)

# 2) Code must keep first-open registry browsing read-only and include corruption salvage.
agent_src = (HERE / 'uyap_archive_agent.py').read_text('utf-8')
req('?mode=ro' in agent_src, 'registry DB is not opened read-only')
req('REGISTRY_DB_PARTIAL_RECOVERY' in agent_src and 'rowid BETWEEN ? AND ?' in agent_src, 'partial DB salvage missing')
req('"caseIndex": case_index' in agent_src, 'native case index output missing')

# 3) Popup and workspace must render Institution / Office + Case No + counts.
popup_html = (ROOT / 'popup' / 'popup.html').read_text('utf-8')
popup_js = (ROOT / 'popup' / 'popup.js').read_text('utf-8')
workspace_html = (ROOT / 'workspace' / 'index.html').read_text('utf-8')
workspace_js = (ROOT / 'workspace' / 'workspace.js').read_text('utf-8')
req('Kurum / Daire' in popup_html and 'Dosya No' in popup_html and 'Evrak' in popup_html, 'popup case table headers missing')
req('recentArchiveRegistry?.caseIndex' in popup_js, 'popup does not consume native case index')
req('archiveCaseIndexRows' in workspace_html and 'Kurum / Daire' in workspace_html and 'Dosya No' in workspace_html, 'workspace full case table missing')
req('state.archiveRegistry?.caseIndex' in workspace_js and 'renderArchiveCaseIndexTable' in workspace_js, 'workspace case index renderer missing')

# 4) Frame injection diagnostics must expose session-relative counters rather than
#    making cumulative service-worker failures look like current-session failures.
frame_client = (ROOT / 'background' / 'frame-client.js').read_text('utf-8')
manager = (ROOT / 'background' / 'download-manager.js').read_text('utf-8')
req('frameClientScopeId' in frame_client and 'scopeId: frameClientScopeId' in frame_client, 'frame client scope id missing')
req('frameClientBaseline: getFrameClientStats()' in manager, 'session frame baseline missing')
req('frameClientSession' in manager and 'session_delta' in manager, 'session-relative frame diagnostic missing')

print('RC15.9.21 LOCAL CASE INDEX REGRESSION: PASS')
