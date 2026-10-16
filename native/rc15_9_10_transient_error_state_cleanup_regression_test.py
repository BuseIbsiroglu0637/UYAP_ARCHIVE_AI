from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]
DM=(ROOT/'background'/'download-manager.js').read_text('utf-8')
PERF=(ROOT/'shared'/'performance-policy.js').read_text('utf-8')
WS=(ROOT/'workspace'/'workspace.js').read_text('utf-8')
CONST=(ROOT/'shared'/'constants.js').read_text('utf-8')
MAN=(ROOT/'manifest.json').read_text('utf-8')

def req(cond,msg):
    if not cond: raise AssertionError(msg)

req('RELIABILITY_SCHEMA_VERSION = 5' in CONST,'schema v5 missing')
req('clearRecoveredPacingError' in PERF,'pacing recovery helper missing')
req('recentOutcomes = before.filter((outcome) => outcome.errorCode !== code)' in PERF,'old native outcomes are not removed')
req('backoffReason === "session"' in PERF and 'mode = healthySuccesses >= FAST_SUCCESS_STREAK ? "fast" : "normal"' in PERF,'session backoff recovery missing')
req('cleanupResolvedNativeTransientState' in DM,'transient cleanup helper missing')
req('TRANSIENT_NATIVE_STATE_CLEANED' in DM,'diagnostic cleanup event missing')
req('nativeHealthy: attemptOk && agent?.ok === true' in DM,'successful validation does not prove native recovery')
req('reason:"native-recovery-ping-success", forceDocuments:true' in DM,'ping recovery cleanup missing')
req('warnings: withoutRecoveredNativeWarnings(document.warnings)' in DM,'terminal duplicate warning cleanup missing')
req('problem-center-refresh' not in DM,'problem center must not claim native health')
req('NATIVE_AGENT_UNAVAILABLE' in WS and 'terminal&&!d.errorCode' in WS,'workspace ghost-warning filter missing')
req('transientNativeCleanupSchemaVersion: 1' in DM,'new-session cleanup schema marker missing')
req('2.9.0 RC15.9.' in MAN,'manifest version_name missing')
# Native service did not change in this UI/state-only hotfix.
AGENT=(ROOT/'native'/'uyap_archive_agent.py').read_text('utf-8')
req(tuple(map(int, AGENT.split('AGENT_VERSION = "', 1)[1].split('"', 1)[0].split("."))) >= (2, 7, 64),'agent should be 2.7.64')
print('RC15.9.10 TRANSIENT ERROR STATE CLEANUP REGRESSION: PASS')
