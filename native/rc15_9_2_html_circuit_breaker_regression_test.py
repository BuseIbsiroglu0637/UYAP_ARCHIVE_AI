from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
dm = (ROOT / 'background/download-manager.js').read_text(encoding='utf-8')
popup = (ROOT / 'popup/popup.js').read_text(encoding='utf-8')
agent = (ROOT / 'native/uyap_archive_agent.py').read_text(encoding='utf-8')

assert manifest['version_name'].startswith('2.9.0 RC15.9.')
assert 'HTML_CIRCUIT_WINDOW_RATE = 0.80' in dm
assert 'HTML_CIRCUIT_STABLE_SUCCESSES = 2' in dm
assert 'function htmlCircuitState' in dm
assert 'htmlCircuitState: "open"' in dm
assert 'htmlCircuitState: "half_open"' in dm
assert '"HTML_CIRCUIT_OPEN"' in dm
assert '"HTML_CIRCUIT_HALF_OPEN"' in dm
assert '"HTML_CIRCUIT_CLOSED"' in dm
assert '"HTML_CIRCUIT_PROBE_FAILED"' in dm
assert 'circuitRecoverySuccesses >= HTML_CIRCUIT_STABLE_SUCCESSES' in dm
assert 'freshPrimaryAvailable: Boolean(freshPrimary)' in dm
assert 'AGENT_VERSION = "2.7.64"' in agent

handler = dm.split('export async function handleHtmlFailure',1)[1].split('export async function handleAlarm',1)[0]
assert 'CONTENT_SOFT_THROTTLE' not in handler
assert 'htmlCircuitShouldOpen' in handler
assert 'scheduleQueueWake(sessionId, cooldownUntil)' in handler

print('RC15.9.2 HTML CIRCUIT BREAKER REGRESSION: PASS')
