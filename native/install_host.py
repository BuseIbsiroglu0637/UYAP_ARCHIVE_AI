"""Build/install a standalone Windows Native Messaging host; never a proxy.

No registry or existing installation is changed until two open-pipe pings pass
from the final installation path. Only per-user HKCU registration is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

from host_self_test import check_host

VERSION = '2.7.68'
MIN_REUSABLE_VERSION = '2.7.68'
HOST_NAME = 'tr.gov.uyap.archive_agent'
STANDARD_EXTENSION_ID = 'mkcemhoekaohggeebgkifcmjbilincfl'
EXTENSION_ID = 'fppnhbkemmbocmicplkfidcbggeininh'
ALLOWED_EXTENSION_IDS = (STANDARD_EXTENSION_ID, EXTENSION_ID)
NATIVE = Path(__file__).resolve().parent

# Chromium browsers use separate NativeMessagingHosts registry roots on Windows.
# Registering only Chrome makes a perfectly healthy host look "missing" in Edge,
# especially on a clean machine where there is no stale registration to mask it.
BROWSER_REGISTRY_ROOTS = (
    ('Google Chrome', r'Software\Google\Chrome\NativeMessagingHosts'),
    ('Microsoft Edge', r'Software\Microsoft\Edge\NativeMessagingHosts'),
)


def extension_id(manifest):
    import base64
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(n, 16)) for n in digest)


def host_manifest(executable, allowed_id):
    ids = list(dict.fromkeys((*ALLOWED_EXTENSION_IDS, allowed_id)))
    for item in ids:
        if len(item) != 32 or any(c not in 'abcdefghijklmnop' for c in item):
            raise ValueError('INVALID_EXTENSION_ID')
    return {'name':HOST_NAME, 'description':'UYAP yerel arsiv dogrulama ajani',
            'path':str(executable), 'type':'stdio',
            'allowed_origins':[f'chrome-extension://{item}/' for item in ids]}



def version_tuple(value):
    try:
        parts = str(value).strip().split('.')
        if not parts or any(not part.isdigit() for part in parts):
            return None
        numbers = tuple(int(part) for part in parts)
        return numbers + (0,) * max(0, 3 - len(numbers))
    except Exception:
        return None


def is_reusable_agent_version(value):
    current = version_tuple(value)
    minimum = version_tuple(MIN_REUSABLE_VERSION)
    if current is None or minimum is None:
        return False
    # Protocol/capability compatibility for this release is guaranteed only
    # inside the 2.x agent family from 2.5.0 onward.
    return current[0] == 2 and current >= minimum


def validate_existing_manifest(manifest_data, manifest_path, check_fn=check_host):
    if not isinstance(manifest_data, dict):
        return {'ok': False, 'reason': 'Kayitli Native Messaging manifesti gecersiz.'}
    if manifest_data.get('name') != HOST_NAME:
        return {'ok': False, 'reason': 'Kayitli host adi beklenen ajanla eslesmiyor.'}
    allowed = manifest_data.get('allowed_origins') or []
    expected_origins = [f'chrome-extension://{item}/' for item in ALLOWED_EXTENSION_IDS]
    missing = [origin for origin in expected_origins if origin not in allowed]
    if missing:
        return {'ok': False, 'reason': 'Kayitli ajan standart ve buyuk-dosya uzantilarinin ikisine birden izin vermiyor.'}
    raw_path = manifest_data.get('path')
    if not raw_path:
        return {'ok': False, 'reason': 'Kayitli ajan EXE yolu bulunamadi.'}
    executable = Path(raw_path)
    if not executable.is_absolute():
        executable = Path(manifest_path).resolve().parent / executable
    if not executable.is_file():
        return {'ok': False, 'reason': 'Kayitli ajan EXE dosyasi artik mevcut degil.', 'path': str(executable)}
    try:
        version = check_fn([str(executable)], timeout=15)
    except Exception as error:
        return {'ok': False, 'reason': 'Mevcut ajan baglanti testini gecemedi: ' + str(error), 'path': str(executable)}
    if not is_reusable_agent_version(version):
        return {
            'ok': False,
            'reason': f'Mevcut ajan {version} bu surum icin uyumlu degil; en az {MIN_REUSABLE_VERSION} gerekir.',
            'version': version,
            'path': str(executable)
        }
    return {'ok': True, 'version': version, 'path': str(executable), 'manifestPath': str(manifest_path)}


def _registry_views():
    """Chrome checks the 32-bit Windows registry view before the 64-bit view."""
    import winreg
    views = []
    if hasattr(winreg, 'KEY_WOW64_32KEY'):
        views.append(('32-bit', winreg.KEY_WOW64_32KEY))
    if hasattr(winreg, 'KEY_WOW64_64KEY'):
        views.append(('64-bit', winreg.KEY_WOW64_64KEY))
    if not views:
        views.append(('default', 0))
    return views


def _registry_path(browser_root):
    return rf'{browser_root}\{HOST_NAME}'


def _read_registered_manifest(browser_root, view_flag):
    import winreg
    registry = _registry_path(browser_root)
    access = winreg.KEY_READ | view_flag
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry, 0, access) as key:
        return Path(winreg.QueryValueEx(key, '')[0])


def probe_existing_compatible_host():
    if os.name != 'nt':
        return {'ok': False, 'reason': 'Windows disinda mevcut ajan kaydi aranmaz.'}
    found = []
    for browser_name, browser_root in BROWSER_REGISTRY_ROOTS:
        for view_name, view_flag in _registry_views():
            try:
                manifest_path = _read_registered_manifest(browser_root, view_flag)
            except FileNotFoundError:
                continue
            except Exception as error:
                found.append({'ok': False, 'browser': browser_name, 'view': view_name, 'reason': 'Mevcut ajan kaydi okunamadi: ' + str(error)})
                continue
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
                result = validate_existing_manifest(manifest_data, manifest_path)
                result['browser'] = browser_name
                result['view'] = view_name
                found.append(result)
                if result.get('ok'):
                    return result
            except Exception as error:
                found.append({'ok': False, 'browser': browser_name, 'view': view_name, 'reason': 'Mevcut ajan manifesti okunamadi: ' + str(error), 'manifestPath': str(manifest_path)})
    if found:
        detail = '; '.join(f"{x.get('browser')} {x.get('view')}: {x.get('reason') or x.get('version') or 'uyumsuz'}" for x in found)
        return {'ok': False, 'reason': detail, 'views': found}
    return {'ok': False, 'reason': 'Chrome/Edge Native Messaging ajan kaydi bulunamadi.'}


def detect_installed_browsers():
    candidates = {
        'Google Chrome': [
            Path(os.environ.get('PROGRAMFILES', '')) / 'Google/Chrome/Application/chrome.exe',
            Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'Google/Chrome/Application/chrome.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Google/Chrome/Application/chrome.exe',
        ],
        'Microsoft Edge': [
            Path(os.environ.get('PROGRAMFILES', '')) / 'Microsoft/Edge/Application/msedge.exe',
            Path(os.environ.get('PROGRAMFILES(X86)', '')) / 'Microsoft/Edge/Application/msedge.exe',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Microsoft/Edge/Application/msedge.exe',
        ],
    }
    result = {}
    for browser, paths in candidates.items():
        existing = next((str(x) for x in paths if str(x) and x.is_file()), None)
        result[browser] = existing
    return result


def authenticode_info(executable):
    """Return Windows Authenticode status without trusting file-name or origin metadata."""
    if os.name != 'nt':
        return {'status': 'NotWindows', 'subject': None, 'thumbprint': None}
    env = dict(os.environ)
    env['UYAP_SIGN_TARGET'] = str(executable)
    command = (
        "$s=Get-AuthenticodeSignature -LiteralPath $env:UYAP_SIGN_TARGET; "
        "$o=[pscustomobject]@{status=$s.Status.ToString();subject=$s.SignerCertificate.Subject;thumbprint=$s.SignerCertificate.Thumbprint}; "
        "$o|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            check=True, capture_output=True, text=True, timeout=20, env=env
        )
        return json.loads(completed.stdout.strip() or '{}')
    except Exception as error:
        raise RuntimeError(f'Kod imzasi dogrulanamadi: {error}') from error


def require_trusted_signature(executable):
    info = authenticode_info(executable)
    if info.get('status') != 'Valid':
        raise RuntimeError(
            'IMZALI_AJAN_GEREKLI: Windows Smart App Control icin CA tarafindan verilmis gecerli '
            'Authenticode imzali ajan gerekir. Guvenlik ozelliklerini kapatmayin. '
            'Imzali UYAP Arsivleyici Setup paketini kullanin.'
        )
    print('KOD IMZASI GECERLI - ' + str(info.get('subject') or 'Yayimci'))
    return info


def verify_host(executable):
    try:
        version = check_host([str(executable)], timeout=30)
    except OSError as error:
        if getattr(error, 'winerror', None) == 4551 or '4551' in str(error):
            raise RuntimeError(
                'WINDOWS_APP_CONTROL_BLOCKED: Windows Uygulama Denetimi imzasiz/bilinmeyen EXE dosyasini engelledi. '
                'Bu son kullanici bilgisayarinda guvenlik ozelligi kapatilarak asilmamalidir; imzali ajan gerekir.'
            ) from error
        raise
    if version != VERSION:
        raise RuntimeError(f'Ajan surumu uyumsuz: {version}; beklenen {VERSION}')
    print(f'BAGLANTI TESTI BASARILI - Ajan {version} - 2/2 ping')


def build_host(yes=False):
    print('Windows EXE bu bilgisayarda uretilecek. Yalnizca derleme icin PyInstaller')
    print('ve bagimliliklari PyPI uzerinden indirilecek; UYAP verisi gonderilmez.')
    if not yes and input('Derleme bagimliliklari indirilsin mi? [E/h]: ').strip().lower() not in ('e', 'evet'):
        raise RuntimeError('Derleme iptal edildi. Mevcut kurulum degistirilmedi.')
    if sys.version_info < (3, 11):
        raise RuntimeError('Derleme icin Python 3.11 veya ustu gerekir.')
    with tempfile.TemporaryDirectory(prefix='uyap-native-build-') as work:
        work = Path(work)
        venv = work / 'venv'
        subprocess.run([sys.executable, '-m', 'venv', str(venv)], check=True)
        python = venv / 'Scripts' / 'python.exe'
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check',
                        '-r', str(NATIVE / 'requirements-build.txt')], check=True)
        subprocess.run([str(python), '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console',
                        '--name', 'uyap_archive_agent', '--distpath', str(work / 'dist'),
                        '--workpath', str(work / 'build'), '--specpath', str(work),
                        str(NATIVE / 'uyap_archive_agent.py')], check=True, cwd=NATIVE)
        produced = work / 'dist' / 'uyap_archive_agent.exe'
        verify_host(produced)
        destination = NATIVE / 'dist' / 'uyap_archive_agent.exe'
        destination.parent.mkdir(exist_ok=True)
        shutil.copy2(produced, destination)
        return destination


def terminate_running_agents():
    """Stop previously connected Native Messaging host processes after registry switch.

    An already-open Chrome native port keeps the old EXE alive even after the
    registry points at a newer version. Killing only our host image forces Chrome
    to reconnect to the newly registered agent. Failure to find a running process
    is harmless.
    """
    if os.name != 'nt':
        return
    try:
        subprocess.run(
            ['taskkill', '/IM', 'uyap_archive_agent.exe', '/F'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
    except Exception:
        pass


def register_manifest_all_registry_views(native_manifest):
    import winreg
    written = []
    errors = []
    for browser_name, browser_root in BROWSER_REGISTRY_ROOTS:
        registry = _registry_path(browser_root)
        for view_name, view_flag in _registry_views():
            label = f'{browser_name} {view_name}'
            try:
                access = winreg.KEY_SET_VALUE | view_flag
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, registry, 0, access) as key:
                    winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(native_manifest))
                written.append(label)
            except Exception as error:
                errors.append(f'{label}: {error}')
    if not written:
        raise RuntimeError('Native Messaging registry kaydi yazilamadi: ' + '; '.join(errors))

    verified = []
    for browser_name, browser_root in BROWSER_REGISTRY_ROOTS:
        for view_name, view_flag in _registry_views():
            label = f'{browser_name} {view_name}'
            try:
                current = _read_registered_manifest(browser_root, view_flag)
            except FileNotFoundError:
                continue
            if current.resolve() != Path(native_manifest).resolve():
                raise RuntimeError(f'{label} registry gorunumu eski ajana isaret ediyor: {current}')
            data = json.loads(current.read_text(encoding='utf-8-sig'))
            result = validate_existing_manifest(data, current)
            if not result.get('ok') or result.get('version') != VERSION:
                raise RuntimeError(f'{label} registry dogrulamasi basarisiz: {result.get("reason") or result.get("version")}')
            verified.append(label)
    print('REGISTRY DOGRULANDI - ' + ', '.join(verified or written) + ' -> Ajan ' + VERSION)
    if errors:
        print('Registry notu: ' + '; '.join(errors))
    return {'written': written, 'verified': verified, 'errors': errors}



def _agent_layout(base):
    agent_root = base / 'Agent'
    return {
        'root': agent_root,
        'current': agent_root / 'current',
        'rollback': agent_root / 'rollback',
        'manifest': agent_root / f'{HOST_NAME}.json',
    }


def _safe_remove_tree(path, base):
    path = Path(path)
    base = Path(base).resolve()
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path.absolute()
    try:
        resolved.relative_to(base)
    except ValueError as error:
        raise RuntimeError(f'Guvenlik nedeniyle kurulum kokunun disindaki yol silinmedi: {resolved}') from error
    if path.exists():
        shutil.rmtree(path)


def _direct_agent_version(executable):
    if not Path(executable).is_file():
        return None
    try:
        return check_host([str(executable)], timeout=15)
    except Exception:
        return None


def capture_registry_state():
    if os.name != 'nt':
        return {}
    import winreg
    previous = {}
    for browser_name, browser_root in BROWSER_REGISTRY_ROOTS:
        previous[browser_name] = {}
        registry = _registry_path(browser_root)
        for view_name, view_flag in _registry_views():
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, registry, 0, winreg.KEY_READ | view_flag) as key:
                    previous[browser_name][view_name] = winreg.QueryValueEx(key, '')[0]
            except FileNotFoundError:
                previous[browser_name][view_name] = None
            except Exception as error:
                previous[browser_name][view_name] = {'error': str(error)}
    return previous


def _delete_registry_key(browser_root, view_flag):
    import winreg
    registry = _registry_path(browser_root)
    try:
        if hasattr(winreg, 'DeleteKeyEx'):
            winreg.DeleteKeyEx(winreg.HKEY_CURRENT_USER, registry, view_flag, 0)
        else:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, registry)
        return True
    except FileNotFoundError:
        return False


def restore_registry_state(previous):
    if os.name != 'nt':
        return
    import winreg
    roots = dict(BROWSER_REGISTRY_ROOTS)
    views = dict(_registry_views())
    for browser_name, browser_state in (previous or {}).items():
        browser_root = roots.get(browser_name)
        if not browser_root:
            continue
        for view_name, value in (browser_state or {}).items():
            view_flag = views.get(view_name)
            if view_flag is None:
                continue
            if isinstance(value, dict) and value.get('error'):
                continue
            if value is None:
                try:
                    _delete_registry_key(browser_root, view_flag)
                except Exception:
                    pass
                continue
            registry = _registry_path(browser_root)
            try:
                with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, registry, 0, winreg.KEY_SET_VALUE | view_flag) as key:
                    winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(value))
            except Exception:
                pass


def unregister_all_registry_views():
    removed = []
    errors = []
    if os.name != 'nt':
        return {'removed': removed, 'errors': errors}
    for browser_name, browser_root in BROWSER_REGISTRY_ROOTS:
        for view_name, view_flag in _registry_views():
            label = f'{browser_name} {view_name}'
            try:
                if _delete_registry_key(browser_root, view_flag):
                    removed.append(label)
            except Exception as error:
                errors.append(f'{label}: {error}')
    return {'removed': removed, 'errors': errors}


def _write_stable_manifest(layout, allowed_id):
    layout['root'].mkdir(parents=True, exist_ok=True)
    current_host = layout['current'] / 'uyap_archive_agent.exe'
    layout['manifest'].write_text(
        json.dumps(host_manifest(current_host, allowed_id), ensure_ascii=False, indent=2),
        encoding='utf-8'
    )
    return layout['manifest']


def _copy_legacy_rollback(base, layout, previous_probe):
    if layout['rollback'].exists() or not previous_probe or not previous_probe.get('ok'):
        return None
    source = Path(previous_probe.get('path') or '')
    if not source.is_file():
        return None
    # Do not duplicate an already managed current binary.
    try:
        if source.resolve() == (layout['current'] / 'uyap_archive_agent.exe').resolve():
            return None
    except Exception:
        pass
    layout['rollback'].mkdir(parents=True, exist_ok=True)
    target = layout['rollback'] / 'uyap_archive_agent.exe'
    shutil.copy2(source, target)
    version = _direct_agent_version(target)
    if not version or not is_reusable_agent_version(version):
        _safe_remove_tree(layout['rollback'], layout['root'])
        return None
    return {'source': str(source), 'version': version, 'target': str(target)}


def cleanup_legacy_installations(base, layout=None):
    """Remove only obsolete agent-install artifacts after the active host is verified.

    User archives, IndexedDB/browser profile data, download roots and UYAP_Arsiv
    folders are deliberately outside this cleanup scope.
    """
    base = Path(base)
    layout = layout or _agent_layout(base)
    removed = []
    failed = []

    legacy_versions = base / 'versions'
    if legacy_versions.exists():
        try:
            _safe_remove_tree(legacy_versions, base)
            removed.append(str(legacy_versions))
        except Exception as error:
            failed.append({'path': str(legacy_versions), 'error': str(error)})

    if layout['root'].exists():
        for child in list(layout['root'].iterdir()):
            if child.name in {'current', 'rollback', layout['manifest'].name}:
                continue
            if child.name.startswith(('.staging-', '.failed-', '.swap-')):
                try:
                    if child.is_dir():
                        _safe_remove_tree(child, layout['root'])
                    else:
                        child.unlink(missing_ok=True)
                    removed.append(str(child))
                except Exception as error:
                    failed.append({'path': str(child), 'error': str(error)})
    return {'removed': removed, 'failed': failed}


def write_install_diagnostic(base, layout, allowed_id, registration, previous, cleanup=None, action='install', migration=None):
    diagnostic = {
        'schemaVersion': 2,
        'kind': 'UYAP_NATIVE_INSTALL_DIAGNOSTIC',
        'lifecycleLayout': 'current-rollback-v1',
        'action': action,
        'agentVersion': VERSION,
        'hostName': HOST_NAME,
        'extensionId': allowed_id,
        'allowedExtensionIds': list(dict.fromkeys((*ALLOWED_EXTENSION_IDS, allowed_id))),
        'installedBrowsers': detect_installed_browsers(),
        'registry': registration,
        'previousRegistration': previous,
        'agentRoot': str(layout['root']),
        'currentDirectory': str(layout['current']),
        'rollbackDirectory': str(layout['rollback']),
        'hostPath': str(layout['current'] / 'uyap_archive_agent.exe'),
        'manifestPath': str(layout['manifest']),
        'cleanup': cleanup or {'removed': [], 'failed': []},
        'legacyMigration': migration,
        'archiveDataTouched': False,
    }
    base.mkdir(parents=True, exist_ok=True)
    path = base / 'native-install-diagnostic.json'
    path.write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
    layout['root'].mkdir(parents=True, exist_ok=True)
    (layout['root'] / 'install-diagnostic.json').write_text(json.dumps(diagnostic, ensure_ascii=False, indent=2), encoding='utf-8')
    print('KURULUM TANILAMA: ' + str(path))
    return path


def _validate_current(layout):
    host = layout['current'] / 'uyap_archive_agent.exe'
    version = _direct_agent_version(host)
    return {'ok': bool(version and is_reusable_agent_version(version)), 'version': version, 'path': str(host)}


def install(executable):
    manifest_path = NATIVE.parent / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
    allowed_id = extension_id(manifest)
    if allowed_id != EXTENSION_ID:
        raise RuntimeError('Manifest anahtari degismis. Kurulum durduruldu.')

    base = Path(os.environ['LOCALAPPDATA']) / 'UYAPArsiv'
    layout = _agent_layout(base)
    layout['root'].mkdir(parents=True, exist_ok=True)
    previous = capture_registry_state()
    previous_probe = probe_existing_compatible_host()

    staging = layout['root'] / f'.staging-{uuid.uuid4().hex}'
    staging.mkdir(parents=True)
    staging_host = staging / 'uyap_archive_agent.exe'
    shutil.copy2(executable, staging_host)
    verify_host(staging_host)

    migration = _copy_legacy_rollback(base, layout, previous_probe)
    current_before = _validate_current(layout)
    backed_up_current = False
    swap_temp = None
    previous_rollback_temp = None

    terminate_running_agents()
    try:
        if layout['current'].exists():
            if current_before.get('ok'):
                if layout['rollback'].exists():
                    previous_rollback_temp = layout['root'] / f'.swap-old-rollback-{uuid.uuid4().hex}'
                    layout['rollback'].rename(previous_rollback_temp)
                layout['current'].rename(layout['rollback'])
                backed_up_current = True
            else:
                swap_temp = layout['root'] / f'.failed-{uuid.uuid4().hex}'
                layout['current'].rename(swap_temp)

        staging.rename(layout['current'])
        _write_stable_manifest(layout, allowed_id)
        registration = register_manifest_all_registry_views(layout['manifest'])
        verify_host(layout['current'] / 'uyap_archive_agent.exe')

        # Only after the fixed current path + registry + ping all pass do we
        # remove obsolete UUID installs and failed staging leftovers.
        cleanup = cleanup_legacy_installations(base, layout)
        if swap_temp and swap_temp.exists():
            try:
                _safe_remove_tree(swap_temp, layout['root'])
                cleanup['removed'].append(str(swap_temp))
            except Exception as error:
                cleanup['failed'].append({'path': str(swap_temp), 'error': str(error)})

        write_install_diagnostic(base, layout, allowed_id, registration, previous, cleanup, action='install', migration=migration)
        terminate_running_agents()
        print('AJAN YASAM DONGUSU DOGRULANDI - current aktif, rollback en fazla bir onceki saglam ajan.')
        print('ESKI UUID AJAN KLASORLERI TEMIZLENDI - arsiv ve kuyruk verilerine dokunulmadi.')
        print('Chrome veya Edge uzantisini yeniden yukleyin. Gerekirse tarayiciyi tamamen kapatip yeniden acin.')
        print('Dosya: ' + str(layout['current'] / 'uyap_archive_agent.exe'))
        return
    except Exception:
        # Restore the prior managed current if we moved it to rollback.
        try:
            if layout['current'].exists():
                _safe_remove_tree(layout['current'], layout['root'])
            if backed_up_current and layout['rollback'].exists():
                layout['rollback'].rename(layout['current'])
                _write_stable_manifest(layout, allowed_id)
            elif swap_temp and swap_temp.exists():
                swap_temp.rename(layout['current'])
            if previous_rollback_temp and previous_rollback_temp.exists():
                if layout['rollback'].exists():
                    _safe_remove_tree(layout['rollback'], layout['root'])
                previous_rollback_temp.rename(layout['rollback'])
            restore_registry_state(previous)
        finally:
            if staging.exists():
                try:
                    _safe_remove_tree(staging, layout['root'])
                except Exception:
                    pass
        raise


def repair(executable=None):
    manifest = json.loads((NATIVE.parent / 'manifest.json').read_text(encoding='utf-8-sig'))
    allowed_id = extension_id(manifest)
    base = Path(os.environ['LOCALAPPDATA']) / 'UYAPArsiv'
    layout = _agent_layout(base)
    previous = capture_registry_state()
    current = _validate_current(layout)
    if current.get('ok'):
        _write_stable_manifest(layout, allowed_id)
        registration = register_manifest_all_registry_views(layout['manifest'])
        verify_host(layout['current'] / 'uyap_archive_agent.exe')
        cleanup = cleanup_legacy_installations(base, layout)
        write_install_diagnostic(base, layout, allowed_id, registration, previous, cleanup, action='repair')
        terminate_running_agents()
        print('ONARIM BASARILI - current ajan ve Chrome/Edge kayitlari yeniden dogrulandi.')
        return
    if executable is None:
        raise RuntimeError('ONARIM_ICIN_AJAN_GEREKLI: current ajan bulunamadi veya bozuk.')
    print('Current ajan bulunamadi/bozuk; guvenli kurulum akisi calistiriliyor.')
    install(executable)


def rollback_install():
    manifest = json.loads((NATIVE.parent / 'manifest.json').read_text(encoding='utf-8-sig'))
    allowed_id = extension_id(manifest)
    base = Path(os.environ['LOCALAPPDATA']) / 'UYAPArsiv'
    layout = _agent_layout(base)
    rollback_host = layout['rollback'] / 'uyap_archive_agent.exe'
    rollback_version = _direct_agent_version(rollback_host)
    if not rollback_version or not is_reusable_agent_version(rollback_version):
        raise RuntimeError('GERI_ALMA_YOK: Dogrulanmis rollback ajan bulunamadi.')

    previous = capture_registry_state()
    swap = layout['root'] / f'.swap-{uuid.uuid4().hex}'
    terminate_running_agents()
    try:
        if layout['current'].exists():
            layout['current'].rename(swap)
        layout['rollback'].rename(layout['current'])
        if swap.exists():
            swap.rename(layout['rollback'])
        _write_stable_manifest(layout, allowed_id)
        registration = register_manifest_all_registry_views(layout['manifest'])
        verify_host(layout['current'] / 'uyap_archive_agent.exe')
        cleanup = cleanup_legacy_installations(base, layout)
        write_install_diagnostic(base, layout, allowed_id, registration, previous, cleanup, action='rollback')
        terminate_running_agents()
        print('GERI ALMA BASARILI - onceki saglam ajan current konumuna alindi.')
    except Exception:
        try:
            # Best-effort reverse swap.
            if layout['rollback'].exists() and not swap.exists() and layout['current'].exists():
                layout['current'].rename(swap)
                layout['rollback'].rename(layout['current'])
                swap.rename(layout['rollback'])
            restore_registry_state(previous)
        finally:
            pass
        raise


def uninstall():
    base = Path(os.environ['LOCALAPPDATA']) / 'UYAPArsiv'
    layout = _agent_layout(base)
    terminate_running_agents()
    registration = unregister_all_registry_views()
    removed = []
    failed = []
    for path in (layout['root'], base / 'versions'):
        if path.exists():
            try:
                _safe_remove_tree(path, base)
                removed.append(str(path))
            except Exception as error:
                failed.append({'path': str(path), 'error': str(error)})
    for file_name in ('native-install-diagnostic.json',):
        path = base / file_name
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except Exception as error:
            failed.append({'path': str(path), 'error': str(error)})
    print('AJAN KALDIRILDI - Chrome/Edge Native Messaging kayitlari ve ajan kurulum kalintilari temizlendi.')
    print('UYAP arsiv evraklari, indirme klasorleri ve tarayici IndexedDB/kuyruk verileri SILINMEDI.')
    if registration.get('errors') or failed:
        print('Kaldirma notlari: ' + json.dumps({'registry': registration, 'failed': failed}, ensure_ascii=False))


def _resolve_executable_for_install(args):
    executable = NATIVE / 'dist' / 'uyap_archive_agent.exe'
    if args.require_signed:
        if not executable.is_file():
            existing = probe_existing_compatible_host()
            if existing.get('ok'):
                return Path(existing['path'])
            detail = existing.get('reason') or 'Mevcut uyumlu ajan bulunamadi.'
            raise RuntimeError(
                'IMZALI_AJAN_PAKETTE_YOK: Son kullanici kurulumu yerelde EXE derlemez. '
                'Mevcut uyumlu ajan da bulunamadi. ' + detail + ' Imzali Setup/ajan release paketi gerekir.'
            )
        require_trusted_signature(executable)
    elif args.build_only or args.allow_unsigned_dev:
        should_build = bool(args.build_only or args.force_build or not executable.is_file())
        if not should_build and executable.is_file():
            try:
                verify_host(executable)
            except Exception:
                should_build = True
        if should_build:
            executable = build_host(args.yes)
    elif not executable.is_file():
        raise RuntimeError('Ajan EXE bulunamadi. Uretimde imzali Setup kullanin; gelistirmede --allow-unsigned-dev secenegi vardir.')
    verify_host(executable)
    return executable


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--yes', action='store_true', help='Explicitly accept build dependency download (CI).')
    parser.add_argument('--require-signed', action='store_true', help='Production install: refuse unsigned/untrusted agent binaries.')
    parser.add_argument('--allow-unsigned-dev', action='store_true', help='Developer-only local build/install; never for customer distribution.')
    parser.add_argument('--force-build', action='store_true', help='Force rebuilding the local developer EXE even if a packaged copy exists.')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--repair', action='store_true', help='Repair current host/registry and clean stale installs.')
    mode.add_argument('--rollback', action='store_true', help='Swap current and rollback agents after validation.')
    mode.add_argument('--uninstall', action='store_true', help='Remove only the native-agent lifecycle installation and registry keys.')
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Windows EXE yalnizca Windows uzerinde derlenebilir ve kurulabilir.')

    if args.uninstall:
        uninstall()
        return
    if args.rollback:
        rollback_install()
        return

    if args.repair:
        base = Path(os.environ['LOCALAPPDATA']) / 'UYAPArsiv'
        layout = _agent_layout(base)
        current = _validate_current(layout)
        if current.get('ok'):
            repair(None)
            return
        executable = _resolve_executable_for_install(args)
        repair(executable)
        return

    executable = _resolve_executable_for_install(args)
    if not args.build_only:
        install(executable)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('KURULUM BASARISIZ: ' + str(error), file=sys.stderr)
        print('Onceki calisan ajan/registry mumkun oldugunca geri yuklenir. UYAP arsiv verileri silinmez.', file=sys.stderr)
        sys.exit(1)
