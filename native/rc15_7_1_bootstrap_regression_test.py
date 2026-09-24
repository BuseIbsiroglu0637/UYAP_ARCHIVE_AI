from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
bootstrap_path = ROOT / 'native' / 'portable_sitecustomize.py'
spec = importlib.util.spec_from_file_location('uyap_portable_sitecustomize_test', bootstrap_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

origin = 'chrome-extension://fppnhbkemmbocmicplkfidcbggeininh/'
# This is the RC15.7 failing shape: the self-test process carries the origin as
# a later argument. It must NOT be hijacked as the Native Messaging host.
assert mod._is_native_messaging_launch(['portable_protocol_self_test.py', r'C:\Agent\runtime\python.exe', origin]) is False
# Real Chromium native launch puts the extension origin in the script slot.
assert mod._is_native_messaging_launch([origin, '--parent-window=0']) is True
assert mod._native_origin_from_argv([origin, '--parent-window=0']) == origin
assert mod._is_native_messaging_launch(['-c', origin]) is False

installer = (ROOT / 'native' / 'portable_installer.ps1').read_text(encoding='utf-8-sig')
assert '& $PythonExe -S $ProtocolTest $PythonExe $origin' in installer
print('RC15.8 bootstrap regression: PASS')
