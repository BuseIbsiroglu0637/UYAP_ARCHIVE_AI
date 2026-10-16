import importlib.util
import tempfile
from pathlib import Path
from unittest import mock

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
spec=importlib.util.spec_from_file_location("agent", HERE/"uyap_archive_agent.py")
agent=importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
DM=(ROOT/'background'/'download-manager.js').read_text(encoding='utf-8')
MAN=(ROOT/'manifest.json').read_text(encoding='utf-8')

def req(cond,msg):
    if not cond: raise AssertionError(msg)

def jpeg():
    return (b"\xff\xd8" +
            b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00" +
            b"\xff\xda\x00\x0c\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00" +
            b"abc" + b"\xff\xd9")

ping=agent.dispatch({"action":"ping","probe":"rc1596"})
req(tuple(map(int, str(ping.get("version")).split("."))) >= (2, 7, 64), ping)
req('jpeg_windows_wic_decoder_v1' in ping.get('capabilities',[]), ping)
req('document_type_truth_v1' in ping.get('capabilities',[]), ping)
req('function declaredUyapDocumentKindFromDownload' in DM, 'download-item truth helper missing')
req('download-item-url' in DM, 'document kind evidence missing')
req('completedDeclaredKind = declaredUyapDocumentKindFromDownload(item)' in DM, 'completion truth missing')
req('htmlCircuitState: "open"' in DM and 'htmlCircuitState: "half_open"' in DM, 'circuit state machine missing')
req('HTML_CIRCUIT_PROBE_FAILED' in DM, 'half-open failure event missing')
req('2.9.0 RC15.9.' in MAN, 'manifest version mismatch')

with tempfile.TemporaryDirectory() as td:
    p=Path(td)/'sample.jpg'; p.write_bytes(jpeg())
    with mock.patch.object(agent,'_jpeg_dimensions',return_value=None), \
         mock.patch.object(agent,'_jpeg_compatible_profile',return_value={'valid':False,'mode':'jpeg_compatible_marker_sync'}), \
         mock.patch.object(agent,'_windows_wic_jpeg_probe',return_value={'valid':True,'kind':'JPEG','extension':'.jpg','mode':'jpeg_windows_wic_decode','width':2992,'height':2992}), \
         mock.patch.object(agent,'_windows_gdiplus_jpeg_probe') as gdip:
        prof=agent.classify_image_payload(p)
        req(prof.get('valid') is True, prof)
        req(prof.get('mode')=='jpeg_windows_wic_decode', prof)
        req(prof.get('fallbackFromMode')=='jpeg_compatible_marker_sync', prof)
        req(gdip.call_count==0, 'GDI+ must not run after WIC succeeds')

    with mock.patch.object(agent,'_jpeg_dimensions',return_value=None), \
         mock.patch.object(agent,'_jpeg_compatible_profile',return_value={'valid':False,'mode':'jpeg_compatible_marker_sync'}), \
         mock.patch.object(agent,'_windows_wic_jpeg_probe',return_value={'valid':False,'mode':'jpeg_windows_wic_rejected'}), \
         mock.patch.object(agent,'_windows_gdiplus_jpeg_probe',return_value={'valid':False,'mode':'jpeg_windows_decoder_rejected'}):
        prof=agent.classify_image_payload(p)
        req(prof.get('valid') is False, prof)
        req(prof.get('mode')=='jpeg_windows_decoders_rejected', prof)
        req(prof.get('wicFallbackMode')=='jpeg_windows_wic_rejected', prof)
        req(prof.get('gdiplusFallbackMode')=='jpeg_windows_decoder_rejected', prof)

    bad=Path(td)/'trailing.jpg'; bad.write_bytes(jpeg()+b'EVIL')
    with mock.patch.object(agent,'_jpeg_dimensions',return_value=None), \
         mock.patch.object(agent,'_jpeg_compatible_profile',return_value={'valid':False,'mode':'jpeg_compatible_trailing_data'}), \
         mock.patch.object(agent,'_windows_wic_jpeg_probe') as wic, \
         mock.patch.object(agent,'_windows_gdiplus_jpeg_probe') as gdip:
        prof=agent.classify_image_payload(bad)
        req(prof.get('valid') is False, prof)
        req(wic.call_count==0 and gdip.call_count==0, 'OS decoders must not run with unsafe trailing bytes')

print('RC15.9.6 DOCUMENT TYPE TRUTH + WIC REGRESSION: PASS')
