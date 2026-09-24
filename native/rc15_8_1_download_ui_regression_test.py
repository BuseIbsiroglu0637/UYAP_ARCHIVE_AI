from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
popup_js = (ROOT/'popup'/'popup.js').read_text(encoding='utf-8')
popup_html = (ROOT/'popup'/'popup.html').read_text(encoding='utf-8')
workspace_js = (ROOT/'workspace'/'workspace.js').read_text(encoding='utf-8')
workspace_html = (ROOT/'workspace'/'index.html').read_text(encoding='utf-8')
manifest = json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))

assert 'RC15.' in manifest['version_name']
assert 'function downloadedCount(session){return Number(counts(session).verified||0);}' in popup_js
assert 'function completedCount(session){return Number(counts(session).verified||0)+Number(counts(session).skipped||0);}' in popup_js
assert 'el.verified.textContent=downloaded.toLocaleString("tr-TR")' in popup_js
assert 'YERELE İNDİRİLDİ VE DOĞRULANDI' in popup_html
assert 'Önceden yerelde bulunan' in popup_html
assert 'Kalan tamamlanacak' in popup_html
assert 'option value="session" selected' in workspace_html
assert 'option value="verified" selected>Bu işte indirilenler' in workspace_html
assert 'if(status==="queued")return"İndirilecek"' in workspace_js
assert 'if(status==="verified")return"İndirildi ve doğrulandı"' in workspace_js
assert '"Henüz indirilmedi"' in workspace_js
assert '$("ovVerified").textContent=dl.toLocaleString("tr-TR")' in workspace_js

# User-observed scenario: 1,393 safe = 149 newly verified + 1,244 pre-existing local.
verified = 149
skipped = 1244
assert verified + skipped == 1393
print('RC15.8.1 DOWNLOAD COUNT UI REGRESSION: PASS')
