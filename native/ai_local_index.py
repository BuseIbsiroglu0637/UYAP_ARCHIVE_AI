from __future__ import annotations

"""Local-only text extraction and lexical retrieval for UYAP Archive AI."""

import hashlib
from html.parser import HTMLParser
import html
from pathlib import Path
import re
import sqlite3
import time
import xml.etree.ElementTree as ET
import json
import zipfile
import zlib

from ai_case_facts import (
    BODY_ROLE_RULES,
    body_document_role,
    extract_case_facts,
    format_date as format_fact_date,
    is_attachment_title,
    parse_date as parse_fact_date,
    title_document_role,
    tr_lower,
)

AI_INDEX_SCHEMA_VERSION = 1
AI_INDEX_DIR = "_ai"
AI_INDEX_FILE = "index-v1.sqlite"
MAX_TEXT_FILE_BYTES = 64 * 1024 * 1024
MAX_ZIP_MEMBER_BYTES = 32 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 128 * 1024 * 1024
DEFAULT_CHUNK_CHARS = 3200
DEFAULT_OVERLAP_CHARS = 400
MAX_CHUNKS_PER_DOCUMENT = 500
MAX_RESULTS = 20
PDF_EXTRACTOR_METHOD = "pdf-content-stream-v5-mixed-script-gated"
XLSX_EXTRACTOR_METHOD = "xlsx-semantic-v2-noise-gated"
PDF_MIN_QUALITY_SCORE = 0.72

ZIP_XML_TYPES = {".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp"}
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff"}


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"}:
            self.skip += 1
        elif tag in {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript"} and self.skip:
            self.skip -= 1
        elif tag in {"p", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def _clean(value):
    value = str(value or "").replace("\x00", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\r\n?", "\n", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _decode(raw):
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1254", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_limited(path):
    size = path.stat().st_size
    if size > MAX_TEXT_FILE_BYTES:
        raise ValueError("AI_TEXT_FILE_TOO_LARGE")
    return path.read_bytes()


def assess_text_quality(text):
    """Return a conservative language-agnostic gate for extracted legal text.

    The local PDF stream fallback cannot resolve every embedded font/CMap.  In
    those cases arbitrary CID bytes can look like CJK/private-use characters.
    Reject suspicious text rather than sending corrupt evidence to retrieval or
    a future model provider.
    """
    value = _clean(text)
    if not value:
        return {"accepted": False, "score": 0.0, "reason": "empty", "metrics": {"length": 0}}

    chars = [ch for ch in value if not ch.isspace()]
    total = max(1, len(chars))
    printable = sum(1 for ch in chars if ch.isprintable())
    replacements = value.count("\ufffd")
    private_use = sum(1 for ch in chars if 0xE000 <= ord(ch) <= 0xF8FF)
    cjk_hangul = sum(
        1 for ch in chars
        if 0x3400 <= ord(ch) <= 0x9FFF
        or 0xAC00 <= ord(ch) <= 0xD7AF
        or 0xF900 <= ord(ch) <= 0xFAFF
    )
    cjk_runs = re.findall(r"[\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff]{2,}", value)
    latin_letters = sum(
        1 for ch in chars
        if ("A" <= ch <= "Z") or ("a" <= ch <= "z")
        or ch in "ÇĞİÖŞÜçğıöşüÂâÎîÛû"
    )
    letters = sum(1 for ch in chars if ch.isalpha())
    digits = sum(1 for ch in chars if ch.isdigit())
    word_tokens = re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]{2,}", value)
    raw_tokens = re.findall(r"\S+", value)
    single_alnum_tokens = [
        token for token in raw_tokens
        if len(re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]", "", token)) == 1
    ]
    single_token_ratio = len(single_alnum_tokens) / max(1, len(raw_tokens))
    spaced_letter_runs = len(re.findall(
        r"(?:\b[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]\s+){4,}[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]\b",
        value
    ))
    visible_ratio = printable / total
    replacement_ratio = replacements / total
    private_ratio = private_use / total
    cjk_ratio = cjk_hangul / total
    mixed_script_ratio = cjk_hangul / max(1, cjk_hangul + latin_letters)
    latin_ratio = latin_letters / max(1, letters)
    signal_ratio = (letters + digits) / total
    token_density = min(1.0, len(word_tokens) / max(1.0, len(value) / 18.0))

    score = 1.0
    score -= min(0.55, replacement_ratio * 12.0)
    score -= min(0.45, private_ratio * 10.0)
    score -= min(0.50, cjk_ratio * 2.5)
    if cjk_runs and latin_letters >= 20:
        score -= min(0.45, 0.18 + mixed_script_ratio * 1.8)
    if letters >= 24 and latin_ratio < 0.55:
        score -= min(0.35, (0.55 - latin_ratio) * 0.8)
    if visible_ratio < 0.98:
        score -= min(0.30, (0.98 - visible_ratio) * 3.0)
    if signal_ratio < 0.45:
        score -= min(0.25, (0.45 - signal_ratio) * 0.8)
    if len(value) >= 120 and token_density < 0.18:
        score -= min(0.25, (0.18 - token_density) * 1.2)
    if len(raw_tokens) >= 20 and single_token_ratio > 0.14:
        score -= min(0.35, (single_token_ratio - 0.16) * 1.8)
    if spaced_letter_runs:
        score -= min(0.35, spaced_letter_runs * 0.18)
    score = max(0.0, min(1.0, score))

    reason = None
    if replacement_ratio > 0.015:
        reason = "replacement_characters"
    elif private_ratio > 0.01:
        reason = "private_use_glyphs"
    elif cjk_runs and latin_letters >= 20 and (cjk_hangul >= 3 or mixed_script_ratio >= 0.015):
        reason = "mixed_script_mojibake"
    elif cjk_ratio > 0.04 and latin_ratio < 0.85:
        reason = "suspicious_cid_glyphs"
    elif len(raw_tokens) >= 20 and single_token_ratio > 0.18:
        reason = "fragmented_glyph_spacing"
    elif spaced_letter_runs >= 2:
        reason = "fragmented_glyph_spacing"
    elif visible_ratio < 0.92:
        reason = "non_printable_text"
    elif score < PDF_MIN_QUALITY_SCORE:
        reason = "low_quality_score"

    return {
        "accepted": reason is None,
        "score": round(score, 4),
        "reason": reason,
        "metrics": {
            "length": len(value),
            "visibleRatio": round(visible_ratio, 4),
            "replacementRatio": round(replacement_ratio, 4),
            "privateUseRatio": round(private_ratio, 4),
            "cjkHangulRatio": round(cjk_ratio, 4),
            "mixedScriptRatio": round(mixed_script_ratio, 4),
            "cjkRuns": len(cjk_runs),
            "latinLetterRatio": round(latin_ratio, 4),
            "signalRatio": round(signal_ratio, 4),
            "tokenDensity": round(token_density, 4),
            "singleTokenRatio": round(single_token_ratio, 4),
            "spacedLetterRuns": spaced_letter_runs,
        },
    }


def _html_text(raw):
    parser = _HTMLText()
    parser.feed(_decode(raw))
    return _clean("".join(parser.parts))


def _xml_text(raw):
    try:
        root = ET.fromstring(raw)
        return _clean(" ".join(part for part in root.itertext() if part))
    except ET.ParseError:
        return _clean(html.unescape(re.sub(r"<[^>]+>", " ", _decode(raw))))


def _rtf_text(raw):
    text = _decode(raw)
    text = re.sub(r"\\'[0-9a-fA-F]{2}", " ", text)
    text = re.sub(r"\\u-?\d+\??", " ", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", text)
    return _clean(text.replace("{", " ").replace("}", " ").replace("\\", " "))


def _xml_local_name(element):
    return str(element.tag or "").rsplit("}", 1)[-1]


def _xlsx_text_nodes(element):
    return [
        str(node.text or "")
        for node in element.iter()
        if _xml_local_name(node) == "t" and node.text
    ]


def _xlsx_numeric_value(value):
    return bool(re.fullmatch(r"[-+]?\d+(?:[.,]\d+)?", str(value or "").strip()))


def _xlsx_text(path):
    """Extract semantic spreadsheet rows without indexing raw OOXML numeric noise.

    Excel worksheets store shared-string references and cached numeric values in
    separate XML structures. Treating every XML text node as prose duplicates
    shared strings and turns dense numeric sheets into retrieval garbage. Resolve
    shared strings first, then retain rows that contain actual language signal.
    Numeric cells are kept only when they are contextualized by text in the same
    row, with a conservative per-row cap.
    """
    shared_strings = []
    rows = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        infos = {info.filename.replace("\\", "/"): info for info in archive.infolist() if not info.is_dir()}
        shared_info = infos.get("xl/sharedStrings.xml")
        if shared_info and shared_info.file_size <= MAX_ZIP_MEMBER_BYTES:
            total += max(0, int(shared_info.file_size))
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("AI_ZIP_UNCOMPRESSED_LIMIT")
            try:
                root = ET.fromstring(archive.read(shared_info))
                for item in root.iter():
                    if _xml_local_name(item) != "si":
                        continue
                    shared_strings.append(_clean(" ".join(_xlsx_text_nodes(item))))
            except ET.ParseError:
                shared_strings = []

        worksheet_names = sorted(
            name for name in infos
            if name.lower().startswith("xl/worksheets/") and name.lower().endswith(".xml")
        )
        for name in worksheet_names:
            info = infos[name]
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                continue
            total += max(0, int(info.file_size))
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("AI_ZIP_UNCOMPRESSED_LIMIT")
            try:
                root = ET.fromstring(archive.read(info))
            except ET.ParseError:
                continue
            for row in root.iter():
                if _xml_local_name(row) != "row":
                    continue
                values = []
                alpha_chars = 0
                numeric_seen = 0
                for cell in row:
                    if _xml_local_name(cell) != "c":
                        continue
                    cell_type = str(cell.attrib.get("t") or "").strip()
                    value = ""
                    if cell_type == "inlineStr":
                        value = _clean(" ".join(_xlsx_text_nodes(cell)))
                    else:
                        raw_value = next(
                            (str(node.text or "") for node in cell if _xml_local_name(node) == "v" and node.text is not None),
                            "",
                        ).strip()
                        if cell_type == "s" and raw_value.isdigit():
                            index = int(raw_value)
                            if 0 <= index < len(shared_strings):
                                value = shared_strings[index]
                        elif cell_type in {"str", "e"}:
                            value = _clean(raw_value)
                        else:
                            value = _clean(raw_value)
                    if not value:
                        continue
                    alpha_chars += sum(1 for ch in value if ch.isalpha())
                    is_numeric = _xlsx_numeric_value(value)
                    if is_numeric:
                        numeric_seen += 1
                        if len(value) > 32 or numeric_seen > 6:
                            continue
                    values.append(value)

                # Numeric-only rows are usually IDs, cached formulas, counters,
                # coordinates or internal exports. Without a textual label they
                # are not safe retrieval evidence.
                if alpha_chars < 3:
                    continue
                row_text = _clean(" | ".join(values))
                if row_text:
                    rows.append(row_text)
    return _clean("\n".join(rows))


def _safe_zip_xml_text(path, extension):
    prefixes = {
        ".docx": ("word/document.xml", "word/header", "word/footer", "word/footnotes.xml", "word/endnotes.xml"),
        ".pptx": ("ppt/slides/", "ppt/notesSlides/"),
        ".xlsx": ("xl/sharedStrings.xml", "xl/worksheets/"),
        ".odt": ("content.xml",),
        ".ods": ("content.xml",),
        ".odp": ("content.xml",),
        ".udf": ("",),
    }.get(extension, ("",))
    parts = []
    total = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir() or info.file_size > MAX_ZIP_MEMBER_BYTES:
                continue
            total += max(0, int(info.file_size))
            if total > MAX_ZIP_TOTAL_BYTES:
                raise ValueError("AI_ZIP_UNCOMPRESSED_LIMIT")
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in Path(name).parts:
                continue
            lowered = name.lower()
            if not lowered.endswith((".xml", ".html", ".htm", ".xhtml")):
                continue
            if prefixes != ("",) and not any(lowered == p.lower() or lowered.startswith(p.lower()) for p in prefixes):
                continue
            raw = archive.read(info)
            text = _xml_text(raw) if lowered.endswith(".xml") else _html_text(raw)
            if text:
                parts.append(text)
    return _clean("\n\n".join(parts))


def _pdf_literal(value):
    out = bytearray()
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != 92:
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(value):
            break
        esc = value[i]
        mapped = {110: 10, 114: 13, 116: 9, 98: 8, 102: 12}
        if esc in mapped:
            out.append(mapped[esc])
            i += 1
        elif esc in (40, 41, 92):
            out.append(esc)
            i += 1
        elif 48 <= esc <= 55:
            digits = bytes([esc])
            i += 1
            for _ in range(2):
                if i < len(value) and 48 <= value[i] <= 55:
                    digits += bytes([value[i]])
                    i += 1
                else:
                    break
            out.append(int(digits, 8))
        else:
            out.append(esc)
            i += 1
    if bytes(out).startswith((b"\xfe\xff", b"\xff\xfe")):
        try:
            return bytes(out).decode("utf-16")
        except UnicodeError:
            pass
    return _decode(bytes(out))


def _pdf_operator_text(block):
    values = []
    for match in re.finditer(rb"\((?:\\.|[^\\()]){1,20000}\)\s*Tj\b", block, re.S):
        raw = match.group(0)
        values.append(_pdf_literal(raw[1:raw.rfind(b")")]))
    for match in re.finditer(rb"\[(.{1,100000}?)\]\s*TJ\b", block, re.S):
        bits = []
        for literal in re.finditer(rb"\((?:\\.|[^\\()]){1,20000}\)", match.group(1), re.S):
            bits.append(_pdf_literal(literal.group(0)[1:-1]))
        if bits:
            values.append(" ".join(bits))
    return values


def _pdf_text(path):
    raw = _read_limited(path)
    values = []
    for stream in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        payload = stream.group(1)
        prefix = raw[max(0, stream.start() - 600):stream.start()]
        candidates = [payload]
        if b"/FlateDecode" in prefix:
            try:
                inflated = zlib.decompress(payload)
                if len(inflated) <= MAX_TEXT_FILE_BYTES:
                    candidates.append(inflated)
            except zlib.error:
                pass
        for candidate in candidates:
            for bt in re.finditer(rb"BT(.*?)ET", candidate, re.S):
                values.extend(_pdf_operator_text(bt.group(1)))
    text = _clean("\n".join(values))
    if not text:
        raise ValueError("AI_PDF_TEXT_NOT_EXTRACTABLE")
    return text


def extract_text(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file() or path.is_symlink():
        raise ValueError("AI_SOURCE_NOT_REGULAR_FILE")
    extension = path.suffix.lower()
    if extension == ".pdf":
        text = _pdf_text(path)
        assessment = assess_text_quality(text)
        if not assessment["accepted"]:
            return {
                "text": "",
                "method": PDF_EXTRACTOR_METHOD,
                "quality": "rejected",
                "errorCode": "AI_PDF_TEXT_LOW_QUALITY",
                "qualityScore": assessment["score"],
                "qualityReason": assessment["reason"],
                "qualityMetrics": assessment["metrics"],
            }
        return {
            "text": text,
            "method": PDF_EXTRACTOR_METHOD,
            "quality": "accepted",
            "qualityScore": assessment["score"],
            "qualityReason": None,
            "qualityMetrics": assessment["metrics"],
        }
    if extension in IMAGE_TYPES:
        return {"text": "", "method": "image-no-ocr", "quality": "unsupported", "errorCode": "AI_OCR_NOT_CONFIGURED"}
    if extension == ".udf":
        if zipfile.is_zipfile(path):
            text = _safe_zip_xml_text(path, extension)
            return {"text": text, "method": "udf-zip-xml-v1", "quality": "high" if text else "empty"}
        text = _xml_text(_read_limited(path))
        return {"text": text, "method": "udf-raw-xml-v1", "quality": "high" if text else "empty"}
    if extension == ".xlsx":
        text = _xlsx_text(path)
        return {"text": text, "method": XLSX_EXTRACTOR_METHOD, "quality": "high" if text else "empty"}
    if extension in ZIP_XML_TYPES:
        text = _safe_zip_xml_text(path, extension)
        return {"text": text, "method": extension[1:] + "-zip-xml-v1", "quality": "high" if text else "empty"}
    if extension in {".html", ".htm"}:
        text = _html_text(_read_limited(path))
        return {"text": text, "method": "html-parser-v1", "quality": "high" if text else "empty"}
    if extension == ".rtf":
        text = _rtf_text(_read_limited(path))
        return {"text": text, "method": "rtf-basic-v1", "quality": "best_effort" if text else "empty"}
    if extension in {".xml", ".svg"}:
        text = _xml_text(_read_limited(path))
        return {"text": text, "method": "xml-itertext-v1", "quality": "high" if text else "empty"}
    if extension in {".txt", ".csv", ".json"}:
        text = _clean(_decode(_read_limited(path)))
        return {"text": text, "method": "plain-text-v1", "quality": "high" if text else "empty"}
    return {"text": "", "method": "unsupported", "quality": "unsupported", "errorCode": "AI_TEXT_FORMAT_UNSUPPORTED"}


def chunk_text(text, chunk_chars=DEFAULT_CHUNK_CHARS, overlap=DEFAULT_OVERLAP_CHARS):
    text = _clean(text)
    if not text:
        return []
    chunk_chars = max(800, min(int(chunk_chars or DEFAULT_CHUNK_CHARS), 8000))
    overlap = max(0, min(int(overlap or DEFAULT_OVERLAP_CHARS), chunk_chars // 3))
    chunks = []
    start = 0
    while start < len(text) and len(chunks) < MAX_CHUNKS_PER_DOCUMENT:
        end = min(len(text), start + chunk_chars)
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n\n"), window.rfind(". "), window.rfind("; "))
            if cut >= int(chunk_chars * 0.55):
                end = start + cut + 1
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _quality_gate_pdf_chunks(chunks):
    accepted = []
    rejected = 0
    reasons = {}
    scores = []
    for chunk in chunks:
        assessment = assess_text_quality(chunk)
        scores.append(float(assessment.get("score") or 0.0))
        if assessment.get("accepted") is True:
            accepted.append(chunk)
            continue
        rejected += 1
        reason = str(assessment.get("reason") or "low_quality")
        reasons[reason] = reasons.get(reason, 0) + 1
    return {
        "chunks": accepted,
        "rejectedChunks": rejected,
        "rejectedByReason": reasons,
        "minScore": round(min(scores), 4) if scores else None,
        "maxScore": round(max(scores), 4) if scores else None,
    }


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def archive_case_scope_id(institution_name, case_id):
    institution = re.sub(r"\s+", " ", str(institution_name or "").strip()).casefold()
    normalized_case = _normalize_case_id(case_id).casefold()
    if not institution or not normalized_case:
        raise ValueError("AI_ARCHIVE_CASE_IDENTITY_REQUIRED")
    digest = hashlib.sha256(f"{institution}|{normalized_case}".encode("utf-8")).hexdigest()[:24]
    return f"case:{digest}"


def _normalize_case_id(value):
    raw = re.sub(r"\s+", "", str(value or "").strip()).replace("_", "/").replace("-", "/")
    match = re.fullmatch(r"(20\d{2})/(\d{1,10})", raw)
    return f"{match.group(1)}/{int(match.group(2))}" if match else raw


def _index_path(root, scope_id=None):
    folder = root / "UYAP_Arsiv" / AI_INDEX_DIR
    folder.mkdir(parents=True, exist_ok=True)
    scope = str(scope_id or "")
    if scope.startswith("case:"):
        digest = scope.split(":", 1)[1]
        if not re.fullmatch(r"[0-9a-f]{24}", digest):
            raise ValueError("AI_ARCHIVE_CASE_SCOPE_INVALID")
        return folder / f"case-{digest}-v1.sqlite"
    return folder / AI_INDEX_FILE


def _index_db(root, scope_id=None):
    db = sqlite3.connect(_index_path(root, scope_id), timeout=30.0)
    db.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA busy_timeout=30000;
        CREATE TABLE IF NOT EXISTS ai_documents(
          source_ref TEXT PRIMARY KEY, session_id TEXT NOT NULL, document_id_hash TEXT NOT NULL,
          file_sha256 TEXT NOT NULL, title TEXT, document_type TEXT, document_date TEXT,
          extraction_method TEXT, extraction_quality TEXT, text_chars INTEGER NOT NULL DEFAULT 0,
          chunk_count INTEGER NOT NULL DEFAULT 0, indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ai_chunks(
          id INTEGER PRIMARY KEY AUTOINCREMENT, source_ref TEXT NOT NULL,
          chunk_no INTEGER NOT NULL, text TEXT NOT NULL, UNIQUE(source_ref, chunk_no)
        );
        CREATE INDEX IF NOT EXISTS idx_ai_documents_session ON ai_documents(session_id);
        CREATE INDEX IF NOT EXISTS idx_ai_chunks_source ON ai_chunks(source_ref);
        CREATE TABLE IF NOT EXISTS ai_meta(
          key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
        );
    """)
    try:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS ai_chunks_fts USING fts5(source_ref UNINDEXED, text, tokenize='unicode61')")
        fts5 = True
    except sqlite3.Error:
        fts5 = False
    db.commit()
    return db, fts5


def _archive_rows(root, session_id, bindings):
    archive_root = (root / "UYAP_Arsiv").resolve()
    database_path = archive_root / "_archive.db"
    if not database_path.is_file():
        raise ValueError("AI_ARCHIVE_DB_NOT_FOUND")
    binding_by_id = {str(row.get("documentId") or ""): str(row.get("sourceRef") or "") for row in bindings or [] if row.get("documentId") and row.get("sourceRef")}
    if not binding_by_id:
        return []
    db = sqlite3.connect(database_path, timeout=30.0)
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        placeholders = ",".join("?" for _ in binding_by_id)
        params = [session_id, *binding_by_id.keys()]
        rows = db.execute(
            f"""SELECT document_id, display_name, local_path, size, sha256, document_date
                FROM documents WHERE session_id=? AND status='VERIFIED'
                AND document_id IN ({placeholders}) ORDER BY uyap_path, display_name""",
            params,
        ).fetchall()
    finally:
        db.close()
    proven = []
    for document_id, title, local_path, expected_size, expected_sha, document_date in rows:
        try:
            path = Path(local_path or "").resolve(strict=True)
            path.relative_to(archive_root)
            if path.is_symlink() or not path.is_file():
                continue
            if path.stat().st_size != int(expected_size or -1):
                continue
            digest = _sha256_file(path)
            if digest.lower() != str(expected_sha or "").lower():
                continue
            proven.append({
                "documentId": str(document_id), "sourceRef": binding_by_id[str(document_id)],
                "title": str(title or "Adsız evrak"), "path": path, "sha256": digest, "date": document_date,
            })
        except (OSError, ValueError, TypeError):
            continue
    return proven



def _safe_archive_candidate(archive_root, local_path, target_path):
    candidates = []
    raw_target = str(target_path or "").replace("\\", "/").strip("/")
    if raw_target:
        parts = [part for part in raw_target.split("/") if part and part not in {".", ".."}]
        if parts and parts[0].casefold() == "uyap_arsiv":
            parts = parts[1:]
        if parts:
            candidates.append(archive_root.joinpath(*parts))
    if local_path:
        candidates.append(Path(str(local_path)))
    for candidate in candidates:
        try:
            resolved = candidate.expanduser().resolve(strict=True)
            resolved.relative_to(archive_root)
            if resolved.is_file() and not resolved.is_symlink():
                return resolved
        except (OSError, ValueError):
            continue
    return None


def _archive_case_metadata(archive_root, row):
    path = _safe_archive_candidate(archive_root, row.get("local_path"), row.get("target_path"))
    relative_parts = []
    if path is not None:
        try:
            relative_parts = list(path.relative_to(archive_root).parts)
        except ValueError:
            relative_parts = []
    if not relative_parts:
        target = str(row.get("target_path") or "").replace("\\", "/").strip("/")
        relative_parts = [part for part in target.split("/") if part]
        if relative_parts and relative_parts[0].casefold() == "uyap_arsiv":
            relative_parts = relative_parts[1:]
    institution = str(row.get("institution_name") or (relative_parts[0] if relative_parts else "") or "").strip()
    case_id = _normalize_case_id(row.get("case_id") or (relative_parts[1] if len(relative_parts) > 1 else ""))
    return path, institution, case_id


def inspect_archive_case(downloads_root, institution_name, case_id):
    root = Path(downloads_root).resolve(strict=True)
    archive_root = (root / "UYAP_Arsiv").resolve(strict=True)
    database_path = archive_root / "_archive.db"
    if not database_path.is_file():
        raise ValueError("AI_ARCHIVE_DB_NOT_FOUND")

    wanted_institution = re.sub(r"\s+", " ", str(institution_name or "").strip()).casefold()
    wanted_case = _normalize_case_id(case_id).casefold()
    if not wanted_institution or not wanted_case:
        raise ValueError("AI_ARCHIVE_CASE_IDENTITY_REQUIRED")

    db = sqlite3.connect(database_path, timeout=30.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        columns = {str(row[1]) for row in db.execute("PRAGMA table_info(documents)").fetchall()}
        required = {"document_id", "display_name", "local_path", "size", "sha256", "status", "updated_at"}
        if not required.issubset(columns):
            raise ValueError("AI_ARCHIVE_DB_SCHEMA_INCOMPLETE")
        optional = {
            "case_id": "NULL",
            "institution_name": "NULL",
            "target_path": "NULL",
            "document_date": "NULL",
            "uyap_path": "NULL",
            "source_system": "NULL",
        }
        select_parts = ["document_id", "display_name", "local_path", "size", "sha256", "status", "updated_at"]
        for name, fallback in optional.items():
            select_parts.append(name if name in columns else f"{fallback} AS {name}")
        rows = [
            dict(row)
            for row in db.execute(
                f"SELECT {', '.join(select_parts)} FROM documents ORDER BY updated_at DESC"
            ).fetchall()
        ]
    finally:
        db.close()

    matching = []
    for row in rows:
        path, institution, normalized_case = _archive_case_metadata(archive_root, row)
        if institution.casefold() != wanted_institution or normalized_case.casefold() != wanted_case:
            continue
        row["_resolved_path"] = path
        matching.append(row)

    latest = {}
    for row in matching:
        key = str(row.get("document_id") or "").strip()
        if not key:
            key = str(row.get("target_path") or row.get("local_path") or "")
        if key and key not in latest:
            latest[key] = row

    issues = []
    proven = []
    ordered = sorted(latest.items(), key=lambda item: (
        str(item[1].get("document_date") or ""),
        str(item[1].get("display_name") or ""),
        item[0],
    ))
    for index, (document_id, row) in enumerate(ordered):
        path = row.get("_resolved_path")
        status = str(row.get("status") or "")
        expected_sha = str(row.get("sha256") or "").strip().lower()
        try:
            expected_size = int(row.get("size") or 0)
        except (TypeError, ValueError):
            expected_size = 0

        reason = None
        digest = None
        if status != "VERIFIED":
            reason = "metadata_not_verified"
        elif path is None:
            reason = "file_missing"
        else:
            try:
                actual_size = int(path.stat().st_size)
            except OSError:
                actual_size = -1
            if expected_size > 0 and actual_size != expected_size:
                reason = "size_mismatch"
            elif not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
                reason = "sha256_missing"
            else:
                try:
                    digest = _sha256_file(path).lower()
                except OSError:
                    reason = "sha256_read_failed"
                if reason is None and digest != expected_sha:
                    reason = "sha256_mismatch"

        if reason:
            issues.append({
                "documentIdHash": hashlib.sha256(document_id.encode("utf-8")).hexdigest()[:16],
                "title": str(row.get("display_name") or "Adsız evrak")[:240],
                "reason": reason,
            })
            continue

        proven.append({
            "documentId": document_id,
            "sourceRef": f"D{index + 1:06d}",
            "title": str(row.get("display_name") or "Adsız evrak"),
            "path": path,
            "sha256": digest,
            "date": row.get("document_date"),
        })

    scope_id = archive_case_scope_id(institution_name, case_id)
    total = len(latest)
    return {
        "ok": True,
        "kind": "UYAP_AI_ARCHIVE_CASE_INSPECTION",
        "schemaVersion": 1,
        "scopeId": scope_id,
        "institutionName": str(institution_name or "").strip(),
        "caseId": _normalize_case_id(case_id),
        "totalDocuments": total,
        "verifiedPhysicalDocuments": len(proven),
        "problemDocuments": len(issues),
        "readyForAi": total > 0 and len(proven) == total,
        "issues": issues[:50],
        "_rows": proven,
    }


def build_archive_case_index(downloads_root, institution_name, case_id):
    inspection = inspect_archive_case(downloads_root, institution_name, case_id)
    scope_id = inspection["scopeId"]
    if not inspection["readyForAi"]:
        return {
            **{key: value for key, value in inspection.items() if key != "_rows"},
            "kind": "UYAP_AI_ARCHIVE_CASE_INDEX",
            "indexedDocuments": 0,
            "availableDocuments": 0,
            "availableChunks": 0,
            "errorCode": "AI_ARCHIVE_CASE_INCOMPLETE",
        }

    root = Path(downloads_root).resolve(strict=True)
    rows = list(inspection.get("_rows") or [])
    db, fts5 = _index_db(root, scope_id)
    indexed = unsupported = failed = quality_rejected = quality_rejected_chunks = 0
    quality_reasons = {}
    quality_chunk_reasons = {}
    methods = {}
    skipped = []
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def remember_skipped(item, reason):
        # Title and date only: the summary must be able to say which documents
        # it could not read, without storing any of their content.
        skipped.append({
            "sourceRef": item["sourceRef"],
            "title": str(item.get("title") or "Adsız evrak")[:240],
            "date": format_fact_date(parse_fact_date(item.get("date"))) or None,
            "extension": item["path"].suffix.lower().lstrip(".") or None,
            "reason": reason,
        })

    try:
        db.execute("DELETE FROM ai_chunks")
        if fts5:
            db.execute("DELETE FROM ai_chunks_fts")
        db.execute("DELETE FROM ai_documents")
        db.execute("DELETE FROM ai_meta")

        for item in rows:
            ref = item["sourceRef"]
            try:
                extracted = extract_text(item["path"])
                method = str(extracted.get("method") or "unknown")
                methods[method] = methods.get(method, 0) + 1
                chunks = chunk_text(extracted.get("text") or "")
                if item["path"].suffix.lower() == ".pdf" and chunks:
                    gated = _quality_gate_pdf_chunks(chunks)
                    chunks = gated["chunks"]
                    quality_rejected_chunks += int(gated["rejectedChunks"] or 0)
                    for reason, count in (gated["rejectedByReason"] or {}).items():
                        quality_chunk_reasons[reason] = quality_chunk_reasons.get(reason, 0) + int(count or 0)
                    if chunks and gated["rejectedChunks"]:
                        extracted["quality"] = "accepted_partial"
                        extracted["qualityReason"] = "chunk_quality_gate"
                if not chunks:
                    if extracted.get("errorCode") == "AI_PDF_TEXT_LOW_QUALITY" or item["path"].suffix.lower() == ".pdf":
                        quality_rejected += 1
                        reason = str(extracted.get("qualityReason") or "all_pdf_chunks_rejected")
                        quality_reasons[reason] = quality_reasons.get(reason, 0) + 1
                        remember_skipped(item, "low_quality_text")
                    else:
                        unsupported += 1
                        remember_skipped(
                            item,
                            "unsupported" if extracted.get("errorCode") == "AI_TEXT_FORMAT_UNSUPPORTED" else "no_text",
                        )
                    continue

                for chunk_no, chunk in enumerate(chunks):
                    db.execute(
                        "INSERT OR REPLACE INTO ai_chunks(source_ref,chunk_no,text) VALUES(?,?,?)",
                        (ref, chunk_no, chunk),
                    )
                    if fts5:
                        db.execute("INSERT INTO ai_chunks_fts(source_ref,text) VALUES(?,?)", (ref, chunk))
                db.execute(
                    """INSERT OR REPLACE INTO ai_documents
                       (source_ref,session_id,document_id_hash,file_sha256,title,document_type,document_date,
                        extraction_method,extraction_quality,text_chars,chunk_count,indexed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        ref,
                        scope_id,
                        hashlib.sha256(item["documentId"].encode()).hexdigest(),
                        item["sha256"],
                        item["title"],
                        item["path"].suffix.lower().lstrip(".").upper() or "DOSYA",
                        item["date"],
                        method,
                        extracted.get("quality"),
                        len(extracted.get("text") or ""),
                        len(chunks),
                        now,
                    ),
                )
                indexed += 1
            except Exception:
                failed += 1
                remember_skipped(item, "extraction_failed")

        coverage = {
            "schemaVersion": 1,
            "totalDocuments": int(inspection["totalDocuments"]),
            "indexedDocuments": indexed,
            "skipped": skipped[:500],
            "skippedCount": len(skipped),
            "builtAt": now,
        }
        db.execute(
            "INSERT OR REPLACE INTO ai_meta(key,value,updated_at) VALUES('coverage',?,?)",
            (json.dumps(coverage, ensure_ascii=False), now),
        )
        db.commit()
        available = db.execute("SELECT COUNT(*) FROM ai_documents WHERE session_id=?", (scope_id,)).fetchone()[0]
        chunks = db.execute(
            "SELECT COUNT(*) FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref WHERE d.session_id=?",
            (scope_id,),
        ).fetchone()[0]
        return {
            "ok": True,
            "kind": "UYAP_AI_ARCHIVE_CASE_INDEX",
            "schemaVersion": AI_INDEX_SCHEMA_VERSION,
            "scopeId": scope_id,
            "institutionName": inspection["institutionName"],
            "caseId": inspection["caseId"],
            "readyForAi": True,
            "totalDocuments": inspection["totalDocuments"],
            "verifiedPhysicalDocuments": inspection["verifiedPhysicalDocuments"],
            "indexedDocuments": indexed,
            "unsupportedDocuments": unsupported,
            "failedDocuments": failed,
            "qualityRejectedDocuments": quality_rejected,
            "qualityRejectedByReason": quality_reasons,
            "qualityRejectedChunks": quality_rejected_chunks,
            "qualityRejectedChunksByReason": quality_chunk_reasons,
            "availableDocuments": int(available),
            "availableChunks": int(chunks),
            "fts5": bool(fts5),
            "extractionMethods": methods,
            "rawDocumentsUploaded": False,
            "networkUsed": False,
        }
    finally:
        db.close()



def build_index(downloads_root, session_id, bindings):
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("AI_SESSION_REQUIRED")
    rows = _archive_rows(root, session_id, bindings)
    db, fts5 = _index_db(root, session_id)
    indexed = reused = unsupported = failed = 0
    quality_rejected = 0
    quality_reasons = {}
    quality_rejected_chunks = 0
    quality_chunk_reasons = {}
    methods = {}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        for item in rows:
            ref = item["sourceRef"]
            current = db.execute(
                "SELECT file_sha256, extraction_method, extraction_quality FROM ai_documents WHERE source_ref=? AND session_id=?",
                (ref, session_id)
            ).fetchone()
            current_method = str(current[1] or "") if current else ""
            extension = item["path"].suffix.lower()
            current_method_ok = (
                (extension != ".pdf" or current_method == PDF_EXTRACTOR_METHOD)
                and (extension != ".xlsx" or current_method == XLSX_EXTRACTOR_METHOD)
            )
            if current and str(current[0]) == item["sha256"] and current_method_ok:
                reused += 1
                continue
            try:
                extracted = extract_text(item["path"])
                method = str(extracted.get("method") or "unknown")
                methods[method] = methods.get(method, 0) + 1
                chunks = chunk_text(extracted.get("text") or "")
                if item["path"].suffix.lower() == ".pdf" and chunks:
                    gated = _quality_gate_pdf_chunks(chunks)
                    chunks = gated["chunks"]
                    quality_rejected_chunks += int(gated["rejectedChunks"] or 0)
                    for reason, count in (gated["rejectedByReason"] or {}).items():
                        quality_chunk_reasons[reason] = quality_chunk_reasons.get(reason, 0) + int(count or 0)
                    if chunks and gated["rejectedChunks"]:
                        extracted["quality"] = "accepted_partial"
                        extracted["qualityReason"] = "chunk_quality_gate"
                if not chunks:
                    db.execute("DELETE FROM ai_chunks WHERE source_ref=?", (ref,))
                    if fts5:
                        db.execute("DELETE FROM ai_chunks_fts WHERE source_ref=?", (ref,))
                    db.execute("DELETE FROM ai_documents WHERE source_ref=? AND session_id=?", (ref, session_id))
                    if extracted.get("errorCode") == "AI_PDF_TEXT_LOW_QUALITY" or item["path"].suffix.lower() == ".pdf":
                        quality_rejected += 1
                        reason = str(extracted.get("qualityReason") or "all_pdf_chunks_rejected")
                        quality_reasons[reason] = quality_reasons.get(reason, 0) + 1
                    else:
                        unsupported += 1
                    continue
                db.execute("DELETE FROM ai_chunks WHERE source_ref=?", (ref,))
                if fts5:
                    db.execute("DELETE FROM ai_chunks_fts WHERE source_ref=?", (ref,))
                for chunk_no, chunk in enumerate(chunks):
                    db.execute("INSERT OR REPLACE INTO ai_chunks(source_ref,chunk_no,text) VALUES(?,?,?)", (ref, chunk_no, chunk))
                    if fts5:
                        db.execute("INSERT INTO ai_chunks_fts(source_ref,text) VALUES(?,?)", (ref, chunk))
                db.execute(
                    """INSERT OR REPLACE INTO ai_documents
                       (source_ref,session_id,document_id_hash,file_sha256,title,document_type,document_date,
                        extraction_method,extraction_quality,text_chars,chunk_count,indexed_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ref, session_id, hashlib.sha256(item["documentId"].encode()).hexdigest(), item["sha256"],
                     item["title"], item["path"].suffix.lower().lstrip(".").upper() or "DOSYA", item["date"],
                     method, extracted.get("quality"), len(extracted.get("text") or ""), len(chunks), now),
                )
                indexed += 1
            except Exception:
                failed += 1
        db.commit()
        available = db.execute("SELECT COUNT(*) FROM ai_documents WHERE session_id=?", (session_id,)).fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref WHERE d.session_id=?", (session_id,)).fetchone()[0]
        return {
            "ok": True, "kind": "UYAP_AI_LOCAL_INDEX", "schemaVersion": AI_INDEX_SCHEMA_VERSION,
            "sessionId": session_id, "verifiedSourceRows": len(rows), "indexedDocuments": indexed,
            "reusedDocuments": reused, "unsupportedDocuments": unsupported, "failedDocuments": failed,
            "qualityRejectedDocuments": quality_rejected, "qualityRejectedByReason": quality_reasons,
            "qualityRejectedChunks": quality_rejected_chunks, "qualityRejectedChunksByReason": quality_chunk_reasons,
            "availableDocuments": int(available), "availableChunks": int(chunks), "fts5": bool(fts5),
            "extractionMethods": methods, "pdfQualityGate": {"method": PDF_EXTRACTOR_METHOD, "minScore": PDF_MIN_QUALITY_SCORE},
            "rawDocumentsUploaded": False, "networkUsed": False,
        }
    finally:
        db.close()


def index_status(downloads_root, session_id):
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    path = _index_path(root, session_id)
    if not path.is_file():
        return {"ok": True, "kind": "UYAP_AI_INDEX_STATUS", "schemaVersion": 1, "ready": False, "sessionId": session_id, "documents": 0, "chunks": 0}
    db, fts5 = _index_db(root, session_id)
    try:
        documents = db.execute("SELECT COUNT(*) FROM ai_documents WHERE session_id=?", (session_id,)).fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref WHERE d.session_id=?", (session_id,)).fetchone()[0]
        return {"ok": True, "kind": "UYAP_AI_INDEX_STATUS", "schemaVersion": 1, "ready": int(chunks) > 0, "sessionId": session_id, "documents": int(documents), "chunks": int(chunks), "fts5": bool(fts5)}
    finally:
        db.close()


def _tokens(query):
    return [part.casefold() for part in re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]{2,}", str(query or ""))][:16]


def _summary_date_key(value):
    raw = str(value or "").strip()
    match = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", raw)
    if match:
        return tuple(int(part) for part in match.groups())
    match = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", raw)
    if match:
        day, month, year = (int(part) for part in match.groups())
        return (year, month, day)
    return (0, 0, 0)


def _summary_chunk_signal(text):
    value = _clean(text)
    if not value:
        return -1
    letters = sum(1 for ch in value if ch.isalpha())
    digits = sum(1 for ch in value if ch.isdigit())
    words = len(re.findall(r"[A-Za-zÇĞİÖŞÜçğıöşü]{3,}", value))
    numeric_penalty = max(0, digits - letters) * 3
    return letters * 3 + words * 12 + min(len(value), 2400) - numeric_penalty


# Body-text fallback rules, kept under the old name for diagnostics/tests.
SUMMARY_DOCUMENT_RULES = BODY_ROLE_RULES
_TEMPLATE_TOKEN = re.compile(r"\b[a-z]{2,}[A-Z][A-Za-z]{2,}\b")
_DECISION_ANCHOR = re.compile(
    r"(?i)GERE[ĞG][İI]\s+D[ÜU][ŞS][ÜU]N[ÜU]LD[ÜU]|(?m:^\s*G\.?\s?D\.?\s*:)|(?m:^\s*H[ÜU]K[ÜU]M\s*:)"
    r"|(?m:^\s*SONU[ÇC]\s*:)|(?m:^\s*NET[İI]CE\s+VE\s+TALEP)")
_SNIPPET_GAP = "\n…\n"
_TALIMAT_MARK = re.compile(r"(?i)TAL[İI]MAT\s+NO\s*:")


def _looks_like_template(text):
    """UYAP receipt/form templates leave camelCase placeholders (birimAdi, dosyaNo…)."""
    return len(_TEMPLATE_TOKEN.findall(str(text or ""))) >= 3


def fit_snippet(text, limit):
    """Shorten a document for the model without losing its decision.

    Court records put what matters last: interim decisions after "GEREĞİ
    DÜŞÜNÜLDÜ" and the next hearing date in the closing sentence. Cutting from
    the start dropped exactly that part, so keep the decision block (or the
    tail) and fill the remaining budget from the head.
    """
    value = _clean(text)
    limit = max(200, int(limit or 0))
    if len(value) <= limit:
        return value
    anchor = None
    for match in _DECISION_ANCHOR.finditer(value):
        anchor = match
    if anchor is not None and anchor.start() > limit * 0.3:
        tail = value[anchor.start():]
    else:
        tail = value[-int(limit * 0.4):]
    tail_budget = int(limit * 0.7) - len(_SNIPPET_GAP)
    if len(tail) > tail_budget:
        front = tail_budget // 2
        back = tail_budget - front - len(_SNIPPET_GAP)
        tail = tail[:front].rstrip() + _SNIPPET_GAP + tail[-back:].lstrip()
    head_budget = limit - len(tail) - len(_SNIPPET_GAP)
    head = value[:head_budget].rstrip() if head_budget > 0 else ""
    return (head + _SNIPPET_GAP + tail.lstrip()) if head else tail[:limit]


def _join_chunks(chunks):
    """Rebuild a document from its overlapping index chunks."""
    text = ""
    for chunk in chunks:
        chunk = str(chunk or "")
        if not text:
            text = chunk
            continue
        overlap = 0
        for size in range(min(len(text), len(chunk), DEFAULT_OVERLAP_CHARS + 64), 24, -1):
            if text.endswith(chunk[:size]):
                overlap = size
                break
        text += chunk[overlap:] if overlap else "\n" + chunk
    return text


def _summary_document_profile(row):
    source_ref, text, title, document_type, document_date, chunk_no = row
    titled = title_document_role(title)
    role_source = "title"
    if titled:
        role, reason, base_score = titled
    else:
        bodied = body_document_role(f"{document_type or ''} {text or ''}")
        if bodied:
            role, reason, base_score = bodied
            base_score *= 0.85
            role_source = "body"
        else:
            role, reason, base_score, role_source = "other", "Anlamlı dosya içeriği", 35, None
    signal = _summary_chunk_signal(text)
    quality_bonus = max(0.0, min(18.0, signal / 350.0))
    if is_attachment_title(title):
        base_score -= 15
    if _looks_like_template(text):
        # A receipt/form whose title merely names the petition it accompanies
        # (e.g. "Şikayet Dilekçesi (Ön Büro)") must not anchor the summary.
        role, reason, base_score, role_source = "low_value", "Form / alındı kaydı", 5, "template"
        quality_bonus = 0.0
    return {
        "row": row,
        "sourceRef": source_ref,
        "role": role,
        "roleSource": role_source,
        "reason": reason,
        "baseScore": float(base_score),
        "signal": float(signal),
        "score": float(base_score) + quality_bonus,
        "dateKey": _summary_date_key(document_date),
        "chunkNo": int(chunk_no or 0),
    }


def _select_summary_documents(rows, limit):
    """Deterministically select distinct, meaningful documents for the case summary.

    Selection is code-driven and title-first, not model-driven:
    - one best chunk per Dxxxxxx source,
    - anchors: the opening document and the final decision,
    - the newest procedural record (current state) and the earliest one
      (tensip: subject, parties, first hearing date),
    - then expert, response, enforcement and deadline evidence,
    - at most two documents of one role while filling (three hearing records),
    - financial/administrative slips (reddiyat, makbuz …) only when nothing
      else exists, so they cannot crowd out the record that matters.
    """
    limit = max(1, min(int(limit or 5), 6, MAX_RESULTS))
    best_by_source = {}
    for row in rows:
        profile = _summary_document_profile(row)
        current = best_by_source.get(profile["sourceRef"])
        rank = (profile["signal"], -profile["chunkNo"])
        if current is None or rank > (current["signal"], -current["chunkNo"]):
            best_by_source[profile["sourceRef"]] = profile

    documents = list(best_by_source.values())
    if not documents:
        return []
    meaningful = [item for item in documents if item["role"] != "low_value"]

    dated = [item for item in meaningful if item["dateKey"] != (0, 0, 0)]
    if dated:
        earliest = min(dated, key=lambda item: (item["dateKey"], item["sourceRef"]))
        latest = max(dated, key=lambda item: (item["dateKey"], item["sourceRef"]))
        earliest["score"] += 15
        earliest["timelineReason"] = "Dosyanın erken dönemi"
        latest["score"] += 20
        latest["timelineReason"] = "En güncel anlamlı evrak"

    selected = []
    selected_refs = set()
    role_counts = {}

    def add(item, forced_reason=None):
        if item is None or item["sourceRef"] in selected_refs or len(selected) >= limit:
            return False
        item = dict(item)
        if forced_reason:
            item["selectionReason"] = forced_reason
        elif item.get("timelineReason"):
            item["selectionReason"] = item["reason"] + " · " + item["timelineReason"]
        else:
            item["selectionReason"] = item["reason"]
        selected.append(item)
        selected_refs.add(item["sourceRef"])
        role_counts[item["role"]] = role_counts.get(item["role"], 0) + 1
        return True

    def best(role):
        candidates = [item for item in meaningful if item["role"] == role and item["sourceRef"] not in selected_refs]
        return max(candidates, key=lambda item: (item["score"], item["dateKey"], item["sourceRef"])) if candidates else None

    for role in ("opening", "final_decision"):
        add(best(role))

    procedural = [item for item in meaningful if item["role"] == "procedural"]
    if procedural:
        # Another court's talimat hearing is not the case's current state.
        talimat_refs = {
            row[0] for row in rows
            if _TALIMAT_MARK.search(str(row[1] or "")) or "talimat" in tr_lower(row[2])
        }
        own = [item for item in procedural if item["sourceRef"] not in talimat_refs] or procedural
        add(max(own, key=lambda item: (item["dateKey"], item["score"], item["sourceRef"])))
        dated_procedural = [item for item in procedural if item["dateKey"] != (0, 0, 0)]
        if dated_procedural:
            add(min(dated_procedural, key=lambda item: (item["dateKey"], -item["score"], item["sourceRef"])))

    for role in ("expert", "response", "enforcement", "deadline"):
        if len(selected) >= limit:
            break
        add(best(role))

    if dated and len(selected) < limit:
        newest = max(dated, key=lambda item: (item["dateKey"], item["score"], item["sourceRef"]))
        add(newest, "En güncel anlamlı evrak")

    ranked = sorted(
        meaningful,
        key=lambda item: (-item["score"], tuple(-x for x in item["dateKey"]), item["sourceRef"]),
    )
    for item in ranked:
        if len(selected) >= limit:
            break
        # Hearing records carry the case's decisions, so allow one more of them.
        cap = 3 if item["role"] == "procedural" else 2
        if item["sourceRef"] not in selected_refs and role_counts.get(item["role"], 0) < cap:
            add(item)
    for item in ranked:
        if len(selected) >= limit:
            break
        add(item)
    if not selected:
        for item in sorted(documents, key=lambda item: (-item["score"], item["sourceRef"])):
            add(item)
    return selected


def _select_summary_rows(rows, limit):
    """Compatibility helper used by tests; returns only the chosen row tuples."""
    return [item["row"] for item in _select_summary_documents(rows, limit)]


def _case_documents(db, session_id):
    """Every indexed document of a scope with its full, re-joined text."""
    documents = {}
    for source_ref, title, document_type, document_date in db.execute(
        "SELECT source_ref, title, document_type, document_date FROM ai_documents WHERE session_id=?",
        (session_id,),
    ).fetchall():
        documents[source_ref] = {"title": title, "documentType": document_type, "documentDate": document_date, "chunks": []}
    for source_ref, text in db.execute(
        """SELECT c.source_ref, c.text FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref
           WHERE d.session_id=? ORDER BY c.source_ref, c.chunk_no""",
        (session_id,),
    ).fetchall():
        if source_ref in documents:
            documents[source_ref]["chunks"].append(text)
    for document in documents.values():
        document["text"] = _join_chunks(document.pop("chunks"))
    return documents


def _read_meta(db, key):
    try:
        row = db.execute("SELECT value FROM ai_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
    except (sqlite3.Error, TypeError, ValueError):
        return None


def summary_context(downloads_root, session_id, limit=4, snippet_chars=1800):
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    limit = max(1, min(int(limit or 4), MAX_RESULTS))
    db, _ = _index_db(root, session_id)
    try:
        rows = db.execute(
            """SELECT c.source_ref, c.text, d.title, d.document_type, d.document_date, c.chunk_no
               FROM ai_chunks c
               JOIN ai_documents d ON d.source_ref=c.source_ref
               WHERE d.session_id=?
               ORDER BY d.document_date, c.source_ref, c.chunk_no""",
            (session_id,),
        ).fetchall()
        chosen = _select_summary_documents(rows, limit)
        documents = _case_documents(db, session_id)
        results = []
        for item in chosen:
            source_ref, text, title, document_type, document_date, chunk_no = item["row"]
            full_text = (documents.get(source_ref) or {}).get("text") or text
            results.append({
                "sourceRef": source_ref,
                "title": title,
                "documentType": document_type,
                "documentDate": document_date,
                # The whole document, shortened around its decision block, not
                # just the single "best" chunk.
                "snippet": fit_snippet(full_text, snippet_chars),
                "score": float(item["score"]),
                "chunkNo": int(chunk_no or 0),
                "summaryRole": item["role"],
                "selectionReason": item["selectionReason"],
            })
        return {
            "ok": True,
            "kind": "UYAP_AI_SUMMARY_CONTEXT",
            "schemaVersion": 2,
            "query": "__summary__",
            "results": results,
            "distinctSources": len(results),
            "rawDocumentsUploaded": False,
            "networkUsed": False,
        }
    finally:
        db.close()


def case_summary_facts(downloads_root, session_id, institution_name=None, case_id=None, today=None):
    """Rule-based case facts and coverage for a scope. No model is involved."""
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    db, _ = _index_db(root, session_id)
    try:
        documents = _case_documents(db, session_id)
        meta = _read_meta(db, "coverage")
    finally:
        db.close()
    facts = extract_case_facts(
        [
            {"ref": ref, "title": doc["title"], "date": parse_fact_date(doc["documentDate"]), "text": doc["text"]}
            for ref, doc in documents.items()
        ],
        institution_name=institution_name,
        case_id=case_id,
        today=today,
    )
    skipped = list((meta or {}).get("skipped") or [])

    def importance(item):
        role = title_document_role(item.get("title"))
        return (0 if role and role[0] not in {"low_value", "correspondence"} else 1, str(item.get("date") or ""))

    return {
        "ok": True,
        "kind": "UYAP_AI_CASE_FACTS",
        "schemaVersion": 1,
        "facts": facts,
        "coverage": {
            "totalDocuments": (meta or {}).get("totalDocuments"),
            "indexedDocuments": len(documents),
            "notIndexed": skipped,
            "notIndexedSamples": sorted(skipped, key=importance)[:6],
            "metaAvailable": meta is not None,
        },
        "documents": {
            ref: {"title": doc["title"], "documentType": doc["documentType"], "documentDate": doc["documentDate"]}
            for ref, doc in documents.items()
        },
        "rawDocumentsUploaded": False,
        "networkUsed": False,
    }


def search_index(downloads_root, session_id, query, limit=8):
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    tokens = _tokens(query)
    if not tokens:
        return {"ok": True, "kind": "UYAP_AI_SEARCH", "schemaVersion": 1, "query": "", "results": []}
    limit = max(1, min(int(limit or 8), MAX_RESULTS))
    db, fts5 = _index_db(root, session_id)
    try:
        rows = []
        if fts5:
            expression = " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)
            try:
                rows = db.execute(
                    """SELECT c.source_ref, c.text, d.title, d.document_type, d.document_date, bm25(ai_chunks_fts)
                       FROM ai_chunks_fts
                       JOIN ai_chunks c ON c.source_ref=ai_chunks_fts.source_ref AND c.text=ai_chunks_fts.text
                       JOIN ai_documents d ON d.source_ref=c.source_ref
                       WHERE d.session_id=? AND ai_chunks_fts MATCH ? ORDER BY bm25(ai_chunks_fts) LIMIT ?""",
                    (session_id, expression, limit),
                ).fetchall()
            except sqlite3.Error:
                rows = []
        if not rows:
            all_rows = db.execute(
                """SELECT c.source_ref, c.text, d.title, d.document_type, d.document_date
                   FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref WHERE d.session_id=?""",
                (session_id,),
            ).fetchall()
            scored = []
            for row in all_rows:
                folded = str(row[1]).casefold()
                score = sum(folded.count(token) for token in tokens)
                if score:
                    scored.append((score, row))
            scored.sort(key=lambda item: (-item[0], item[1][0]))
            rows = [(*row, -score) for score, row in scored[:limit]]
        results = []
        for source_ref, text, title, document_type, document_date, score in rows[:limit]:
            snippet = _clean(text)[:1400]
            results.append({"sourceRef": source_ref, "title": title, "documentType": document_type, "documentDate": document_date, "snippet": snippet, "score": float(score or 0)})
        return {"ok": True, "kind": "UYAP_AI_SEARCH", "schemaVersion": 1, "query": str(query or "")[:500], "results": results, "rawDocumentsUploaded": False, "networkUsed": False}
    finally:
        db.close()
