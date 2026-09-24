from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
DM=(ROOT/'background'/'download-manager.js').read_text(encoding='utf-8')
SC=(ROOT/'background'/'save-capture.js').read_text(encoding='utf-8')
MAN=(ROOT/'manifest.json').read_text(encoding='utf-8')
assert 'function declaredUyapDocumentKind' in DM
assert 'view_document_brd\\.uyap' in DM
assert 'uyap_html_document' in DM
assert 'declaredDocumentKind:hint.declaredDocumentKind || null' in DM
assert 'expectedDocumentKind: fallbackExpectedDocumentKind' in DM
assert 'xml|html?' in SC
assert '2.9.0 RC15.9.' in MAN
print('RC15.9.6 NETWORK HTML EVIDENCE REGRESSION: PASS')
