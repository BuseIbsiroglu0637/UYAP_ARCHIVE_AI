#!/usr/bin/env python3
"""Create a synthetic, non-personal ZIP that mimics the real archive DB schema."""
import io
import os
import sqlite3
import tempfile
import zipfile
import hashlib
from pathlib import Path

def main():
    destination = Path(tempfile.gettempdir()) / "HukukArsivimMixedArchiveDemo.zip"
    # A structurally valid one-page PDF that Android PdfRenderer can display.
    stream = b"BT /F1 18 Tf 40 750 Td (SYNTHETIC UYAP DEMO) Tj ET\n"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() +
        b" >>\nstream\n" + stream + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for index, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += str(index).encode() + b" 0 obj\n" + body + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets:
        pdf += (str(offset).zfill(10) + " 00000 n \n").encode()
    pdf += b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n"
    pdf += str(xref).encode() + b"\n%%EOF\n"
    udf_buffer = io.BytesIO()
    with zipfile.ZipFile(udf_buffer, "w") as inner:
        inner.writestr("content.xml", "<template format_id='2'><content>TEST</content></template>")
    files = [
        ("sample-1", ".pdf", pdf),
        ("sample-2", ".udf", udf_buffer.getvalue()),
        ("sample-3", ".tif", b"II*\x00\x08\x00\x00\x00\x00\x00\x00\x00"),
    ]
    db = sqlite3.connect(":memory:")
    try:
        db.execute("""CREATE TABLE documents (
            document_id TEXT, source_system TEXT, source_context TEXT,
            institution_name TEXT, court_name TEXT, source_document_id TEXT,
            source_identity_hash TEXT, source_fingerprint TEXT,
            case_id TEXT, folder_names TEXT, document_date TEXT,
            display_name TEXT, uyap_path TEXT, target_path TEXT,
            local_path TEXT, extension TEXT, size INTEGER,
            sha256 TEXT, verified_at TEXT, status TEXT,
            archive_part TEXT, archive_part_index INTEGER
        )""")
        for doc_id, extension, payload in files:
            name = "synthetic_" + doc_id + extension
            path = "/archived/on/another/pc/" + name
            db.execute(
                """INSERT INTO documents VALUES (
                    ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )""",
                (doc_id, "UYAP", "", "Test Mahkemesi", "Test Mahkemesi",
                 doc_id, hashlib.sha256(doc_id.encode()).hexdigest(), "",
                 "2026/100", "", "", name, "", path, path, extension,
                 len(payload), hashlib.sha256(payload).hexdigest(),
                 "2026-10-22", "VERIFIED", "TEST", 1)
            )
        db.commit()
        db_data = db.serialize()
    finally:
        db.close()
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as out:
        out.writestr("Synthetic/UYAP_Arsiv/_archive.db", db_data)
        for doc_id, extension, payload in files:
            out.writestr(
                "Synthetic/UYAP_Arsiv/synthetic_" + doc_id + extension,
                payload
            )
    print("SYNTHETIC ZIP:", destination)
    print("3 fake files: 1 PDF, 1 UDF, 1 TIF; zero personal data")

if __name__ == "__main__":
    main()
