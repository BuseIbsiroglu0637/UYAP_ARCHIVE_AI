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
import zipfile
import zlib

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
PDF_EXTRACTOR_METHOD = "pdf-content-stream-v3-quality-gated"
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
    latin_ratio = latin_letters / max(1, letters)
    signal_ratio = (letters + digits) / total
    token_density = min(1.0, len(word_tokens) / max(1.0, len(value) / 18.0))

    score = 1.0
    score -= min(0.55, replacement_ratio * 12.0)
    score -= min(0.45, private_ratio * 10.0)
    score -= min(0.50, cjk_ratio * 2.5)
    if letters >= 24 and latin_ratio < 0.55:
        score -= min(0.35, (0.55 - latin_ratio) * 0.8)
    if visible_ratio < 0.98:
        score -= min(0.30, (0.98 - visible_ratio) * 3.0)
    if signal_ratio < 0.45:
        score -= min(0.25, (0.45 - signal_ratio) * 0.8)
    if len(value) >= 120 and token_density < 0.18:
        score -= min(0.25, (0.18 - token_density) * 1.2)
    if len(raw_tokens) >= 20 and single_token_ratio > 0.16:
        score -= min(0.35, (single_token_ratio - 0.16) * 1.8)
    if spaced_letter_runs:
        score -= min(0.35, spaced_letter_runs * 0.18)
    score = max(0.0, min(1.0, score))

    reason = None
    if replacement_ratio > 0.015:
        reason = "replacement_characters"
    elif private_ratio > 0.01:
        reason = "private_use_glyphs"
    elif cjk_ratio > 0.08 and latin_ratio < 0.75:
        reason = "suspicious_cid_glyphs"
    elif len(raw_tokens) >= 20 and single_token_ratio > 0.22:
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


def _sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _index_path(root):
    folder = root / "UYAP_Arsiv" / AI_INDEX_DIR
    folder.mkdir(parents=True, exist_ok=True)
    return folder / AI_INDEX_FILE


def _index_db(root):
    db = sqlite3.connect(_index_path(root), timeout=30.0)
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


def build_index(downloads_root, session_id, bindings):
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("AI_SESSION_REQUIRED")
    rows = _archive_rows(root, session_id, bindings)
    db, fts5 = _index_db(root)
    indexed = reused = unsupported = failed = 0
    quality_rejected = 0
    quality_reasons = {}
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
            if current and str(current[0]) == item["sha256"] and (
                item["path"].suffix.lower() != ".pdf" or current_method == PDF_EXTRACTOR_METHOD
            ):
                reused += 1
                continue
            try:
                extracted = extract_text(item["path"])
                method = str(extracted.get("method") or "unknown")
                methods[method] = methods.get(method, 0) + 1
                chunks = chunk_text(extracted.get("text") or "")
                if not chunks:
                    db.execute("DELETE FROM ai_chunks WHERE source_ref=?", (ref,))
                    if fts5:
                        db.execute("DELETE FROM ai_chunks_fts WHERE source_ref=?", (ref,))
                    db.execute("DELETE FROM ai_documents WHERE source_ref=? AND session_id=?", (ref, session_id))
                    if extracted.get("errorCode") == "AI_PDF_TEXT_LOW_QUALITY":
                        quality_rejected += 1
                        reason = str(extracted.get("qualityReason") or "low_quality")
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
            "availableDocuments": int(available), "availableChunks": int(chunks), "fts5": bool(fts5),
            "extractionMethods": methods, "pdfQualityGate": {"method": PDF_EXTRACTOR_METHOD, "minScore": PDF_MIN_QUALITY_SCORE},
            "rawDocumentsUploaded": False, "networkUsed": False,
        }
    finally:
        db.close()


def index_status(downloads_root, session_id):
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    path = _index_path(root)
    if not path.is_file():
        return {"ok": True, "kind": "UYAP_AI_INDEX_STATUS", "schemaVersion": 1, "ready": False, "sessionId": session_id, "documents": 0, "chunks": 0}
    db, fts5 = _index_db(root)
    try:
        documents = db.execute("SELECT COUNT(*) FROM ai_documents WHERE session_id=?", (session_id,)).fetchone()[0]
        chunks = db.execute("SELECT COUNT(*) FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref WHERE d.session_id=?", (session_id,)).fetchone()[0]
        return {"ok": True, "kind": "UYAP_AI_INDEX_STATUS", "schemaVersion": 1, "ready": int(chunks) > 0, "sessionId": session_id, "documents": int(documents), "chunks": int(chunks), "fts5": bool(fts5)}
    finally:
        db.close()


def _tokens(query):
    return [part.casefold() for part in re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşü]{2,}", str(query or ""))][:16]


def search_index(downloads_root, session_id, query, limit=8):
    root = Path(downloads_root).resolve(strict=True)
    session_id = str(session_id or "").strip()
    tokens = _tokens(query)
    if not tokens:
        return {"ok": True, "kind": "UYAP_AI_SEARCH", "schemaVersion": 1, "query": "", "results": []}
    limit = max(1, min(int(limit or 8), MAX_RESULTS))
    db, fts5 = _index_db(root)
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
