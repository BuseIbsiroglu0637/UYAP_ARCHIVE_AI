from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
sw = (ROOT / 'background/service-worker.js').read_text(encoding='utf-8')
dm = (ROOT / 'background/download-manager.js').read_text(encoding='utf-8')
na = (ROOT / 'background/native-agent.js').read_text(encoding='utf-8')
notify = (ROOT / 'background/session-notifications.js').read_text(encoding='utf-8')
policy = (ROOT / 'shared/failure-policy.js').read_text(encoding='utf-8')
capture = (ROOT / 'background/save-capture.js').read_text(encoding='utf-8')
agent = (ROOT / 'native/uyap_archive_agent.py').read_text(encoding='utf-8')

assert 'RC15.9' in manifest['version_name']
assert 'webRequest' in manifest['permissions']
assert 'chrome.webRequest?.onBeforeRequest?.addListener' in sw
assert 'chrome.webRequest?.onBeforeRedirect?.addListener' in sw
assert 'chrome.webRequest?.onCompleted?.addListener' in sw
assert 'observeUyapNetworkRequest' in dm
assert 'handleUyapNetworkRedirect' in dm
assert 'handleUyapNetworkCompleted' in dm
assert 'strongNetworkFallbackRequest' in dm
assert 'const transientNetworkHints = new Map();' in dm
assert 'raw URL/query' in dm or 'raw request URLs/query tokens' in dm
assert 'networkHint:{\n    method:hint.method' in dm
assert 'url:hint.url' not in dm.split('await setPendingDownload({...current, networkHint:{',1)[1].split('}, networkObserverSeenAt:now});',1)[0]
assert 'Math.min(250, strategy.nativePreferenceMs)' in dm
assert 'continueLocalWorkDuringLoginPause' in dm
assert 'LOGIN_PAUSE_LOCAL_WORK' in dm
assert 'maybeRefreshPreservedSourceAfterAuth' in dm
assert 'AUTH_TAB_RECOVERED' in dm
assert 'authTabId:' in notify
assert 'sourceTabIdAtLogin:' in notify
assert 'chrome.tabs.create({url:targetUrl, active:true})' in notify
assert 'lane.name === "fast"' in na
assert 'captureWindowMs: 550' in policy
assert 'Math.max(180, Number(captureWindowMs)' in capture
assert tuple(map(int, agent.split('AGENT_VERSION = "', 1)[1].split('"', 1)[0].split("."))) >= (2, 7, 64)
assert 'PRAGMA wal_autocheckpoint=4000;' in agent
assert 'with os.scandir(target.parent) as iterator:' in agent
assert 'sqlite_scandir_hotpath_v1' in agent
print('RC15.9 NETWORK OBSERVER + SESSION RECOVERY REGRESSION: PASS')
