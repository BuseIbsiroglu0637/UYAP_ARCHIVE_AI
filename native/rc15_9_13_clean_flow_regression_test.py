import copy
import hashlib
import tempfile
from pathlib import Path
import uyap_archive_agent as agent


def message(root, payload, identifier="1234567890"):
    source = root / f"evrak_{identifier}.html"
    source.write_bytes(payload)
    return {
        "action":"validate_download", "sessionId":"session", "attemptId":identifier,
        "filename":str(source), "stagingPath":f"UYAP_Arsiv/_incoming/session/{identifier}.part",
        "targetPath":f"UYAP_Arsiv/Court/Case/Documents/{identifier}.pdf", "extension":".pdf",
        "allowCanonicalAdoption":True, "directDownloadEvidence":True,
        "browserDownloadId":17, "browserState":"complete", "browserExists":True,
        "browserFileSize":len(payload), "browserTotalBytes":len(payload),
        "document":{"sessionId":"session", "documentId":identifier, "attemptId":identifier,
                    "downloadId":17, "bindingMethod":"extension-download-id", "attempts":1,
                    "sourceSystem":"UYAP_KURUM", "caseId":"Case", "documentTitle":"KEP"}
    }


with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    payload = '<html><body><b>KEP Paketi:</b> tarihinde gönderilen KEP iletisi ektedir.</body></html>'.encode()
    request = message(root, payload)
    # Reproduce RC15.9.13: extension-download-id was excluded from adoption.
    old = copy.deepcopy(request)
    old["allowCanonicalAdoption"] = False
    try:
        agent.dispatch(old)
        raise AssertionError("Old staging failure not reproduced")
    except ValueError as error:
        assert "Kaynak yalnızca" in str(error)
    result = agent.dispatch(request)
    assert result.get("valid"), result
    assert result["extension"] == ".html", result
    assert Path(result["localPath"]).read_bytes() == payload
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()

    for identifier, invalid in (
        ("1234567891", b'<html><form action="login"><input type="password"></form></html>'),
        ("1234567892", b'<html><title>Error</title>Internal server error</html>'),
    ):
        rejected = agent.dispatch(message(root, invalid, identifier))
        assert rejected.get("valid") is False and rejected["errorCode"] == "HTML_RESPONSE", rejected

    mismatched = message(root, payload, "1234567893")
    mismatched["browserDownloadId"] = 99
    try:
        agent.dispatch(mismatched)
        raise AssertionError("Mismatched browser identity accepted")
    except ValueError:
        assert Path(mismatched["filename"]).exists()

    collision = message(root, payload, "1234567894")
    staged = root / collision["stagingPath"]
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"different staged content")
    recovered = agent.dispatch(collision)
    assert recovered.get("valid"), recovered
    assert staged.read_bytes() == b"different staged content"
    assert Path(recovered["localPath"]).read_bytes() == payload

print("RC15.9.13: old failure reproduced; HTML archived byte-exact; login/error rejected; exact identity and collision recovery protected: PASS")
