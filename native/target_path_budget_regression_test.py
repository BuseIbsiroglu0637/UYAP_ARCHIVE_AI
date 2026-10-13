"""Archive target paths must stay inside the Windows MAX_PATH budget.

Field evidence (2026-10-13): on an archive root under
"OneDrive\\Masaustu\\UYAP GELISTIRME\\DOSYALAR", 22 documents had absolute
targets of 266-276 characters. os.replace() raised FileNotFoundError, the
dispatch-level net reported SOURCE_FILE_MISSING, and each completed, valid
download was fetched from UYAP again and again (348 uncommitted journal rows;
all 185 committed rows were <= 259 characters).
"""
import importlib.util
import re
import sqlite3
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PATH = HERE / 'uyap_archive_agent.py'
spec = importlib.util.spec_from_file_location('uyap_agent_target_budget', AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


def req(cond, msg):
    if not cond:
        raise AssertionError(msg)


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


COURT = 'Kastamonu İcra Ceza Mahkemesi'
CASE = '2026_106_Esas'
SOURCE_ID = '9988776655'
TITLE = '2026-10-13_' + ('Bilirkişi Raporu ve Ek Belgeler ' * 3).strip()


def padded_folder(root: Path, wanted_dir_len: int) -> str:
    """Folder name that makes root/UYAP_Arsiv/court/case/<pad> reach a length."""
    base = root / 'UYAP_Arsiv' / COURT / CASE
    pad = max(8, wanted_dir_len - agent._win_path_len(base) - 1)
    return ('Klasör_' * 40)[:pad].rstrip('_ ') or 'Klasor'


def message_for(root: Path, session: str, docid: str, rel_target: str, staging: Path, staging_rel: str, attempt: str):
    size = staging.stat().st_size
    return {
        'action': 'validate_download',
        'attemptId': attempt,
        'sessionId': session,
        'filename': str(staging),
        'stagingPath': staging_rel,
        'targetPath': rel_target,
        'extension': '.pdf',
        'browserState': 'complete',
        'browserExists': True,
        'browserFileSize': size,
        'browserTotalBytes': size,
        'document': {
            'documentId': docid,
            'sourceSystem': 'UYAP',
            'caseId': '2026/106',
            'documentTitle': TITLE,
            'sourceDocumentId': SOURCE_ID,
            'targetPath': rel_target,
            'bindingMethod': 'native-uyap-exact-source-id',
            'correlationMode': 'exact',
            'correlationCandidateCount': 1,
            'attempts': 1,
        },
    }


def stage_pdf(root: Path, session: str, docid: str):
    staging_rel = f'UYAP_Arsiv/_incoming/{session}/{docid}.part'
    staging = root / staging_rel
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.write_bytes(minimal_pdf())
    return staging, staging_rel


def uncommitted_journal_rows(root: Path) -> int:
    connection = sqlite3.connect(root / 'UYAP_Arsiv' / '_archive.db')
    try:
        return connection.execute('SELECT COUNT(*) FROM transfer_journal WHERE committed=0').fetchone()[0]
    finally:
        connection.close()


ping = agent.dispatch({'action': 'ping', 'probe': 'target-budget'})
req('target_path_budget_v1' in ping.get('capabilities', []), 'agent must advertise target_path_budget_v1')

# --- Unit: a target that already fits is never renamed -----------------------
base = Path(tempfile.gettempdir()).resolve()
short = base / 'UYAP_Arsiv' / COURT / CASE / f'Kısa_Başlık_{SOURCE_ID}.pdf'
fitted, info = agent._fit_target_path_budget(short)
req(fitted == short and info is None, 'fitting target must stay byte-for-byte unchanged')

# --- Unit: over budget -> only the title shrinks; id + extension survive -----
folder = base / 'UYAP_Arsiv' / ('K' * 60) / ('E' * 60)
long_target = folder / f'{TITLE} {TITLE}_{SOURCE_ID}.pdf'
req(agent._win_path_len(long_target) > agent.TARGET_PATH_MAX_CHARS, 'fixture must be over budget')
fitted, info = agent._fit_target_path_budget(long_target)
req(agent._win_path_len(fitted) <= agent.TARGET_PATH_MAX_CHARS, fitted)
req(fitted.parent == long_target.parent, 'folders must not change')
req(fitted.name.endswith(f'_{SOURCE_ID}.pdf'), fitted.name)
req(not re.search(r'[ ._-]_' + SOURCE_ID, fitted.name), f'no dangling separator before id: {fitted.name}')
req(info and info.get('targetPathShortened') is True, info)
again, _ = agent._fit_target_path_budget(long_target)
req(again == fitted, 'shortening must be deterministic so a retry reuses the same name')
versioned = agent._versioned_target(fitted)
req(agent._win_path_len(versioned) <= agent.TARGET_PATH_MAX_CHARS, versioned)
req(re.search(rf'_{SOURCE_ID}__v[0-9a-f]{{32}}\.pdf$', versioned.name), versioned.name)

# --- Unit: a directory beyond the CreateDirectory limit is a clear error ------
deep = base / ('D' * 120) / ('D' * 120) / f'x_{SOURCE_ID}.pdf'
try:
    agent._fit_target_path_budget(deep)
    raise AssertionError('over-long directory must raise TargetPathTooLong')
except agent.TargetPathTooLong as error:
    req(error.diagnostics.get('targetPathOverflowPart') == 'directory', error.diagnostics)


# --- Integration: the production failure, end to end -------------------------
# Directory ~200 chars (creatable on any Windows PC), full target > 259 chars.
with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    session, docid = 'session-budget', 'doc-budget'
    pad = padded_folder(root, 200)
    rel_target = f'UYAP_Arsiv/{COURT}/{CASE}/{pad}/{TITLE}_{SOURCE_ID}.pdf'
    req(agent._win_path_len(root / rel_target) > agent.TARGET_PATH_MAX_CHARS, 'fixture must reproduce the >259 target')
    staging, staging_rel = stage_pdf(root, session, docid)
    result = agent.dispatch(message_for(root, session, docid, rel_target, staging, staging_rel, 'attempt-budget-1'))
    req(result.get('ok') is True and result.get('valid') is True, result)
    req(result.get('errorCode') != 'SOURCE_FILE_MISSING', result)
    local = Path(result['localPath'])
    req(local.is_file(), f'archived file must exist: {result}')
    req(agent._win_path_len(local) <= agent.TARGET_PATH_MAX_CHARS, result)
    req(local.name.endswith(f'_{SOURCE_ID}.pdf'), local.name)
    req(result.get('targetPathShortened') is True, result)
    req(any('kısaltıldı' in note for note in result.get('validationNotes') or []), result)
    req((root / result['targetPath']).resolve() == local.resolve(), 'relative targetPath must name the real file')
    req(not staging.exists(), 'staging file must be moved into the archive')
    req(uncommitted_journal_rows(root) == 0, 'no uncommitted journal rows may remain')
    connection = sqlite3.connect(root / 'UYAP_Arsiv' / '_archive.db')
    try:
        row = connection.execute(
            "SELECT target_path, local_path, status FROM documents WHERE document_id=?", (docid,)
        ).fetchone()
    finally:
        connection.close()
    req(row is not None and row[2] == 'VERIFIED', row)
    # Reconciliation keys on the logical (unshortened) target_path; the bytes
    # live at the shortened local_path.
    req(row[0] == rel_target, row)
    req(Path(row[1]).resolve() == local.resolve(), row)


# --- Integration: an unfittable directory fails clearly, without retry ------
with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    session, docid = 'session-deep', 'doc-deep'
    rel_target = f'UYAP_Arsiv/{COURT}/{CASE}/{"A" * 130}/{"B" * 130}/{TITLE}_{SOURCE_ID}.pdf'
    staging, staging_rel = stage_pdf(root, session, docid)
    result = agent.dispatch(message_for(root, session, docid, rel_target, staging, staging_rel, 'attempt-deep-1'))
    req(result.get('valid') is False and result.get('errorCode') == 'TARGET_PATH_TOO_LONG', result)
    req(result.get('retryHint') is None, 'a path problem must never request a fresh UYAP re-download')
    req(result.get('targetPathOverflowPart') == 'directory', result)
    req(staging.is_file(), 'valid downloaded bytes must be preserved for a later re-validation')

# --- Integration: other target-side write failures are not SOURCE_FILE_MISSING
with tempfile.TemporaryDirectory() as td:
    root = Path(td).resolve()
    session, docid = 'session-unavailable', 'doc-unavailable'
    rel_target = f'UYAP_Arsiv/{COURT}/{CASE}/Ara Kararlar/Kısa_{SOURCE_ID}.pdf'
    staging, staging_rel = stage_pdf(root, session, docid)
    real_replace = agent.os.replace

    def failing_replace(src, dst, *args, **kwargs):
        if '_incoming' not in str(dst) and str(dst).endswith('.pdf'):
            raise FileNotFoundError(3, 'The system cannot find the path specified', str(src), None, str(dst))
        return real_replace(src, dst, *args, **kwargs)

    agent.os.replace = failing_replace
    try:
        result = agent.dispatch(message_for(root, session, docid, rel_target, staging, staging_rel, 'attempt-unavailable-1'))
    finally:
        agent.os.replace = real_replace
    req(result.get('valid') is False and result.get('errorCode') == 'TARGET_PATH_UNAVAILABLE', result)
    req(result.get('retryHint') is None, result)
    req(result.get('sourcePreserved') is True and staging.is_file(), result)
    req(uncommitted_journal_rows(root) == 0, 'failed move must not leave an uncommitted journal row')


# --- The dispatch-level net now carries privacy-light evidence ---------------
real_validate = agent.validate_download

MISSING_PATH = 'C:\\x' * 10


def unclassified_missing(message):
    raise FileNotFoundError(2, 'No such file or directory', MISSING_PATH)

agent.validate_download = unclassified_missing
try:
    fallback = agent.dispatch({'action': 'validate_download'})
finally:
    agent.validate_download = real_validate
req(fallback.get('errorCode') == 'SOURCE_FILE_MISSING', fallback)
req(fallback.get('fileErrorStage') == 'unclassified', fallback)
req(fallback.get('fileErrorPathLength') == len(MISSING_PATH), fallback)
req(all(MISSING_PATH not in str(value) for value in fallback.values()), 'raw paths must never be exported')

# --- Extension + installer contracts -----------------------------------------
policy = (HERE.parent / 'shared' / 'failure-policy.js').read_text(encoding='utf-8')
req("'TARGET_PATH_TOO_LONG'" in policy and "'TARGET_PATH_UNAVAILABLE'" in policy, 'local target errors must be terminal')
req('export function isLocalTargetError' in policy, 'isLocalTargetError export missing')
manager = (HERE.parent / 'background' / 'download-manager.js').read_text(encoding='utf-8')
req('isolatedContentFailure || localTargetFailure ||' in manager, 'handleFailure must stop re-downloading on local target errors')
req('targetPathLength: Number.isFinite(document.targetPathLength)' in manager, 'diagnostics must export target path length')
installer = (HERE / 'install_host.py').read_text(encoding='utf-8')
req("'target_path_budget_v1'" in installer, 'installer must replace agents without the path budget')

print('TARGET PATH BUDGET REGRESSION: PASS')
