from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
dm = (ROOT / 'background/download-manager.js').read_text(encoding='utf-8')
popup = (ROOT / 'popup/popup.js').read_text(encoding='utf-8')
agent = (ROOT / 'native/uyap_archive_agent.py').read_text(encoding='utf-8')

assert manifest['version_name'].startswith('2.9.0 RC15.9.')
assert 'HTML_CIRCUIT_CONSECUTIVE_THRESHOLD = 6' in dm
assert 'HTML_CIRCUIT_WINDOW_MIN = 10' in dm
assert 'HTML_CIRCUIT_WINDOW_FAILURES = 8' in dm
assert 'HTML_CIRCUIT_WINDOW_RATE = 0.80' in dm
assert 'HTML_CIRCUIT_HALF_OPEN_CONSECUTIVE_THRESHOLD = 2' in dm
assert '[15_000, 30_000, 60_000]' in dm
assert 'sessionGuardStatus: "CONTENT_COOLDOWN"' in dm
assert 'status: SESSION_STATUS.RUNNING' in dm
assert 'pauseReason: null' in dm
assert 'queueFrozen: false' in dm
assert 'await scheduleQueueWake(sessionId, cooldownUntil)' in dm
assert 'localArchivePrefetchTasks = new Map()' in dm
assert 'localArchivePrefetchTasks.has(key)' in dm
assert 'HTML yanıtları arttı, akış korunuyor' in popup
assert 'sessionGuardStatus==="CONTENT_COOLDOWN"' in popup
assert 'AGENT_VERSION = "2.7.64"' in agent

handler = dm.split('export async function handleHtmlFailure',1)[1].split('export async function handleAlarm',1)[0]
assert 'status: SESSION_STATUS.RECOVERING' not in handler
assert 'scheduleOperationalAlarm(recoveryAlarmName(sessionId)' not in handler
assert 'HTML_RESPONSE' in handler
assert 'htmlCircuitShouldOpen' in handler

print('RC15.9.3 ADAPTIVE HTML BYPASS REGRESSION: PASS')
