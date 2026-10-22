#!/usr/bin/env python3
"""Create a synthetic on-disk UYAP_Arsiv DB and PDFs/UDF/TIF, no real data."""
from __future__ import annotations
import argparse
import sqlite3
import tempfile
import zipfile
from pathlib import Path

import create_mobile_archive_demo


def create(output: str) -> Path:
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
    with sqlite3.connect(archive_root / "_archive.db") as db:
        for name in names:
            db.execute(
                "UPDATE documents SET local_path=? WHERE display_name=?",
                (str((archive_root / name).resolve(strict=True)), name),
            )
        db.commit()
    print("SYNTHETIC LIVE ROOT:", archive_root)
    print("3 fake VERIFIED documents, refreshable without ZIP; no personal data")
    return archive_root


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(
        Path(tempfile.gettempdir()) / "HukukArsivimLiveDemo"
    ))
    args = parser.parse_args()
    create(args.output)
