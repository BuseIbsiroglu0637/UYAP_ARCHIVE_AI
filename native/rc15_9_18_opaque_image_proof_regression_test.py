import hashlib
import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SPEC = importlib.util.spec_from_file_location('agent_rc15918', HERE / 'uyap_archive_agent.py')
agent = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent)


def req(condition, message):
    if not condition:
        raise AssertionError(message)


def insert_row(root, session, doc_id, path, payload, *, kind=None, mode=None, status='VERIFIED', fp='fp-1'):
    digest = hashlib.sha256(payload).hexdigest()
    conn = agent.database(root)
    conn.execute(
        """INSERT INTO documents(session_id,document_id,case_id,display_name,uyap_path,local_path,size,sha256,
           source_fingerprint,source_system,institution_name,source_document_id,archive_identity_key,document_date,
           target_path,validation_kind,validation_mode,status,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (session, doc_id, '2026/18', 'Document', 'Folder/Document', str(path), len(payload), digest,
         fp, 'UYAP_KURUM', 'Court', '18181818', 'identity-'+doc_id, '2026-09-20',
         path.relative_to(root).as_posix(), kind, mode, status, '2026-09-20T00:00:00Z')
    )
    conn.commit(); conn.close()
    return digest


def final_message(root, session, doc_id, path, fp='fp-1'):
    return {
        'downloadsRoot': str(root), 'sessionId': session, 'expected': 1,
        'sourceInventoryTotal': 1, 'documentIds': [doc_id], 'reviewRequired': 0,
        'allowPartial': False, 'partSizeBytes': 64 * 1024 * 1024, 'maxDocumentsPerPart': 500,
        'documents': [{
            'documentId': doc_id, 'caseId': '2026/18', 'sourceSystem': 'UYAP_KURUM',
            'institutionName': 'Court', 'sourceDocumentId': '18181818',
            'sourceDocumentIdOrigin': 'learned-download', 'sourceFingerprint': fp,
            'documentDate': '2026-09-20', 'folderNames': ['Folder'],
            'targetPath': path.relative_to(root).as_posix(),
        }],
    }


req(agent.AGENT_VERSION == '2.7.64', agent.AGENT_VERSION)

# 1) A byte-identical JPEG that was already accepted by the real Windows decoder
# with UYAP trailer provenance must keep that proof during finalization.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'image-proof-final'
    doc_id = 'image-doc'
    path = root / 'UYAP_Arsiv' / 'Court' / '2026_18_Dosya' / '2026_18 (Hukuk Dava Dosyası)' / 'Folder' / 'image.jpg'
    path.parent.mkdir(parents=True, exist_ok=True)
    # Deliberately not a decodable JPEG. The point is that finalization is allowed
    # to reuse ONLY an already-persisted byte-bound IMAGE_DOCUMENT proof.
    payload = b'\xff\xd8\xff\xe0UYAP-PROVEN-IMAGE-BYTES\xff\xd9TRAILER'
    path.write_bytes(payload)
    insert_row(root, session, doc_id, path, payload, kind='IMAGE_DOCUMENT', mode='jpeg_uyap_trailing_preserved_wic')
    result = agent.finalize_archive(final_message(root, session, doc_id, path))
    req(result.get('archiveStatus') == 'VERIFIED_COMPLETE', result)
    req(result.get('integrity', {}).get('invalidCount') == 0, result)
    req(result.get('integrity', {}).get('gatePassed') is True, result)

# Same proof MUST NOT survive byte mutation, even when file size is unchanged.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'image-proof-mutated'
    doc_id = 'image-doc-mutated'
    path = root / 'UYAP_Arsiv' / 'Court' / '2026_18_Dosya' / '2026_18 (Hukuk Dava Dosyası)' / 'Folder' / 'image.jpg'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = b'\xff\xd8\xff\xe0UYAP-PROVEN-IMAGE-BYTES\xff\xd9TRAILER'
    path.write_bytes(payload)
    insert_row(root, session, doc_id, path, payload, kind='IMAGE_DOCUMENT', mode='jpeg_uyap_trailing_preserved_wic')
    mutated = bytearray(payload); mutated[8] ^= 0x01; path.write_bytes(mutated)
    result = agent.finalize_archive(final_message(root, session, doc_id, path))
    req(result.get('archiveStatus') != 'VERIFIED_COMPLETE', result)
    req(result.get('integrity', {}).get('invalidCount') == 1, result)

# 2) Exact UYAP browser/native provenance may preserve an unknown BINARY payload,
# but only after complete-state + exact browser byte-size proof and binary probe.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'opaque-direct'
    attempt = 'opaque-attempt'
    doc_id = 'opaque-doc'
    source = root / 'UYAP_Arsiv' / '_incoming' / session / f'{attempt}.part'
    source.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(range(256)) * 128
    source.write_bytes(payload)
    target = 'UYAP_Arsiv/Court/2026_18_Dosya/2026_18 (Hukuk Dava Dosyası)/Folder/Opaque_18181818.bin'
    msg = {
        'filename': str(source), 'stagingPath': f'UYAP_Arsiv/_incoming/{session}/{attempt}.part',
        'targetPath': target, 'sessionId': session, 'attemptId': attempt,
        'browserDownloadId': 1818, 'browserState': 'complete', 'browserExists': True,
        'browserFileSize': len(payload), 'browserTotalBytes': len(payload),
        'directDownloadEvidence': True,
        'document': {
            'sessionId': session, 'attemptId': attempt, 'downloadId': 1818,
            'documentId': doc_id, 'caseId': '2026/18', 'displayName': 'Opaque',
            'sourceSystem': 'UYAP_KURUM', 'institutionName': 'Court',
            'sourceDocumentId': '18181818', 'bindingMethod': 'extension-download-id',
            'correlationMode': 'exact', 'correlationCandidateCount': 1,
            'sourceFingerprint': 'fp-opaque', 'targetPath': target,
        },
    }
    result = agent.validate_download(msg)
    req(result.get('valid') is True, result)
    req(result.get('contentKind') == 'UYAP_OPAQUE_BINARY', result)
    req(result.get('opaqueBinaryValidated') is True, result)
    req(result.get('opaqueValidationMode') == 'uyap_exact_opaque_binary_v1', result)
    archived = Path(result['localPath'])
    req(archived.read_bytes() == payload, 'opaque bytes changed')

# Unknown text must NEVER use opaque fallback even with exact UYAP provenance.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'opaque-text-reject'
    attempt = 'opaque-text-attempt'
    source = root / 'UYAP_Arsiv' / '_incoming' / session / f'{attempt}.part'
    source.parent.mkdir(parents=True, exist_ok=True)
    payload = (b'UNKNOWN TEXT RESPONSE ' * 200)
    source.write_bytes(payload)
    target = 'UYAP_Arsiv/Court/2026_18_Dosya/2026_18 (Hukuk Dava Dosyası)/Folder/Text_18181819.bin'
    msg = {
        'filename': str(source), 'stagingPath': f'UYAP_Arsiv/_incoming/{session}/{attempt}.part',
        'targetPath': target, 'sessionId': session, 'attemptId': attempt,
        'browserDownloadId': 1819, 'browserState': 'complete', 'browserExists': True,
        'browserFileSize': len(payload), 'browserTotalBytes': len(payload), 'directDownloadEvidence': True,
        'document': {'sessionId': session, 'attemptId': attempt, 'downloadId': 1819, 'documentId': 'text-doc',
                     'caseId': '2026/18', 'displayName': 'Text', 'sourceSystem': 'UYAP_KURUM',
                     'institutionName': 'Court', 'sourceDocumentId': '18181819', 'bindingMethod': 'extension-download-id',
                     'correlationMode': 'exact', 'correlationCandidateCount': 1, 'targetPath': target},
    }
    result = agent.validate_download(msg)
    req(result.get('valid') is False, result)
    req(result.get('errorCode') == 'UNSUPPORTED_FORMAT', result)

# 3) Existing RC15.9.17 quarantine entries can be upgraded locally when their
# native UYAP correlation + size + previous invalid SHA-256 prove exact bytes.
with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'opaque-quarantine'
    session_ref = hashlib.sha256(session.encode()).hexdigest()[:24]
    quarantine = root / 'UYAP_Arsiv' / 'Hatalı Dosyalar' / 'OTHER' / session_ref / 'old_UNSUPPORTED_FORMAT.invalid'
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    payload = bytes(range(255, -1, -1)) * 96
    quarantine.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    target = 'UYAP_Arsiv/Court/2026_18_Dosya/2026_18 (Hukuk Dava Dosyası)/Folder/Opaque_18181820.bin'
    result = agent.revalidate_quarantined({
        'downloadsRoot': str(root), 'quarantinePath': str(quarantine), 'sessionId': session,
        'targetPath': target,
        'document': {
            'sessionId': session, 'documentId': 'old-opaque', 'caseId': '2026/18', 'displayName': 'Opaque',
            'sourceSystem': 'UYAP_KURUM', 'institutionName': 'Court', 'sourceDocumentId': '18181820',
            'bindingMethod': 'native-uyap-discovered-source-id', 'correlationMode': 'exact',
            'correlationCandidateCount': 1, 'browserFileSize': len(payload), 'browserTotalBytes': len(payload),
            'actualSize': len(payload), 'observedInvalidSize': len(payload), 'invalidSha256': digest,
            'sourceFingerprint': 'fp-old-opaque', 'targetPath': target,
        }
    })
    req(result.get('valid') is True, result)
    req(result.get('contentKind') == 'UYAP_OPAQUE_BINARY', result)
    req(result.get('opaqueValidationMode') == 'uyap_exact_opaque_binary_v1', result)
    req(Path(result['localPath']).read_bytes() == payload, 'quarantine recovery changed bytes')

# 4) Browser-side revalidation must persist opaque proof metadata for diagnostics.
dm = (ROOT / 'background' / 'download-manager.js').read_text('utf-8')
req('opaqueValidationMode: agent.opaqueValidationMode || null' in dm, 'opaque proof not persisted in extension state')
agent_src = (HERE / 'uyap_archive_agent.py').read_text('utf-8')
req("result.get('opaqueValidationMode')" in agent_src, 'opaque journal replay does not persist proof mode')

print('RC15.9.18 OPAQUE + IMAGE FINAL PROOF REGRESSION: PASS')
