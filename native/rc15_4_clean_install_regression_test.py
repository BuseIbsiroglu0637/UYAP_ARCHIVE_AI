from __future__ import annotations
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / 'native' / 'install_host.py'
spec = importlib.util.spec_from_file_location('uyap_install_host_rc154', INSTALLER)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8-sig'))
actual_id = agent.extension_id(manifest)
assert actual_id == agent.EXTENSION_ID
roots = dict(agent.BROWSER_REGISTRY_ROOTS)
assert roots['Google Chrome'].endswith(r'Google\Chrome\NativeMessagingHosts')
assert roots['Microsoft Edge'].endswith(r'Microsoft\Edge\NativeMessagingHosts')

host = agent.host_manifest(Path(r'C:\\Temp\\uyap_archive_agent.exe'), actual_id)
origins = set(host['allowed_origins'])
assert f'chrome-extension://{actual_id}/' in origins
assert f'chrome-extension://{agent.STANDARD_EXTENSION_ID}/' in origins
assert host['name'] == agent.HOST_NAME

source = INSTALLER.read_text(encoding='utf-8')
assert 'native-install-diagnostic.json' in source
assert 'Microsoft Edge' in source
assert 'register_manifest_all_registry_views' in source

bridge = (ROOT / 'background' / 'native-agent.js').read_text(encoding='utf-8')
for code in ('NATIVE_HOST_NOT_FOUND','NATIVE_HOST_FORBIDDEN','NATIVE_HOST_EXITED','NATIVE_HOST_COMMUNICATION_ERROR'):
    assert code in bridge

popup = (ROOT / 'popup' / 'popup.js').read_text(encoding='utf-8')
assert 'Ajan kaydı bulunamadı' in popup
assert 'Ajan izni hatalı' in popup
print('RC15.4 clean-install/browser-host regression PASS')
