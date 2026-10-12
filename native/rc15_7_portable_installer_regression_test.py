from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def require(condition, message):
    if not condition:
        raise AssertionError(message)

manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
require('RC15.' in manifest.get('version_name',''), 'manifest version_name RC15 değil')

contract = json.loads((ROOT / 'config' / 'release-contract.json').read_text(encoding='utf-8'))
agent_version = contract['nativeAgent']['version']
agent = (ROOT / 'native' / 'uyap_archive_agent.py').read_text(encoding='utf-8')
require(f'AGENT_VERSION = "{agent_version}"' in agent, f'Agent {agent_version} değil')

installer = (ROOT / 'native' / 'portable_installer.ps1').read_text(encoding='utf-8')
for token in [
    'python-3.13.15-embed-amd64.zip',
    'python-3.13.15-embed-arm64.zip',
    'python-3.13.15-embed-win32.zip',
    'Get-FileHash -Algorithm SHA256',
    'NativeMessagingHosts',
    'Microsoft\\Edge',
    'Google\\Chrome',
    'Invoke-PortableSelfTest',
    'native-install-diagnostic.json',
    'native-install.log',
    'Restore-RegistryState',
    'rollback',
    'ai_local_index.py',
    'ai_provider.py',
]:
    require(token.lower() in installer.lower(), f'portable installer token eksik: {token}')

# End-user installer path must not require system Python, pip, or PyInstaller.
setup = (ROOT / 'SETUP_UYAP_ARCHIVE_AI.bat').read_text(encoding='utf-8')
require('portable_installer.ps1' in setup, 'SETUP portable installer çağırmıyor')
require(' -m pyinstaller' not in setup.lower() and 'pip install' not in setup.lower(), 'SETUP build aracı çağırıyor')
commands='\n'.join(line for line in setup.lower().splitlines() if not line.lstrip().startswith('echo '))
require('python.exe' not in commands and ' python3 ' not in commands, 'SETUP sistem Python çağırıyor')

bootstrap = (ROOT / 'native' / 'portable_sitecustomize.py').read_text(encoding='utf-8')
require('chrome-extension://' in bootstrap, 'portable bootstrap extension origin doğrulamıyor')
require('from uyap_archive_agent import main' in bootstrap, 'portable bootstrap agent main çağırmıyor')

protocol = (ROOT / 'native' / 'portable_protocol_self_test.py').read_text(encoding='utf-8')
require('pingCount' in protocol and "'ping'" in protocol or '"ping"' in protocol, '2/2 ping self-test eksik')

builder = (ROOT / 'native' / 'build_offline_setup.ps1').read_text(encoding='utf-8')
require('iexpress.exe' in builder.lower(), 'offline Setup.exe builder IExpress kullanmıyor')
require('runtime-cache' in builder.lower(), 'offline builder runtime cache eklemiyor')

print('RC15.8 portable installer regression: PASS')
