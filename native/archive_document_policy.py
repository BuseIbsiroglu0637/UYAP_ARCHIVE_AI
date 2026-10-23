"""Central archive-artifact exclusion policy.

Do not mistake an archive finalization manifest/checksum for a court document.
This is a strict, best-effort filter, NOT proof that a document is a valid UYAP
source: DB VERIFIED + contained path + exact bytes/SHA are required separately.
"""
from pathlib import PurePosixPath
import re

COURT_FORMATS = {".pdf": "PDF", ".udf": "UDF", ".tif": "TIF", ".tiff": "TIF"}

_INTERNAL_NAMES = {
    "_archive.db", "_manifest.json", "_parts.json", "_verification.csv",
    "_checksums.sha256", "_archive-report.html",
    "_archive.db-wal", "_archive.db-shm",
}
_INTERNAL_STEMS = {
    "_archive", "_manifest", "_parts", "_verification",
    "_checksums", "_archive-report",
}
_INTERNAL_FOLDERS = {"_final", "_incoming", "_unmatched", "_invalid"}


def normalized_file_parts(path: str) -> tuple[str, ...]:
    """Treat both POSIX and Windows separators as separators for classification."""
    raw = str(path or "").replace("\\", "/")
    return tuple(p.casefold() for p in raw.split("/") if p not in ("", "."))


def archive_kind(extension: str, local_path: str, display_name: str = "") -> str:
    """Return DOCUMENT_PDF/UDF/TIF, INTERNAL, or UNSUPPORTED."""
    parts = normalized_file_parts(local_path)
    filename = parts[-1] if parts else ""
    display = str(display_name or "").strip().casefold()
    for name in (filename, display):
        stem = name.rsplit(".", 1)[0] if "." in name else name
        if (name in _INTERNAL_NAMES or stem in _INTERNAL_STEMS or
                re.fullmatch(r"archive[-_]part[-_]\d+", stem) or
                name.startswith(("final_paket_", "final-paket-"))):
            return "INTERNAL"
    if any(p in _INTERNAL_FOLDERS or p.startswith(("final_paket_", "final-paket-"))
           for p in parts[:-1]):
        return "INTERNAL"
    fmt = COURT_FORMATS.get(str(extension or "").lower())
    return "DOCUMENT_" + fmt if fmt else "UNSUPPORTED"
