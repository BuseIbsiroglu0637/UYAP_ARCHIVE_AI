from pathlib import Path
import importlib.util
import json
import tempfile

ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = ROOT / "native" / "uyap_archive_agent.py"
MANAGER = (ROOT / "background" / "download-manager.js").read_text(encoding="utf-8")
BRIDGE = (ROOT / "background" / "native-agent.js").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def req(condition, message):
    if not condition:
        raise AssertionError(message)


def load_agent():
    spec = importlib.util.spec_from_file_location("uyap_agent_rc15933", AGENT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def minimal_pdf():
    chunks = [b"%PDF-1.4\n", b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"]
    xref = sum(len(chunk) for chunk in chunks)
    chunks.append(b"xref\n0 2\n0000000000 65535 f \n0000000009 00000 n \n")
    chunks.append(b"trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return b"".join(chunks)


agent = load_agent()
req(agent.AGENT_VERSION == "2.7.64", agent.AGENT_VERSION)
req('const MIN_AGENT_VERSION = "2.7.64";' in BRIDGE, "old native agent is not rejected")

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    benign = root / "benign.pdf"
    # minimal_pdf ends with one LF; 16 comment bytes therefore reproduce the
    # field diagnostic's 17 bytes after %%EOF.
    suffix = b"%Scanner Model 4"
    req(len(suffix) == 16, "field-sized suffix fixture changed")
    benign.write_bytes(minimal_pdf() + suffix)
    ok, code, _detail, diag = agent.validate_pdf_structure(benign)
    req(ok and code is None, (code, diag))
    req(diag.get("pdfTrailingBytes") == 17, diag)
    req(diag.get("pdfTrailingKind") == "pdf_producer_comment_suffix", diag)

    for name, tail in {
        "binary": b"ARBITRARY_TRAILING_PAYLOAD",
        "nested": b"%PDF-2.0",
        "active": b"%javascript alert",
        "html": b"<html>login</html>",
    }.items():
        bad = root / f"{name}.pdf"
        bad.write_bytes(minimal_pdf() + tail)
        ok, code, _detail, _diag = agent.validate_pdf_structure(bad)
        req(not ok and code == "PDF_TRAILING_DATA", f"{name} tail accepted")

req("async function autoRevalidatePdfTrailingReviews" in MANAGER, "startup recovery missing")
req('document.errorCode === "PDF_TRAILING_DATA" && document.quarantinePath' in MANAGER,
    "startup recovery is not scoped to quarantined PDF trailing records")
req('"PDF_COMMENT_TAIL_AUTO_RECOVERY"' in MANAGER, "recovery diagnostic missing")
req("pdfTrailingCoreHex" in MANAGER, "bounded suffix evidence missing")
req("pdf_producer_comment_suffix_v1" in AGENT_PATH.read_text(encoding="utf-8"), "agent capability missing")
req("RC15.9.37 QUEUE-RECOVERY" in MANIFEST.get("version_name", ""), "release identity mismatch")

print("RC15.9.33 PDF comment tail recovery regression: PASS")
