"""Regression gate for PDF + UDF validation.

This is intentionally dependency-free and uses synthetic fixtures so future
validator changes cannot fix one UYAP format while silently breaking the other.
"""
from __future__ import annotations

import hashlib
import importlib.util
import shutil
import struct
import tempfile
import zlib
import zipfile
from pathlib import Path

AGENT_PATH = Path(__file__).with_name("uyap_archive_agent.py")
spec = importlib.util.spec_from_file_location("uyap_archive_agent_test", AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(agent)


def raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(level=6, wbits=-15)
    return compressor.compress(data) + compressor.flush()


def streaming_member(name: str, data: bytes) -> bytes:
    raw_name = name.encode("utf-8")
    compressed = raw_deflate(data)
    crc = zlib.crc32(data) & 0xFFFFFFFF
    flags = 0x0800 | 0x0008
    header = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        flags,
        8,
        0,
        0,
        0,
        0,
        0,
        len(raw_name),
        0,
    )
    descriptor = struct.pack("<IIII", 0x08074B50, crc, len(compressed), len(data))
    return header + raw_name + compressed + descriptor


def minimal_pdf() -> bytes:
    header = b"%PDF-1.4\n"
    obj = b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
    xref_offset = len(header) + len(obj)
    xref = (
        b"xref\n0 2\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"trailer\n<< /Size 2 /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
    )
    return header + obj + xref


def assert_true(value, message: str):
    if not value:
        raise AssertionError(message)


def main() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        # Standard central-directory UDF.
        standard_udf = root / "standard.udf"
        with zipfile.ZipFile(standard_udf, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("content.xml", '<template format_id="1.8"><content>Test</content><properties/><elements/><styles/></template>')
            archive.writestr("sign.sgn", b"signature")
        profile = agent.detect_content_profile(standard_udf)
        assert_true(profile["extension"] == ".udf", f"standard UDF misclassified: {profile}")
        valid, error, _detail, mode = agent._validate_udf_package(standard_udf)
        assert_true(valid and not error and mode == "zip_central_directory", "standard UDF validation failed")

        # UDF: reverse member order proves classification does not depend on the
        # first local ZIP entry being content.xml.
        stream_udf = root / "stream.udf"
        stream_udf.write_bytes(
            streaming_member("sign.sgn", b"signature")
            + streaming_member("content.xml", b'<template format_id="1.8"><content>Test</content><properties/><elements/><styles/></template>')
        )
        profile = agent.detect_content_profile(stream_udf)
        assert_true(profile["extension"] == ".udf", f"stream UDF misclassified: {profile}")
        valid, error, _detail, mode = agent._validate_udf_package(stream_udf)
        assert_true(valid and not error and mode == "uyap_streaming_udf_v1", "stream UDF validation failed")

        # Raw XML/template UDF used by some Kurum UYAP records.
        raw_xml_udf = root / "raw-template.udf"
        raw_xml_udf.write_text(
            """<?xml version="1.0" encoding="UTF-8"?>
<template format_id="1.7">
  <content><![CDATA[Test]]></content>
  <properties><pageFormat mediaSizeName="1"/></properties>
  <elements><paragraph><content startOffset="0" length="4"/></paragraph></elements>
  <styles><style name="default"/></styles>
  <data><field>value</field></data>
</template>
""",
            encoding="utf-8",
        )
        profile = agent.detect_content_profile(raw_xml_udf)
        assert_true(profile["extension"] == ".udf", f"raw XML UDF misclassified: {profile}")
        valid, error, _detail, mode = agent._validate_udf_package(raw_xml_udf)
        assert_true(valid and not error and mode == "uyap_raw_xml_template_udf_v1", "raw XML UDF validation failed")

        # XML fail-closed controls: arbitrary XML and DTD/entity-bearing template
        # content must not be accepted merely because the extension says .udf.
        arbitrary_xml = root / "arbitrary.udf"
        arbitrary_xml.write_text("<root><content>x</content></root>", encoding="utf-8")
        valid, error, _detail, _mode = agent._validate_udf_package(arbitrary_xml)
        assert_true(not valid and error, "arbitrary XML was accepted as UDF")

        entity_xml = root / "entity.udf"
        entity_xml.write_text(
            '<?xml version="1.0"?><!DOCTYPE template [<!ENTITY x "y">]><template format_id="1.7"><content>&x;</content><elements/><styles/><data/></template>',
            encoding="utf-8",
        )
        valid, error, _detail, _mode = agent._validate_udf_package(entity_xml)
        assert_true(not valid and error == "INVALID_UDF_XML", "DTD/entity raw UDF was accepted")

        # UDF fail-closed control.
        bad_udf = root / "bad.udf"
        bad_udf.write_bytes(stream_udf.read_bytes()[:80])
        valid, error, _detail, _mode = agent._validate_udf_package(bad_udf)
        assert_true(not valid and error, "corrupt UDF was accepted")

        # PDF normal + known Belgenet suffix both pass.
        normal_pdf = root / "normal.pdf"
        normal_pdf.write_bytes(minimal_pdf())
        valid, error, _detail, diag = agent.validate_pdf_structure(normal_pdf)
        assert_true(valid and not error and diag.get("pdfTrailingKind") == "whitespace", "normal PDF failed")

        belgenet_pdf = root / "belgenet.pdf"
        belgenet_suffix = (
            "İlgili dokümanın Belgenet sunucusu üzerinden indirilmesi esnasında "
            "hata meydana gelmiştir. Hata:Thread was being aborted."
        ).encode("utf-8")
        belgenet_pdf.write_bytes(minimal_pdf() + belgenet_suffix)
        valid, error, _detail, diag = agent.validate_pdf_structure(belgenet_pdf)
        assert_true(
            valid and not error and diag.get("pdfTrailingKind") == "uyap_belgenet_diagnostic_suffix",
            "Belgenet PDF suffix regression",
        )

        # Avision scanner PDFs observed in UYAP may append the exact vendor
        # comment %Avision after final %%EOF. Accept only that exact suffix.
        avision_pdf = root / "avision.pdf"
        avision_pdf.write_bytes(minimal_pdf() + b"\r%Avision")
        valid, error, _detail, diag = agent.validate_pdf_structure(avision_pdf)
        assert_true(
            valid and not error and diag.get("pdfTrailingKind") == "uyap_avision_vendor_comment_suffix",
            "Avision PDF suffix regression",
        )

        # UYAP scanner/report producers may append one bounded printable PDF
        # producer comment. It remains inert PDF comment syntax and the xref
        # structure must still validate.
        producer_pdf = root / "producer-comment.pdf"
        producer_pdf.write_bytes(minimal_pdf() + b"\r\n%Scanner Model 42")
        valid, error, _detail, diag = agent.validate_pdf_structure(producer_pdf)
        assert_true(
            valid and not error and diag.get("pdfTrailingKind") == "pdf_producer_comment_suffix",
            "bounded PDF producer comment suffix regression",
        )

        active_comment_pdf = root / "active-comment.pdf"
        active_comment_pdf.write_bytes(minimal_pdf() + b"%javascript alert")
        valid, error, _detail, _diag = agent.validate_pdf_structure(active_comment_pdf)
        assert_true(not valid and error == "PDF_TRAILING_DATA", "active marker PDF comment was accepted")

        # PDF fail-closed control: arbitrary payload after final EOF must fail.
        bad_pdf = root / "bad.pdf"
        bad_pdf.write_bytes(minimal_pdf() + b"ARBITRARY_TRAILING_PAYLOAD")
        valid, error, _detail, _diag = agent.validate_pdf_structure(bad_pdf)
        assert_true(not valid and error == "PDF_TRAILING_DATA", "arbitrary PDF tail was accepted")

        # ZIP-family discrimination: UDF must not swallow Office/ODF/generic ZIP.
        docx = root / "sample.docx"
        with zipfile.ZipFile(docx, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("word/document.xml", "<w:document xmlns:w='urn:test'/>")
        profile = agent.detect_content_profile(docx, ".docx")
        assert_true(profile["extension"] == ".docx", f"DOCX misclassified: {profile}")
        assert_true(agent.validate_content(docx, ".docx")[0], "DOCX validation failed")

        odt = root / "sample.odt"
        with zipfile.ZipFile(odt, "w") as archive:
            archive.writestr("mimetype", "application/vnd.oasis.opendocument.text", compress_type=zipfile.ZIP_STORED)
            archive.writestr("content.xml", "<office:document-content xmlns:office='urn:oasis:names:tc:opendocument:xmlns:office:1.0'/>")
            archive.writestr("meta.xml", "<office:document-meta xmlns:office='urn:oasis:names:tc:opendocument:xmlns:office:1.0'/>")
        profile = agent.detect_content_profile(odt, ".odt")
        assert_true(profile["extension"] == ".odt", f"ODT misclassified as UDF: {profile}")
        assert_true(agent.validate_content(odt, ".odt")[0], "ODT validation failed")

        generic_zip = root / "generic.zip"
        with zipfile.ZipFile(generic_zip, "w") as archive:
            archive.writestr("content.xml", "<root/>")
        profile = agent.detect_content_profile(generic_zip, ".zip")
        assert_true(profile["extension"] == ".zip", f"generic content.xml ZIP misclassified: {profile}")

        rtf = root / "sample.rtf"
        rtf.write_bytes(b"{\\rtf1\\ansi Test}")
        profile = agent.detect_content_profile(rtf, ".rtf")
        assert_true(profile["extension"] == ".rtf" and agent.validate_content(rtf, ".rtf")[0], "RTF detection failed")

        # Revalidation uses the same classifier. The quarantine .invalid suffix
        # must not erase the actual content type.
        session_id = "regression-session"
        session_ref = hashlib.sha256(session_id.encode()).hexdigest()[:24]
        qdir = root / "UYAP_Arsiv" / "Hatalı Dosyalar" / "Test" / session_ref
        qdir.mkdir(parents=True)
        quarantine = qdir / "stream.invalid"
        shutil.copy2(stream_udf, quarantine)
        response = agent.revalidate_quarantined({
            "downloadsRoot": str(root),
            "quarantinePath": str(quarantine),
            "sessionId": session_id,
            "targetPath": "UYAP_Arsiv/Test/case/document.udf",
            "document": {
                "documentId": "d-regression",
                "caseId": "2026/1",
                "documentTitle": "regression",
                "extension": ".udf",
                "targetPath": "UYAP_Arsiv/Test/case/document.udf",
                "attempts": 2,
            },
        })
        assert_true(response.get("valid") is True, f"quarantine UDF revalidation failed: {response}")
        assert_true(response.get("contentDetectionMode") == "uyap_streaming_udf_v1", "revalidation used wrong detector")

        raw_quarantine = qdir / "raw-template.invalid"
        shutil.copy2(raw_xml_udf, raw_quarantine)
        raw_response = agent.revalidate_quarantined({
            "downloadsRoot": str(root),
            "quarantinePath": str(raw_quarantine),
            "sessionId": session_id,
            "targetPath": "UYAP_Arsiv/Test/case/raw-document.udf",
            "document": {
                "documentId": "d-raw-regression",
                "caseId": "2026/2",
                "documentTitle": "raw regression",
                "extension": ".udf",
                "targetPath": "UYAP_Arsiv/Test/case/raw-document.udf",
                "attempts": 2,
            },
        })
        assert_true(raw_response.get("valid") is True, f"raw XML quarantine UDF revalidation failed: {raw_response}")
        assert_true(
            raw_response.get("contentDetectionMode") == "uyap_raw_xml_template_udf_v1",
            "raw XML revalidation used wrong detector",
        )

        # HTML quarantine regression: UYAP/Chrome can stage a byte-detected HTML
        # response with a .bin/.tmp/no-suffix filename. New quarantines must use
        # .html, while legacy .invalid HTML must remain safely discardable after
        # content-signature verification.
        html_session = "html-discard-regression-session"
        html_ref = hashlib.sha256(html_session.encode()).hexdigest()[:24]
        staged_html = root / "uyap_download.bin"
        staged_html.write_bytes(b"<!doctype html><html><body>UYAP HTML response</body></html>")
        quarantined_html = agent.quarantine(staged_html, root, {"sessionId": html_session}, "HTML_RESPONSE")
        assert_true(quarantined_html.suffix.lower() == ".html", f"HTML quarantine suffix regression: {quarantined_html}")
        assert_true(quarantined_html.exists(), "new HTML quarantine was not persisted")

        legacy_dir = root / "UYAP_Arsiv" / "Hatalı Dosyalar" / "UYAP_HTML_Yanitlari" / html_ref
        legacy_dir.mkdir(parents=True, exist_ok=True)
        legacy_html = legacy_dir / "legacy_HTML_RESPONSE.invalid"
        legacy_html.write_bytes(b"<html><head><title>UYAP</title></head><body>legacy HTML</body></html>")
        legacy_doc = {
            "documentId": "legacy-html-doc",
            "caseId": "2026/HTML",
            "documentTitle": "legacy html",
            "folderNames": [],
            "sourceFingerprint": "legacy-html-fingerprint",
            "attempts": 2,
        }
        conn = agent.database(root)
        try:
            agent.record_attempt(
                conn,
                {"sessionId": html_session, "document": legacy_doc},
                "INVALID_CONTENT",
                "HTML_RESPONSE",
                "legacy html quarantine",
                local_path=str(legacy_html),
                size=legacy_html.stat().st_size,
                sha256=agent.sha256_file(legacy_html),
            )
        finally:
            conn.close()
        discard = agent.discard_review_html({
            "downloadsRoot": str(root),
            "sessionId": html_session,
            "quarantinePath": str(legacy_html),
            "document": legacy_doc,
        })
        assert_true(discard.get("ok") is True and discard.get("discarded") is True, f"legacy HTML discard failed: {discard}")
        assert_true(not legacy_html.exists(), "legacy .invalid HTML was not physically deleted")
        conn = agent.database(root)
        try:
            doc_row = conn.execute(
                "SELECT 1 FROM documents WHERE session_id=? AND document_id=?",
                (html_session, legacy_doc["documentId"]),
            ).fetchone()
            audit_row = conn.execute(
                "SELECT error_code FROM download_attempts WHERE session_id=? AND document_id=? ORDER BY id DESC LIMIT 1",
                (html_session, legacy_doc["documentId"]),
            ).fetchone()
        finally:
            conn.close()
        assert_true(doc_row is None, "discarded HTML remained in physical documents registry")
        assert_true(audit_row and audit_row[0] == "USER_CONFIRMED_HTML_RESPONSE", "HTML discard audit record missing")

        fake_html = legacy_dir / "not-html.invalid"
        fake_html.write_bytes(minimal_pdf())
        rejected = False
        try:
            agent.discard_review_html({
                "downloadsRoot": str(root),
                "sessionId": html_session,
                "quarantinePath": str(fake_html),
                "document": {"documentId": "not-html", "attempts": 1},
            })
        except ValueError:
            rejected = True
        assert_true(rejected and fake_html.exists(), "non-HTML .invalid quarantine bypassed discard safety")

    print("FORMAT MATRIX REGRESSION: PASS")


if __name__ == "__main__":
    main()
