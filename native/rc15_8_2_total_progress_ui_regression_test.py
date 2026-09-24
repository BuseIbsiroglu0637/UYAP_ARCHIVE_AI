from pathlib import Path
import json, re
ROOT=Path(__file__).resolve().parents[1]
manifest=json.loads((ROOT/'manifest.json').read_text(encoding='utf-8'))
html=(ROOT/'popup/popup.html').read_text(encoding='utf-8')
js=(ROOT/'popup/popup.js').read_text(encoding='utf-8')
assert 'RC15.' in manifest['version_name']
assert 'YERELE İNDİRİLDİ VE DOĞRULANDI' in html
assert 'id="completionRatio"' in html
assert 'id="completedCount"' in html and 'id="total"' in html
assert 'Kalan tamamlanacak' in html and 'id="remainingCount"' in html
assert 'function downloadedCount(session){return Number(counts(session).verified||0);}' in js
assert 'function completedCount(session){return Number(counts(session).verified||0)+Number(counts(session).skipped||0);}' in js
assert 'el.remainingCount.textContent=Math.max(0,total-completed)' in js
# Ensure primary downloaded count is not completed aggregate.
assert 'el.verified.textContent=downloaded.toLocaleString' in js
print('RC15.8.2 TOTAL PROGRESS UI REGRESSION: PASS')
