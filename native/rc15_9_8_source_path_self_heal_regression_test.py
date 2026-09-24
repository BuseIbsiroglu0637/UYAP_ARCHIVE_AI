import importlib.util
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AGENT_PATH = HERE / 'uyap_archive_agent.py'
spec = importlib.util.spec_from_file_location('uyap_agent_rc1598', AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

def req(cond, msg):
    if not cond:
        raise AssertionError(msg)

ping = agent.dispatch({'action':'ping','probe':'rc1598'})
req(ping.get('version') == '2.7.64', ping)
req('source_path_self_heal_v1' in ping.get('capabilities', []), ping)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    session = 'session-test'
    docid = 'doc-test'
    staging_rel = f'UYAP_Arsiv/_incoming/{session}/{docid}.part'
    missing_source = root / 'UYAP_Arsiv' / '_incoming' / session / f'{docid}.part'
    # Deliberately do not create the source or its incoming/session directory.
    result = agent.dispatch({
        'action':'validate_download',
        'attemptId':'attempt-source-missing',
        'sessionId':session,
        'filename':str(missing_source),
        'stagingPath':staging_rel,
        'targetPath':f'UYAP_Arsiv/Test/2026_1/Evrak/{docid}.udf',
        'extension':'.udf',
        'browserState':'complete',
        'browserExists':True,
        'browserFileSize':3277,
        'browserTotalBytes':3277,
        'document':{
            'documentId':docid,
            'sourceSystem':'UYAP_KURUM',
            'caseId':'2026/1',
            'documentTitle':'Test',
            'bindingMethod':'native-uyap-exact-source-id',
            'correlationMode':'exact',
            'correlationCandidateCount':1,
            'attempts':1,
        }
    })
    req(result.get('ok') is True, result)
    req(result.get('valid') is False, result)
    req(result.get('errorCode') == 'SOURCE_FILE_MISSING', result)
    req(result.get('retryHint') == 'fresh_redownload', result)

js = (HERE.parent / 'background' / 'download-manager.js').read_text(encoding='utf-8')
req('SOURCE_PATH_RECOVERY_QUEUED' in js, 'missing compatibility recovery event')
req('nativePathMissingResponse' in js, 'missing compatibility bridge classifier')
policy = (HERE.parent / 'shared' / 'failure-policy.js').read_text(encoding='utf-8')
req("'SOURCE_FILE_MISSING'" in policy, 'SOURCE_FILE_MISSING not retryable')

print('RC15.9.10 SOURCE PATH SELF HEAL REGRESSION: PASS')
