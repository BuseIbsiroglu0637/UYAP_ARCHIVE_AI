import importlib.util
import tempfile
from pathlib import Path
from unittest import mock

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent
spec=importlib.util.spec_from_file_location('agent', HERE/'uyap_archive_agent.py')
agent=importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)
DM=(ROOT/'background'/'download-manager.js').read_text(encoding='utf-8')
MAN=(ROOT/'manifest.json').read_text(encoding='utf-8')

def req(cond,msg):
    if not cond: raise AssertionError(msg)

def jpeg():
    return (b"\xff\xd8"+
            b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00"+
            b"\xff\xda\x00\x0c\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"+
            b"abc"+b"\xff\xd9")

ctx={
    'sourceDocumentId':'123456789',
    'bindingMethod':'native-uyap-discovered-source-id',
    'correlationMode':'exact',
    'correlationCandidateCount':1,
    'originalFilename':'evrak_123456789.html',
}

ping=agent.dispatch({'action':'ping','probe':'rc1597'})
req(ping.get('version')=='2.7.64', ping)
for cap in ('uyap_exact_content_truth_v1','jpeg_uyap_trailing_preserve_v1','html_circuit_deadline_v1'):
    req(cap in ping.get('capabilities',[]), (cap,ping))

with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    kep=td/'kep.html'; kep.write_text('<div><b>KEP İletisi:</b> 2024-08-26 tarihinde kullanıcı tarafından gönderilen ileti.</div>',encoding='utf-8')
    prof=agent.classify_html_payload(kep,ctx)
    req(prof.get('valid') is True and prof.get('mode')=='uyap_exact_correlated_html_v3',prof)

    login=td/'login.html'; login.write_text('<html><body><form><input type="password" name="password"></form></body></html>',encoding='utf-8')
    prof=agent.classify_html_payload(login,ctx)
    req(prof.get('valid') is False and prof.get('kind')=='error_response',prof)

    trailing=td/'trailing.jpg'; trailing.write_bytes(jpeg()+b'UYAP-SCANNER-META:opaque-source-metadata')
    with mock.patch.object(agent,'_jpeg_dimensions',return_value=None), \
         mock.patch.object(agent,'_jpeg_compatible_profile',return_value={'valid':False,'mode':'jpeg_compatible_trailing_data'}), \
         mock.patch.object(agent,'_windows_wic_jpeg_probe',return_value={'valid':True,'kind':'JPEG','extension':'.jpg','mode':'jpeg_windows_wic_decode','width':2047,'height':2047}), \
         mock.patch.object(agent,'_windows_gdiplus_jpeg_probe') as gdip:
        prof=agent.classify_image_payload(trailing,ctx)
        req(prof.get('valid') is True,prof)
        req(prof.get('mode')=='jpeg_uyap_trailing_preserved_wic',prof)
        req(prof.get('trailingBytes',0)>0,prof)
        req(gdip.call_count==0,'GDI+ should not run after WIC success')

    with mock.patch.object(agent,'_jpeg_dimensions',return_value=None), \
         mock.patch.object(agent,'_jpeg_compatible_profile',return_value={'valid':False,'mode':'jpeg_compatible_trailing_data'}), \
         mock.patch.object(agent,'_windows_wic_jpeg_probe') as wic:
        prof=agent.classify_image_payload(trailing,{})
        req(prof.get('valid') is False,prof)
        req(wic.call_count==0,'trailing JPEG without exact UYAP provenance must remain fail-closed')

    embedded=td/'embedded.jpg'; embedded.write_bytes(jpeg()+b'PK\x03\x04not-a-safe-trailer')
    with mock.patch.object(agent,'_jpeg_dimensions',return_value=None), \
         mock.patch.object(agent,'_jpeg_compatible_profile',return_value={'valid':False,'mode':'jpeg_compatible_trailing_data'}), \
         mock.patch.object(agent,'_windows_wic_jpeg_probe') as wic:
        prof=agent.classify_image_payload(embedded,ctx)
        req(prof.get('valid') is False,prof)
        req(prof.get('trailingKind')=='jpeg_trailer_embedded_file_signature',prof)
        req(wic.call_count==0,'embedded file trailer must fail before decoder')

req('function htmlCircuitDeadlineMs' in DM,'circuit deadline helper missing')
req('htmlCircuitNextProbeAt' in DM,'dedicated probe deadline missing')
req('Math.max(normalNextDownloadAt, circuitDeadline)' in DM,'performance may shorten circuit deadline')
req('circuitProbeValidation = htmlCircuitState(pipelineSession) === "half_open"' in DM,'half-open pipeline gate missing')
req('2.9.0 RC15.9.' in MAN,'manifest mismatch')
print('RC15.9.10 TRANSIENT ERROR STATE CLEANUP REGRESSION: PASS')
