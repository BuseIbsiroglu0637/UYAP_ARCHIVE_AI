from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
dm=(ROOT/'background'/'download-manager.js').read_text(encoding='utf-8')
sw=(ROOT/'background'/'service-worker.js').read_text(encoding='utf-8')
content=(ROOT/'content'/'content.js').read_text(encoding='utf-8')
assert 'RC15.' in manifest['version_name']
for n in (16,32,48,128): assert (ROOT/'icons'/f'uyap-warning-{n}.png').exists()
assert 'setBadgeText({text: "!"})' in dm
assert 'setBadgeBackgroundColor?.({color: "#c62828"})' in dm
assert 'uyap-warning-16.png' in dm and 'uyap-warning-128.png' in dm
assert 'export async function syncSessionGuardUi()' in dm
assert 'active?.pauseReason === "LOGIN_REQUIRED"' in dm
assert 'syncSessionGuardUi' in sw
assert "addEventListener('pageshow'" in content
assert "addEventListener('focus'" in content
assert "visibilitychange" in content
assert 'UYAP_LOGIN_FORM_VISIBLE' in content
print('RC15.8.3 LOGOUT TOOLBAR ALERT REGRESSION: PASS')
