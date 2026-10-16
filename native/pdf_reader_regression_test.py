"""Full PDF reader (vendored pypdf) with the stream reader as fallback.

All PDFs here are synthetic and built byte by byte; no case data is used.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "native"))

import ai_local_index as idx  # noqa: E402

LINES = [
    "Davacı vekilinin tebliğ talebinin kabulüne, duruşmanın 20.10.2026 tarihine ertelenmesine karar verildi.",
    "Gereği düşünüldü: Şişli Belediyesine müzekkere yazılmasına, İstanbul Tapu Müdürlüğü cevabının beklenmesine.",
    "Tarafların delil listelerini iki hafta içinde sunmalarına, aksi halde delil ibraz etmemiş sayılmalarına.",
]
PLAIN = [
    "Davaci vekilinin talebinin kabulune, durusmanin ertelenmesine karar verildi.",
    "Geregi dusunuldu: Belediyeye muzekkere yazilmasina, tapu cevabinin beklenmesine.",
]


def _stream(data: bytes) -> bytes:
    return b"<< /Length %d >>\nstream\n" % len(data) + data + b"\nendstream"


def _pdf(bodies) -> bytes:
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(bodies, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(bodies) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(bodies) + 1, xref)
    return bytes(out)


def _page(font: bytes, content: bytes, extra=()):
    return _pdf([
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        font,
        _stream(content),
        *extra,
    ])


def tounicode_pdf(lines) -> bytes:
    """Glyph codes 1..N mapped to Turkish text only through a ToUnicode CMap,
    the way UYAP's e-signed PDFs embed subset fonts."""
    chars = sorted({ch for line in lines for ch in line})
    code = {ch: number for number, ch in enumerate(chars, 1)}
    assert len(code) < 255
    pairs = [f"<{code[ch]:02X}> <{ord(ch):04X}>" for ch in chars]
    blocks = []
    for start in range(0, len(pairs), 100):
        part = pairs[start:start + 100]
        blocks.append(f"{len(part)} beginbfchar\n" + "\n".join(part) + "\nendbfchar")
    cmap = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
            "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
            "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
            "1 begincodespacerange\n<00> <FF>\nendcodespacerange\n" + "\n".join(blocks)
            + "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n").encode("ascii")
    ops = ["BT", "/F1 11 Tf", "40 780 Td", "16 TL"]
    ops += ["<" + "".join(f"{code[ch]:02X}" for ch in line) + "> Tj T*" for line in lines]
    ops.append("ET")
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /ToUnicode 6 0 R >>"
    return _page(font, "\n".join(ops).encode("ascii"), [_stream(cmap)])


def literal_pdf(lines) -> bytes:
    ops = ["BT", "/F1 11 Tf", "40 780 Td", "16 TL"] + [f"({line}) Tj T*" for line in lines] + ["ET"]
    font = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    return _page(font, "\n".join(ops).encode("ascii"))


def _write(folder: Path, name: str, data: bytes) -> Path:
    path = folder / name
    path.write_bytes(data)
    return path


def test_full_reader_reads_tounicode_text_the_stream_reader_cannot():
    with tempfile.TemporaryDirectory() as folder:
        path = _write(Path(folder), "ara-karar.pdf", tounicode_pdf(LINES))
        result = idx.extract_text(path)
        assert result["method"] == idx.PDF_READER_METHOD, result
        assert result["quality"] == "accepted", result
        text = " ".join(result["text"].split())
        for line in LINES:
            assert line in text, (line, text)
        # The old stream reader alone sees only glyph codes here.
        try:
            stream = idx._pdf_text(path)
        except ValueError:
            stream = ""
        assert "tebliğ" not in stream and "Şişli" not in stream


def test_stream_reader_is_the_fallback_when_the_full_reader_is_off():
    with tempfile.TemporaryDirectory() as folder:
        path = _write(Path(folder), "zapt.pdf", literal_pdf(PLAIN))
        assert idx.extract_text(path)["method"] == idx.PDF_READER_METHOD
        saved = idx._pdf_reader_stuck
        idx._pdf_reader_stuck = idx.PDF_READER_STUCK_LIMIT
        try:
            result = idx.extract_text(path)
        finally:
            idx._pdf_reader_stuck = saved
        assert result["method"] == idx.PDF_EXTRACTOR_METHOD, result
        assert "Belediyeye muzekkere" in result["text"], result


def test_a_reader_that_overruns_its_budget_cannot_stall_the_index():
    import pypdf

    class Stuck:
        def __init__(self, *args, **kwargs):
            time.sleep(4.0)

    original_reader, original_budget, saved = pypdf.PdfReader, idx.PDF_TEXT_TIME_BUDGET_SECONDS, idx._pdf_reader_stuck
    pypdf.PdfReader, idx.PDF_TEXT_TIME_BUDGET_SECONDS, idx._pdf_reader_stuck = Stuck, 0.2, 0
    try:
        with tempfile.TemporaryDirectory() as folder:
            path = _write(Path(folder), "zapt.pdf", literal_pdf(PLAIN))
            started = time.monotonic()
            result = idx.extract_text(path)
            assert time.monotonic() - started < 3.5, "waited for the stuck reader"
            assert result["method"] == idx.PDF_EXTRACTOR_METHOD, result
            assert idx._pdf_reader_stuck == 1
    finally:
        pypdf.PdfReader, idx.PDF_TEXT_TIME_BUDGET_SECONDS, idx._pdf_reader_stuck = original_reader, original_budget, saved


def test_unreadable_pdf_is_still_rejected():
    garbage = _page(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
                    b"BT /F1 11 Tf 40 780 Td <0102030405060708090A> Tj ET")
    with tempfile.TemporaryDirectory() as folder:
        path = _write(Path(folder), "taranmis.pdf", garbage)
        try:
            result = idx.extract_text(path)
        except ValueError as error:
            assert str(error) == "AI_PDF_TEXT_NOT_EXTRACTABLE"
        else:
            assert result["quality"] == "rejected" and not result["text"], result


def test_old_case_index_is_offered_one_rebuild():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "UYAP_Arsiv").mkdir()
        scope = idx.archive_case_scope_id("Örnek Mahkeme", "2026/77")
        db, _fts5 = idx._index_db(root, scope)
        try:
            db.execute(
                """INSERT INTO ai_documents(source_ref,session_id,document_id_hash,file_sha256,title,document_type,
                   document_date,extraction_method,extraction_quality,text_chars,chunk_count,indexed_at)
                   VALUES('D000001',?,'h','s','Tensip Zaptı','UDF','2026-09-11','udf-zip-xml-v1','high',20,1,'now')""",
                (scope,))
            db.execute("INSERT INTO ai_chunks(source_ref,chunk_no,text) VALUES('D000001',0,'Tensip zaptı metni')")

            def coverage(reader):
                value = {"schemaVersion": 1, "totalDocuments": 1, "indexedDocuments": 1}
                if reader:
                    value["pdfReader"] = reader
                db.execute("INSERT OR REPLACE INTO ai_meta(key,value,updated_at) VALUES('coverage',?,'now')",
                           (json.dumps(value),))
                db.commit()

            coverage(None)
            old = idx.index_status(root, scope)
            coverage(idx.PDF_READER_VERSION)
            new = idx.index_status(root, scope)
        finally:
            db.close()
        assert old["ready"] and old["rebuildRecommended"] is True, old
        assert new["rebuildRecommended"] is False and new["pdfReader"] == idx.PDF_READER_VERSION, new


def test_installer_ships_the_vendored_reader():
    vendor = ROOT / "native" / "vendor"
    assert (vendor / "pypdf" / "__init__.py").is_file()
    assert list(vendor.glob("pypdf-*.dist-info/licenses/LICENSE"))
    assert idx.pdf_reader_available()
    installer = (ROOT / "native" / "portable_installer.ps1").read_text(encoding="utf-8-sig")
    assert "$AiVendorSource = Join-Path $PSScriptRoot 'vendor'" in installer
    assert "Copy-Item -LiteralPath $AiVendorSource -Destination $vendorTarget -Recurse -Force" in installer
    assert "pypdf\\__init__.py" in installer


if __name__ == "__main__":
    test_full_reader_reads_tounicode_text_the_stream_reader_cannot()
    test_stream_reader_is_the_fallback_when_the_full_reader_is_off()
    test_a_reader_that_overruns_its_budget_cannot_stall_the_index()
    test_unreadable_pdf_is_still_rejected()
    test_old_case_index_is_offered_one_rebuild()
    test_installer_ships_the_vendored_reader()
    print("PDF reader regression: PASS")
