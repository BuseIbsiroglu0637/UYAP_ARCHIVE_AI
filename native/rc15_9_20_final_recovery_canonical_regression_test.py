from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
dm = (ROOT / 'background/download-manager.js').read_text(encoding='utf-8')
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
agent = (ROOT / 'native/uyap_archive_agent.py').read_text(encoding='utf-8')


def req(cond, msg):
    if not cond:
        raise AssertionError(msg)

req('2.9.0 RC15.9.' in manifest.get('version_name',''), 'release identity')
req('AGENT_VERSION = "2.7.64"' in agent, 'native agent must be 2.7.64')

# The bypass must be narrowly scoped to final-integrity recovery of a document
# that had only been satisfied by local archive reconciliation.
req("mode === 'final_integrity'" in dm, 'final-integrity scope missing')
req('document.status === STATUS.SKIPPED' in dm, 'skipped-state proof missing')
req('document.skipReason === "UNCHANGED_LOCAL_ARCHIVE"' in dm, 'local archive proof missing')
req('document.localArchiveMatchState === "found"' in dm, 'local match state proof missing')
req('forceCanonicalRecovery' in dm, 'canonical recovery flag missing')
req('FINAL_CANONICAL_RECOVERY_QUEUED' in dm, 'canonical recovery diagnostic missing')

# The local duplicate gate must bypass only tagged candidates, while the normal
# duplicate diagnostic remains present for every other download.
bypass = dm.index('if (document.forceCanonicalRecovery === true)')
native_sync = dm.index('action: "sync_inventory"', bypass)
req(bypass < native_sync, 'canonical bypass must happen before local native sync')
req('FINAL_RECOVERY_LOCAL_GATE_BYPASSED' in dm, 'bypass diagnostic missing')
req('DOWNLOAD_BLOCKED_LOCAL_DUPLICATE' in dm, 'normal duplicate guard was removed')

# Successful materialization must revoke the exceptional state.
req('forceCanonicalRecovery: false' in dm, 'canonical flag is not cleared after success')
req('canonicalRecoveryCompletedAt:' in dm, 'canonical completion evidence missing')

# Existing source-recovery liveness behavior must remain in this package.
req('STALE_PENDING_DOWNLOAD_CLEARED' in dm, 'RC15.9.19 stale pending cleanup regressed')
req('persistent fallback kept' in dm, 'RC15.9.19 persistent recovery alarm regressed')


# Invalid document ids must be counted once; older code accidentally appended
# the same row twice before the integrity summary.
req('invalid_ids.append(row[0])\n                invalid_ids.append(row[0])' not in agent, 'duplicate invalid-id append regressed')

print('RC15.9.21 FINAL RECOVERY CANONICAL REGRESSION: PASS')
