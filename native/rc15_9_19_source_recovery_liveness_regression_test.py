from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
dm = (ROOT / 'background/download-manager.js').read_text(encoding='utf-8')
sw = (ROOT / 'background/service-worker.js').read_text(encoding='utf-8')
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
agent = (ROOT / 'native/uyap_archive_agent.py').read_text(encoding='utf-8')


def req(cond, msg):
    if not cond:
        raise AssertionError(msg)

req('2.9.0 RC15.9.' in manifest.get('version_name',''), 'release identity')
req('AGENT_VERSION = "2.7.64"' in agent, 'native agent must be 2.7.64')

# Persistent alarm must survive a hot-path exception.
req('handler has completed successfully' in dm, 'persistent fallback comment missing')
req('await handleAlarm({name, scheduledTime: target});' in dm, 'hot-path alarm not awaited')
req('Math.abs(Number(current.scheduledTime || 0) - persistentWhen) < 250' in dm,
    'new retry alarm could be cleared accidentally')
req('persistent fallback kept' in dm, 'hot-path failure must retain persistent fallback')

# One-shot Chrome alarm errors must bootstrap durable session recovery.
req('async function handleOperationalAlarmSafely(alarm)' in sw, 'safe alarm wrapper missing')
req('await handleAlarm(alarm);' in sw, 'alarm wrapper does not await handler')
req('await scheduleRecovery();' in sw, 'alarm failure does not bootstrap recovery')
req('chrome.alarms.onAlarm.addListener((alarm) => void handleOperationalAlarmSafely(alarm));' in sw,
    'Chrome alarm listener bypasses safe wrapper')

# Expired orphan pending state is cleared without touching a live attempt.
req('async function clearExpiredOrphanPendingDownload' in dm, 'stale pending cleanup missing')
req('isLeaseFresh(pending)' in dm, 'fresh lease guard missing')
req('[STATUS.AWAITING_BROWSER, STATUS.DOWNLOADING].includes(document.status)' in dm,
    'active attempt guard missing')
req('STALE_PENDING_DOWNLOAD_CLEARED' in dm, 'cleanup diagnostic event missing')
req('reason:"source-recovery"' in dm, 'source recovery does not invoke pending cleanup')
req('reason:"runtime-recovery"' in dm, 'runtime bootstrap does not invoke pending cleanup')
req('reason:"queue-entry"' in dm, 'normal queue does not self-heal stale pending state')

# Existing source recovery state machine remains in place.
req('pauseReason: session.pauseReason || "SOURCE_PAGE_NOT_READY"' in dm, 'source wait state removed')
req('sessionGuardStatus: "WAITING_FOR_SOURCE"' in dm, 'source wait guard removed')
req('sourceWaitNextRetryAt:' in dm, 'source wait retry timestamp removed')

print('RC15.9.19 SOURCE RECOVERY LIVENESS REGRESSION: PASS')
