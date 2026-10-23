#!/usr/bin/env python3
"""Create a synthetic on-disk UYAP_Arsiv DB and PDFs/UDF/TIF, no real data."""
from __future__ import annotations
from contextlib import closing
import argparse
import sqlite3
import io
import hashlib
import tempfile
import zipfile
from pathlib import Path

import create_mobile_archive_demo


def create(output: str, include_zip: bool = False) -> Path:
    root = Path(output).expanduser().resolve()
    archive_root = root if root.name == "UYAP_Arsiv" else root / "UYAP_Arsiv"
    if archive_root.exists() and any(archive_root.iterdir()):
        raise ValueError("Target directory is not empty; existing archive left untouched")
    archive_root.mkdir(parents=True, exist_ok=True)
    create_mobile_archive_demo.main()
    generated = Path(tempfile.gettempdir()) / "HukukArsivimMixedArchiveDemo.zip"
    with zipfile.ZipFile(generated) as z:
        archive_db = next(x for x in z.namelist() if x.endswith("/_archive.db"))
        (archive_root / "_archive.db").write_bytes(z.read(archive_db))
        names = ["synthetic_sample-1.pdf", "synthetic_sample-2.udf", "synthetic_sample-3.tif"]
        for name in names:
            item = next(x for x in z.namelist() if x.endswith("/" + name))
            (archive_root / name).write_bytes(z.read(item))
    with closing(sqlite3.connect(archive_root / "_archive.db")) as db:
        for name in names:
            db.execute(
                "UPDATE documents SET local_path=? WHERE display_name=?",
                (str((archive_root / name).resolve(strict=True)), name),
            )
        if include_zip:
            # Generated demo only; never modify an existing/real archive.
            nested_buffer = io.BytesIO()
            with zipfile.ZipFile(nested_buffer, "w", zipfile.ZIP_STORED) as inner:
                inner.writestr("synthetic_note.txt", b"FAKE DOCUMENT - ZIP IMPORT ACCEPTANCE TEST")
            blob = nested_buffer.getvalue()
            original = archive_root / "synthetic_sample-4.zip"
            original.write_bytes(blob)
            db.execute(
                """INSERT INTO documents SELECT
                    'sample-4', source_system, source_context,
                    institution_name, court_name, 'sample-4',
                    'synthetic-identity-zip', source_fingerprint,
                    case_id, folder_names, document_date,
                    ?, uyap_path, target_path,
                    ?, '.zip', ?, ?, verified_at, 'VERIFIED',
                    archive_part, archive_part_index FROM documents
                    WHERE document_id='sample-1'""",
                (original.name, str(original.resolve(strict=True)),
                 len(blob), hashlib.sha256(blob).hexdigest())
            )
        db.commit()
    # Marker protects against accidentally pointing the DEBUG bridge to a real archive.
    # It is NOT a cryptographic proof of origin.
    (archive_root / ".hukukarsivim-synthetic-only").write_text(
        "SYNTHETIC_TEST_FIXTURE_V1\n", encoding="ascii"
    )
    print("SYNTHETIC LIVE ROOT:", archive_root)
    print(("4" if include_zip else "3") + " synthetic VERIFIED documents, no personal data")
    return archive_root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(
        Path(tempfile.gettempdir()) / "HukukArsivimLiveDemo"
    ))
    parser.add_argument("--include-zip", action="store_true",
                        help="Add a synthetic ZIP evrak for v0.15 Android test")
    args = parser.parse_args()
    create(args.output, include_zip=args.include_zip)
