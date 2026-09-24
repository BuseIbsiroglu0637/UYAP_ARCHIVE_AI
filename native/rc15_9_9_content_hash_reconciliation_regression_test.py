import hashlib, tempfile, sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import uyap_archive_agent as agent

def req(cond, msg):
    if not cond: raise AssertionError(msg)

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

req(agent.AGENT_VERSION == "2.7.64", agent.AGENT_VERSION)
with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    archive=root/'UYAP_Arsiv'/'Kurum'/'2025_1_Dosya'/'Folder'
    archive.mkdir(parents=True)
    existing=archive/'old.bin'
    payload=b'UYAP-CONTENT-HASH-RECONCILIATION'*200
    existing.write_bytes(payload)
    digest=sha(existing)
    con=agent.database(root)
    now='2026-09-20T00:00:00Z'
    con.execute("""INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,target_path,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ('old-session','old-doc','2025/1','Same','Folder',str(existing),len(payload),digest,'fp','UYAP_KURUM','Kurum',None,'ak','2026-01-01','x','VERIFIED',now))
    con.commit()
    doc={'caseId':'2025/1','sourceSystem':'UYAP_KURUM','institutionName':'Kurum'}
    candidate=agent._verified_content_hash_candidate(con,root,doc,digest,len(payload))
    req(candidate and candidate.resolve()==existing.resolve(), candidate)
    req(agent._verified_content_hash_candidate(con,root,doc,'0'*64,len(payload)) is None,'wrong hash matched')
    con.close()


# End-to-end: a freshly downloaded, validated document with the same SHA-256
# must get its own archive path without duplicating physical disk blocks when
# hard links are supported.
with tempfile.TemporaryDirectory() as td:
    root=Path(td)
    old_dir=root/'UYAP_Arsiv'/'Kurum'/'2025_1_Dosya'/'Folder'
    old_dir.mkdir(parents=True)
    payload=b'{\\rtf1\\ansi same legal bytes}'
    existing=old_dir/'2026-01-01_old.rtf'
    existing.write_bytes(payload)
    digest=sha(existing)
    con=agent.database(root)
    now='2026-09-20T00:00:00Z'
    con.execute("""INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,target_path,status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ('old-session','old-doc','2025/1','Old','Folder',str(existing),len(payload),digest,'fp-old','UYAP_KURUM','Kurum',None,'ak-old','2026-01-01','x','VERIFIED',now))
    con.commit(); con.close()
    staging=root/'UYAP_Arsiv'/'_incoming'/'sess'/'newdoc.part'
    staging.parent.mkdir(parents=True)
    staging.write_bytes(payload)
    target='UYAP_Arsiv/Kurum/2025_1_Dosya/Folder/2026-01-02_New_d-abc.rtf'
    msg={
      'action':'validate_download','attemptId':'a1','sessionId':'sess',
      'filename':str(staging),'stagingPath':'UYAP_Arsiv/_incoming/sess/newdoc.part',
      'targetPath':target,'extension':'.rtf','browserState':'complete','browserExists':True,
      'browserFileSize':len(payload),'browserTotalBytes':len(payload),
      'document':{'documentId':'new-doc','caseId':'2025/1','documentTitle':'New','documentDate':'2026-01-02',
                  'folderNames':['Folder'],'sourceSystem':'UYAP_KURUM','institutionName':'Kurum','attempts':1,
                  'targetPath':target,'sourceFingerprint':'fp-new','localArchiveMatchState':'ambiguous_hash_pending'}
    }
    result=agent.validate_download(msg)
    req(result.get('ok') and result.get('valid'), result)
    req(result.get('contentHashReconciled') is True, result)
    req(result.get('contentHashReuseMode') in {'hardlink','existing-target','downloaded-copy-fallback'}, result)
    final=Path(result['localPath'])
    req(final.is_file() and sha(final)==digest, result)
    req(not staging.exists(), 'staging file should be consumed')
    if result.get('contentHashReuseMode')=='hardlink':
        req(os.stat(existing).st_ino == os.stat(final).st_ino, 'hardlink expected')

# Static policy checks
js=(Path(__file__).resolve().parents[1]/'background'/'download-manager.js').read_text('utf-8')
const=(Path(__file__).resolve().parents[1]/'shared'/'constants.js').read_text('utf-8')
req('LOCAL_ARCHIVE_AMBIGUOUS_HASH_PENDING' in js,'pending event missing')
req('ambiguous-content-hash-pending' in js,'ambiguous gate must download')
req('contentHashReconciled' in js,'hash reconcile result missing')
req(any(f'RELIABILITY_SCHEMA_VERSION = {version}' in const for version in range(3, 10)),'migration schema not bumped')
req('DOWNLOAD_BLOCKED_LOCAL_AMBIGUOUS' not in js,'old blocking path still present')
print('RC15.9.10 TRANSIENT ERROR STATE CLEANUP REGRESSION: PASS')
