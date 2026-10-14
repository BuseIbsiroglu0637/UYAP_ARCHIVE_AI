from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / 'native' / 'install_host.py'
spec = importlib.util.spec_from_file_location('uyap_install_host_rc155', INSTALLER)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)

manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8-sig'))
actual_id = agent.extension_id(manifest)
assert actual_id == agent.EXTENSION_ID

with tempfile.TemporaryDirectory(prefix='uyap-rc155-lifecycle-') as td:
    base = Path(td) / 'UYAPArsiv'
    layout = agent._agent_layout(base)
    (layout['current']).mkdir(parents=True)
    (layout['rollback']).mkdir(parents=True)
    (layout['current'] / 'uyap_archive_agent.exe').write_bytes(b'current')
    (layout['rollback'] / 'uyap_archive_agent.exe').write_bytes(b'rollback')
    (base / 'versions' / '2.7.33-old').mkdir(parents=True)
    (base / 'versions' / '2.7.34-old').mkdir(parents=True)
    (layout['root'] / '.staging-dead').mkdir(parents=True)
    (layout['root'] / '.swap-dead').mkdir(parents=True)
    # This stands in for user-owned/archive-adjacent state and must survive cleanup.
    protected = base / 'DO_NOT_DELETE_USER_STATE'
    protected.mkdir(parents=True)
    (protected / 'keep.txt').write_text('keep', encoding='utf-8')

    report = agent.cleanup_legacy_installations(base, layout)
    assert not (base / 'versions').exists(), report
    assert not (layout['root'] / '.staging-dead').exists(), report
    assert not (layout['root'] / '.swap-dead').exists(), report
    assert (layout['current'] / 'uyap_archive_agent.exe').read_bytes() == b'current'
    assert (layout['rollback'] / 'uyap_archive_agent.exe').read_bytes() == b'rollback'
    assert (protected / 'keep.txt').read_text(encoding='utf-8') == 'keep'

# A failed swap must never delete the agent that is still in place: a running
# portable agent locks its runtime, so renaming "current" fails before the new
# agent is moved in. The working agent has to survive untouched.
with tempfile.TemporaryDirectory(prefix='uyap-rc155-swapfail-') as td:
    real_layout = agent._agent_layout(Path(td) / 'UYAPArsiv')

    class LockedDir(type(Path())):
        def rename(self, target):
            raise PermissionError('locked by a running agent')

    layout = {**real_layout, 'current': LockedDir(str(real_layout['current']))}
    (real_layout['current'] / 'runtime').mkdir(parents=True)
    for name in ('ai_provider.py', 'python.exe', 'uyap_archive_agent.py'):
        (real_layout['current'] / 'runtime' / name).write_text(name, encoding='utf-8')
    new_exe = Path(td) / 'new_agent.exe'
    new_exe.write_bytes(b'new')
    stubs = {
        '_agent_layout': lambda _base: layout,
        'capture_registry_state': lambda: {},
        'probe_existing_compatible_host': lambda: {},
        'verify_host': lambda _path: None,
        '_validate_current': lambda _layout: {'ok': False},
        'terminate_running_agents': lambda: None,
        'restore_registry_state': lambda _previous: None,
    }
    saved = {name: getattr(agent, name) for name in stubs}
    saved_env = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = td
    try:
        for name, stub in stubs.items():
            setattr(agent, name, stub)
        try:
            agent.install(new_exe)
        except PermissionError:
            pass
        else:
            raise AssertionError('a failed swap must be reported')
    finally:
        for name, value in saved.items():
            setattr(agent, name, value)
        if saved_env is None:
            os.environ.pop('LOCALAPPDATA', None)
        else:
            os.environ['LOCALAPPDATA'] = saved_env
    kept = sorted(path.name for path in (real_layout['current'] / 'runtime').iterdir())
    assert kept == ['ai_provider.py', 'python.exe', 'uyap_archive_agent.py'], kept
    assert not any(path.name.startswith('.staging-') for path in real_layout['root'].iterdir())

stable = agent.host_manifest(Path(r'C:\Users\test\AppData\Local\UYAPArsiv\Agent\current\uyap_archive_agent.exe'), actual_id)
assert stable['path'].endswith(r'Agent\current\uyap_archive_agent.exe')

source = INSTALLER.read_text(encoding='utf-8')
for required in (
    "'current': agent_root / 'current'",
    "'rollback': agent_root / 'rollback'",
    'cleanup_legacy_installations',
    'restore_registry_state',
    'unregister_all_registry_views',
    'def repair(',
    'def rollback_install(',
    'def uninstall(',
    '--repair',
    '--rollback',
    '--uninstall',
    'archiveDataTouched',
):
    assert required in source, required

for bat in ('YEREL_AJANI_KUR.bat', 'YEREL_AJANI_ONAR.bat', 'YEREL_AJANI_GERI_AL.bat', 'YEREL_AJANI_KALDIR.bat'):
    assert (ROOT / bat).is_file(), bat

print('RC15.5 agent lifecycle/cleanup regression PASS')
