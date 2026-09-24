from pathlib import Path
import importlib.util, tempfile

ROOT=Path(__file__).resolve().parent.parent
AGENT_PATH=ROOT/'native'/'uyap_archive_agent.py'
spec=importlib.util.spec_from_file_location('uyap_agent_rc1594', AGENT_PATH)
agent=importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)

def require(value,msg):
    if not value: raise AssertionError(msg)

require(agent.AGENT_VERSION == '2.7.64','Agent 2.7.64 required')
with tempfile.TemporaryDirectory() as td:
    td=Path(td)
    kep=td/'evrak_123456789.html'
    kep.write_text('<html><body>KEP İletisi:<br>2024-08-26 tarihinde kullanıcı tarafından gönderilen ileti ekte yer almaktadır.</body></html>',encoding='utf-8')
    ctx={'expectedDocumentKind':'uyap_html_document','sourceDocumentId':'123456789','originalFilename':kep.name,'bindingMethod':'native-uyap-discovered-source-id'}
    profile=agent.classify_html_payload(kep,ctx)
    require(profile.get('valid') is True,'declared UYAP HTML evrak accepted')
    require(profile.get('mode')=='uyap_declared_html_document_v2','declared HTML validation mode')
    require(agent.validate_content(kep,'.html',html_context=ctx)[0] is True,'declared UYAP HTML validates')

    login=td/'login.html'
    login.write_text('<html><body><form action="login"><input type="password"></form></body></html>',encoding='utf-8')
    bad=agent.classify_html_payload(login,ctx)
    require(bad.get('valid') is False and bad.get('kind')=='error_response','login HTML must still reject')
    require(agent.validate_content(login,'.html',html_context=ctx)[1]=='HTML_RESPONSE','login HTML error code')

    err=td/'error.html'
    err.write_text('<html><head><title>Hata</title></head><body>İşleminiz sırasında bir hata oluştu.</body></html>',encoding='utf-8')
    bad2=agent.classify_html_payload(err,ctx)
    require(bad2.get('valid') is False and bad2.get('kind')=='error_response','error HTML must still reject')

print('RC15.9.6 LEGITIMATE HTML DOCUMENT REGRESSION: PASS')
