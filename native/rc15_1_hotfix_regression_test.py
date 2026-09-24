from pathlib import Path
import importlib.util
import tempfile

ROOT = Path(__file__).resolve().parent.parent
AGENT = ROOT / "native" / "uyap_archive_agent.py"
DM = ROOT / "background" / "download-manager.js"


def load_agent():
    spec = importlib.util.spec_from_file_location("uyap_agent_rc151", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def minimal_pdf():
    # Minimal classic-xref PDF with startxref dynamically resolved.
    chunks = [b"%PDF-1.3\n", b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"]
    xref = sum(len(x) for x in chunks)
    chunks.append(b"xref\n0 2\n0000000000 65535 f \n0000000009 00000 n \n")
    chunks.append(b"trailer\n<< /Size 2 /Root 1 0 R >>\nstartxref\n" + str(xref).encode() + b"\n%%EOF\n")
    return b"".join(chunks)


def main():
    agent = load_agent()
    assert tuple(map(int, agent.AGENT_VERSION.split("."))) >= (2, 7, 34)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        p = root / "avision.pdf"
        p.write_bytes(minimal_pdf() + b"\r%Avision")
        ok, code, detail, diag = agent.validate_pdf_structure(p)
        assert ok and code is None, (code, detail, diag)
        assert diag.get("pdfTrailingKind") == "uyap_avision_vendor_comment_suffix", diag

        q = root / "evil.pdf"
        q.write_bytes(minimal_pdf() + b"ARBITRARY_TRAILING_PAYLOAD")
        ok, code, *_ = agent.validate_pdf_structure(q)
        assert not ok and code == "PDF_TRAILING_DATA"

    js = DM.read_text(encoding="utf-8")
    assert "activeBatchOnly: Boolean(reboundSession?.largeCaseMode)" in js
    assert "const target = Math.max(now + 20" in js
    print("RC15.1 hotfix regression PASS")


if __name__ == "__main__":
    main()
