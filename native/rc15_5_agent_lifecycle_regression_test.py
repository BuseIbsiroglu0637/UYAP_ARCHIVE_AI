from __future__ import annotations
import importlib.util
import json
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
