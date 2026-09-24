import hashlib
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = importlib.util.spec_from_file_location('agent_rc15922', HERE / 'uyap_archive_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def req(condition, message):
    if not condition:
        raise AssertionError(message)


req(agent.AGENT_VERSION == '2.7.64', agent.AGENT_VERSION)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    archive = root / 'UYAP_Arsiv'
    folder = archive / 'Kastamonu İcra Dairesi' / '2024_14998_Dosya' / '2024_14998 (İcra Dosyası)' / 'Evrak'
    folder.mkdir(parents=True)
    path = folder / 'ornek.pdf'
    payload = b'%PDF-1.4\nphysical-proof\n%%EOF\n'
    path.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    target = path.relative_to(root).as_posix()

    conn = agent.database(root)
    conn.execute(
        """INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
           source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,
           target_path,validation_kind,validation_mode,status,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ('old-session','doc-1','2024/14998','Örnek Evrak','Evrak',str(path),len(payload),digest,
         'fp-1','UYAP_KURUM','Kastamonu İcra Dairesi','src-1','identity-1','2026-09-20',target,
         'PDF_DOCUMENT','pdf_eof','VERIFIED','2026-09-20T15:00:00Z')
    )
    conn.commit(); conn.close()

    doc = {
        'sourceSystem':'UYAP_KURUM', 'institutionName':'Kastamonu İcra Dairesi',
        'courtName':'Kastamonu İcra Dairesi', 'documentId':'doc-1', 'sourceDocumentId':'src-1',
        'caseId':'2024/14998', 'documentTitle':'Örnek Evrak', 'documentDate':'2026-09-20',
        'folderNames':['Evrak'], 'sourceFingerprint':'fp-1', 'targetPath':target,
    }

    # Present file: sync_inventory must emit explicit current SHA-256 physical proof.
    sync = agent.sync_inventory({'sessionId':'new-session','downloadsRoot':str(root),'documents':[doc]})
    req(sync.get('unchanged') == ['doc-1'], sync)
    proof = (sync.get('physicalProofs') or {}).get('doc-1') or {}
    req(proof.get('level') == 'sha256' and proof.get('pathExists') is True, proof)
    req(proof.get('sha256') == digest and int(proof.get('size') or 0) == len(payload), proof)

    registry = agent.list_archive_registry({'downloadsRoot':str(root),'limit':20})
    req(registry.get('schemaVersion') == 4, registry)
    case = registry['caseIndex'][0]
    req(case.get('physicalReadyCount') == 1, case)
    req(case.get('missingCount') == 0 and case.get('staleMetadataCount') == 0, case)

    # Delete the real bytes but leave VERIFIED metadata behind. Metadata must NOT
    # suppress redownloads and the dashboard must expose the stale DB state.
    path.unlink()
    registry_missing = agent.list_archive_registry({'downloadsRoot':str(root),'limit':20})
    case_missing = registry_missing['caseIndex'][0]
    req(case_missing.get('metadataVerifiedCount') == 1, case_missing)
    req(case_missing.get('physicalReadyCount') == 0, case_missing)
    req(case_missing.get('missingCount') == 1, case_missing)
    req(case_missing.get('staleMetadataCount') == 1, case_missing)
    req(registry_missing.get('staleMetadataFiles') == 1, registry_missing)
    req(registry_missing['documents'][0].get('status') == 'missing', registry_missing['documents'][0])

    sync_missing = agent.sync_inventory({'sessionId':'another-session','downloadsRoot':str(root),'documents':[doc]})
    req(sync_missing.get('unchanged') == [], sync_missing)
    req((sync_missing.get('physicalProofs') or {}) == {}, sync_missing)
    req(int(sync_missing.get('notFound') or 0) == 1, sync_missing)

    # Replacing the file with same length but different bytes is also rejected by
    # the SHA-256 ALREADY_ARCHIVED proof even though the lightweight dashboard
    # existence/size layer can see a physical file again.
    replacement = bytearray(payload)
    replacement[-2] = (replacement[-2] + 1) % 255
    path.write_bytes(bytes(replacement))
    sync_changed = agent.sync_inventory({'sessionId':'changed-session','downloadsRoot':str(root),'documents':[doc]})
    req(sync_changed.get('unchanged') == [], sync_changed)
    req((sync_changed.get('physicalProofs') or {}) == {}, sync_changed)

# Browser-side contract: only explicit sha256 physical proof can create SKIPPED
# and ALREADY_ARCHIVED; stale old SKIPPED state is converted back to QUEUED.
dm = (ROOT / 'background' / 'download-manager.js').read_text('utf-8')
req('localArchivePhysicalProofLevel: "sha256"' in dm, 'document physical proof persistence missing')
req("document.localArchivePhysicalProofLevel === \"sha256\"" in dm, 'ALREADY_ARCHIVED sha256 gate missing')
req('ALREADY_ARCHIVED_PHYSICAL_PROOF_REJECTED' in dm, 'metadata-only rejection diagnostic missing')
req('status: (document.status === STATUS.SKIPPED && document.skipReason === "UNCHANGED_LOCAL_ARCHIVE") ? STATUS.QUEUED' in dm, 'stale skipped document is not requeued')
req('includeTerminal: true, force: true' in dm, 'old-session forced physical reproof missing')

popup = (ROOT / 'popup' / 'popup.js').read_text('utf-8')
workspace = (ROOT / 'workspace' / 'workspace.js').read_text('utf-8')
req('physicalReadyCount' in popup and 'staleMetadataFiles' in popup, 'popup physical state missing')
req('physicalReadyCount' in workspace and 'staleMetadataFiles' in workspace, 'workspace physical state missing')

print('RC15.9.23 PORTABLE RESUME REGRESSION: PASS')
