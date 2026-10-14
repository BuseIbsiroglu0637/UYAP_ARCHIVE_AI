#!/usr/bin/env python3
"""UYAP Arşivleyici yerel doğrulama ajanı.

Chrome Native Messaging üzerinden çalışır. Yalnızca yerel dosya yollarını işler;
ağ bağlantısı kurmaz ve kimlik doğrulama verisi saklamaz.
"""

from __future__ import annotations

import csv
import hashlib
import json
import mmap
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
import uuid
import re
import html
import xml.etree.ElementTree as ET
import zipfile
import zlib
from ai_local_index import (archive_case_scope_id as ai_archive_case_scope_id, build_index as ai_build_index, build_archive_case_index as ai_build_archive_case_index, inspect_archive_case as ai_inspect_archive_case, index_status as ai_index_status, search_index as ai_search_index)
from ai_provider import provider_status as ai_provider_status, answer_from_local_index as ai_answer_from_local_index
from collections import Counter
from pathlib import Path, PurePosixPath

AGENT_VERSION = "2.7.68"
PROTOCOL_VERSION = 2
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 1024 * 1024
HTML_MARKERS = (b"<!doctype html", b"<html", b"<body", b"<form")
ZIP_MAX_MEMBERS = 10_000
ZIP_MAX_SINGLE_MEMBER_BYTES = 512 * 1024 * 1024
ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_ARCHIVE_PART_BYTES = 1 * 1024 * 1024 * 1024
MAX_ARCHIVE_PART_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_DOCUMENTS_PER_PART = 1000
RESPONSE_ID_SAMPLE_LIMIT = 100
ARCHIVE_PART_MANIFEST = "_part-manifest.json"
JPEG_UYAP_TRAILING_MAX_BYTES = 2 * 1024 * 1024
PORTABLE_RESUME_CONFIG = "portable-resume-root.json"


AI_DIAGNOSTIC_FILE = "ai-diagnostic-latest.json"


def _ai_diagnostic_path():
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    root = (Path(base) if base else Path.home() / ".local" / "share") / "UYAPArsiv"
    return root / AI_DIAGNOSTIC_FILE


def _write_ai_diagnostic(message, result):
    try:
        path = _ai_diagnostic_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        query = str(message.get("query") or "")
        folded = query.casefold()
        query_class = "summary" if ("özet" in folded or "ozet" in folded) else "freeform"
        diagnostic = result.get("diagnostic") if isinstance(result, dict) and isinstance(result.get("diagnostic"), dict) else {}
        payload = {
            "schemaVersion": 1,
            "kind": "UYAP_AI_DIAGNOSTIC",
            "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "action": "ai_answer",
            "sessionId": str(message.get("sessionId") or ""),
            "queryClass": query_class,
            "queryStored": False,
            "ok": bool(isinstance(result, dict) and result.get("ok") is True),
            "errorCode": result.get("errorCode") if isinstance(result, dict) else "NATIVE_INVALID_RESULT",
            "error": result.get("error") if isinstance(result, dict) else "Native AI sonucu nesne değil.",
            "provider": (diagnostic.get("provider") or (result.get("provider") if isinstance(result, dict) else None)),
            "model": (diagnostic.get("model") or (result.get("model") if isinstance(result, dict) else None)),
            "retrievalMode": (diagnostic.get("retrievalMode") or (result.get("retrievalMode") if isinstance(result, dict) else None)),
            "groundingMode": (diagnostic.get("groundingMode") or (result.get("groundingMode") if isinstance(result, dict) else None)),
            "selectionPolicy": (diagnostic.get("selectionPolicy") or (result.get("selectionPolicy") if isinstance(result, dict) else None)),
            "selectedSources": diagnostic.get("selectedSources") or (result.get("sources") if isinstance(result, dict) else []),
            "sourceRefs": result.get("sourceRefs") if isinstance(result, dict) else [],
            "missingSections": result.get("missingSections") if isinstance(result, dict) else None,
            "unsupportedRoles": result.get("unsupportedRoles") if isinstance(result, dict) else None,
            "invalidSourceRefs": result.get("invalidSourceRefs") if isinstance(result, dict) else None,
            "uncitedClaims": result.get("uncitedClaims") if isinstance(result, dict) else None,
            "providerOutputPreview": diagnostic.get("providerOutputPreview"),
            "providerResponseId": diagnostic.get("providerResponseId") or (result.get("providerResponseId") if isinstance(result, dict) else None),
            "usage": diagnostic.get("usage") or (result.get("usage") if isinstance(result, dict) else None),
            "privacy": {
                "rawDocumentsStored": False,
                "rawSourceSnippetsStored": False,
                "queryStored": False,
                "providerOutputPreviewMaxChars": 1200,
            },
        }
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)
        return str(path)
    except Exception:
        return None


AI_SUMMARY_SCHEMA_VERSION = 2
AI_SUMMARY_MAX_ANSWER_CHARS = 24_000
AI_SUMMARY_MAX_SOURCES = 8


def _safe_summary_component(value, fallback):
    text = re.sub(r"[^0-9A-Za-zÇĞİÖŞÜçğıöşü._-]+", "_", str(value or "").strip())
    text = text.strip("._-")[:100]
    return text or fallback


def _ai_summary_scope_name(institution_name, case_id):
    return ai_archive_case_scope_id(institution_name, case_id).replace(":", "-")


def _central_ai_summary_directory(institution_name, case_id):
    override = os.environ.get("UYAP_AI_SUMMARY_STORE_ROOT")
    if override:
        root = Path(override).expanduser()
    else:
        base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
        root = (Path(base) if base else Path.home() / ".local" / "share") / "UYAPArsiv" / "AI_Summaries"
    folder = root / _ai_summary_scope_name(institution_name, case_id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder.resolve()


def _archive_ai_summary_directory(downloads_root, institution_name, case_id, create=True):
    if not downloads_root:
        return None
    try:
        root = Path(downloads_root).resolve(strict=True)
        archive_root = (root / "UYAP_Arsiv").resolve(strict=True)
        folder = archive_root / "_ai" / "summaries" / _ai_summary_scope_name(institution_name, case_id)
        if create:
            folder.mkdir(parents=True, exist_ok=True)
        resolved = folder.resolve()
        resolved.relative_to(archive_root)
        return resolved
    except (OSError, ValueError):
        return None


def _public_summary(payload, path=None):
    return {
        "summaryId": str(payload.get("summaryId") or ""),
        "schemaVersion": int(payload.get("schemaVersion") or AI_SUMMARY_SCHEMA_VERSION),
        "createdAt": payload.get("createdAt"),
        "institutionName": payload.get("institutionName"),
        "caseId": payload.get("caseId"),
        "answer": payload.get("answer"),
        "sources": payload.get("sources") or [],
        "sourceRefs": payload.get("sourceRefs") or [],
        "provider": payload.get("provider"),
        "model": payload.get("model"),
        "retrievalMode": payload.get("retrievalMode"),
        "selectionPolicy": payload.get("selectionPolicy"),
        "path": str(path) if path else payload.get("path"),
        "storage": payload.get("storage") or "central_local",
    }


def _summary_payloads_from(folder):
    if folder is None or not folder.is_dir():
        return []
    rows = []
    for path in sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("kind") != "UYAP_AI_SAVED_SUMMARY":
                continue
            rows.append((payload, path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return rows


def _write_summary_payload(folder, payload):
    folder.mkdir(parents=True, exist_ok=True)
    filename = _safe_summary_component(payload.get("summaryId"), "summary") + ".json"
    target = folder / filename
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, target)
    return target


def save_ai_summary(message):
    downloads_root = message.get("downloadsRoot")
    institution_name = str(message.get("institutionName") or "").strip()
    case_id = str(message.get("caseId") or "").strip()
    answer = str(message.get("answer") or "").strip()
    if not institution_name or not case_id:
        raise ValueError("AI özet kaydı için kurum ve dosya no gerekli")
    if not answer:
        raise ValueError("Kaydedilecek AI özeti boş")
    answer = answer[:AI_SUMMARY_MAX_ANSWER_CHARS]
    raw_sources = message.get("sources") or []
    sources = []
    if isinstance(raw_sources, list):
        for item in raw_sources[:AI_SUMMARY_MAX_SOURCES]:
            if not isinstance(item, dict):
                continue
            sources.append({
                "sourceRef": str(item.get("sourceRef") or "")[:32],
                "title": str(item.get("title") or "")[:300],
                "documentType": str(item.get("documentType") or "")[:64] or None,
                "documentDate": str(item.get("documentDate") or "")[:40] or None,
                "selectionReason": str(item.get("selectionReason") or "")[:240] or None,
                "summaryRole": str(item.get("summaryRole") or "")[:80] or None,
            })
    source_refs = [str(value)[:32] for value in (message.get("sourceRefs") or [])[:AI_SUMMARY_MAX_SOURCES]]
    digest_input = json.dumps(
        {"answer": answer, "sourceRefs": source_refs, "institutionName": institution_name, "caseId": case_id},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    summary_hash = hashlib.sha256(digest_input).hexdigest()
    central = _central_ai_summary_directory(institution_name, case_id)

    for payload, path in _summary_payloads_from(central):
        if payload.get("summaryHash") == summary_hash:
            return {"ok": True, "kind": "UYAP_AI_SAVED_SUMMARY", "deduplicated": True, "summary": _public_summary(payload, path)}

    # Backward compatibility: if an earlier build saved only inside the archive
    # tree, import that record into the stable central store on the next save.
    archive_folder = _archive_ai_summary_directory(downloads_root, institution_name, case_id, create=False)
    for payload, _path in _summary_payloads_from(archive_folder):
        if payload.get("summaryHash") == summary_hash:
            payload = {**payload, "schemaVersion": AI_SUMMARY_SCHEMA_VERSION, "storage": "central_local"}
            target = _write_summary_payload(central, payload)
            return {"ok": True, "kind": "UYAP_AI_SAVED_SUMMARY", "deduplicated": True, "summary": _public_summary(payload, target)}

    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    summary_id = f"summary-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{summary_hash[:12]}"
    payload = {
        "schemaVersion": AI_SUMMARY_SCHEMA_VERSION,
        "kind": "UYAP_AI_SAVED_SUMMARY",
        "summaryId": summary_id,
        "summaryHash": summary_hash,
        "createdAt": now,
        "institutionName": institution_name,
        "caseId": case_id,
        "answer": answer,
        "sources": sources,
        "sourceRefs": source_refs,
        "provider": str(message.get("provider") or "")[:80] or None,
        "model": str(message.get("model") or "")[:160] or None,
        "retrievalMode": str(message.get("retrievalMode") or "")[:80] or None,
        "selectionPolicy": str(message.get("selectionPolicy") or "")[:120] or None,
        "storage": "central_local",
        "privacy": {
            "localOnly": True,
            "rawDocumentsStored": False,
            "rawSourceSnippetsStored": False,
        },
    }
    target = _write_summary_payload(central, payload)

    # Best-effort mirror beside the archive for portability. The central copy is
    # authoritative so changing/rediscovering the archive root cannot erase UI history.
    archive_folder = _archive_ai_summary_directory(downloads_root, institution_name, case_id, create=True)
    if archive_folder is not None:
        try:
            _write_summary_payload(archive_folder, {**payload, "storage": "archive_mirror"})
        except OSError:
            pass

    return {"ok": True, "kind": "UYAP_AI_SAVED_SUMMARY", "deduplicated": False, "summary": _public_summary(payload, target)}


def list_ai_summaries(message):
    downloads_root = message.get("downloadsRoot")
    institution_name = str(message.get("institutionName") or "").strip()
    case_id = str(message.get("caseId") or "").strip()
    if not institution_name or not case_id:
        raise ValueError("AI özet geçmişi için kurum ve dosya no gerekli")
    limit = max(1, min(int(message.get("limit") or 20), 100))
    central = _central_ai_summary_directory(institution_name, case_id)
    archive_folder = _archive_ai_summary_directory(downloads_root, institution_name, case_id, create=False)

    merged = {}
    for payload, path in _summary_payloads_from(central):
        key = str(payload.get("summaryHash") or payload.get("summaryId") or path.name)
        merged[key] = (payload, path)

    # Import archive-only summaries created by the previous implementation.
    for payload, path in _summary_payloads_from(archive_folder):
        key = str(payload.get("summaryHash") or payload.get("summaryId") or path.name)
        if key not in merged:
            imported = {**payload, "schemaVersion": AI_SUMMARY_SCHEMA_VERSION, "storage": "central_local"}
            try:
                central_path = _write_summary_payload(central, imported)
                merged[key] = (imported, central_path)
            except OSError:
                merged[key] = (payload, path)

    ordered = sorted(
        merged.values(),
        key=lambda item: str(item[0].get("createdAt") or ""),
        reverse=True,
    )[:limit]
    summaries = [_public_summary(payload, path) for payload, path in ordered]
    return {
        "ok": True,
        "kind": "UYAP_AI_SAVED_SUMMARY_LIST",
        "schemaVersion": AI_SUMMARY_SCHEMA_VERSION,
        "institutionName": institution_name,
        "caseId": case_id,
        "count": len(summaries),
        "summaries": summaries,
        "historyRoot": str(central),
        "archiveMirrorChecked": archive_folder is not None,
    }


def _portable_resume_config_path():
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    return (Path(base) if base else Path.home() / ".local" / "share") / "UYAPArsiv" / PORTABLE_RESUME_CONFIG


def resolve_existing_archive_selection(value):
    """Normalize a user-selected UYAP_Arsiv folder (or its parent) to its root."""
    selected = Path(str(value or "")).expanduser().resolve(strict=True)
    if not selected.is_dir() or selected.is_symlink():
        raise ValueError("Seçilen arşiv klasörü geçerli değil")
    if selected.name.casefold() == "uyap_arsiv":
        root, archive = selected.parent, selected
    else:
        root, archive = selected, selected / "UYAP_Arsiv"
    if not archive.is_dir() or archive.is_symlink():
        raise ValueError("UYAP_Arsiv klasörünü veya onu içeren üst klasörü seçin")
    try:
        has_archive_evidence = (archive / "_archive.db").is_file() or any(
            item.name not in {"_incoming", "_unmatched", "_invalid", "_final"}
            for item in archive.iterdir()
        )
    except OSError as error:
        raise ValueError("Seçilen arşiv okunamıyor") from error
    if not has_archive_evidence:
        raise ValueError("Seçilen UYAP_Arsiv içinde devam edilecek arşiv verisi bulunamadı")
    return root.resolve()


def save_selected_archive_root(root, config_path=None):
    root = resolve_existing_archive_selection(root)
    path = Path(config_path) if config_path else _portable_resume_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schemaVersion": 1, "downloadsRoot": str(root), "archiveRoot": str(root / "UYAP_Arsiv")}
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)
    return payload


def load_selected_archive_root(config_path=None):
    path = Path(config_path) if config_path else _portable_resume_config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return resolve_existing_archive_selection(payload.get("downloadsRoot"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _windows_folder_picker_script():
    """Return a foreground-owned picker; ownerless WinForms dialogs can hide behind Chromium."""
    return r'''
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class UYAPForegroundWindow {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'UYAP Archive - Arsiv Secimi'
$owner.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
$owner.Location = New-Object System.Drawing.Point(-32000, -32000)
$owner.Size = New-Object System.Drawing.Size(1, 1)
$owner.ShowInTaskbar = $false
$owner.TopMost = $true
$owner.Opacity = 0
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = 'Devam edilecek UYAP_Arsiv klasorunu veya ust klasorunu secin'
$dialog.ShowNewFolderButton = $false
try {
  $owner.Show()
  $owner.Activate()
  [UYAPForegroundWindow]::SetForegroundWindow($owner.Handle) | Out-Null
  $result = $dialog.ShowDialog($owner)
  if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.SelectedPath
    exit 0
  }
  exit 2
} finally {
  $dialog.Dispose()
  $owner.Close()
  $owner.Dispose()
}
'''


def select_existing_archive(message):
    selection = message.get("selectedPath")
    if not selection:
        if os.name != "nt":
            raise ValueError("Klasör seçimi bu sistemde yalnız selectedPath ile kullanılabilir")
        try:
            completed = subprocess.run(
                ["powershell.exe", "-NoLogo", "-NoProfile", "-STA", "-Command", _windows_folder_picker_script()],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError("Klasör seçimi 3 dakika içinde tamamlanmadı; seçim penceresi kapatıldı") from error
        if completed.returncode == 2:
            return {"ok": True, "cancelled": True}
        if completed.returncode != 0:
            raise ValueError("Windows klasör seçicisi açılamadı")
        selection = completed.stdout.decode("utf-8-sig", errors="replace").strip()
    root = resolve_existing_archive_selection(selection)
    saved = save_selected_archive_root(root)
    result = list_archive_registry({"downloadsRoot": str(root), "limit": int(message.get("limit") or 10000)})
    return {**result, **saved, "ok": True, "selected": True, "portableResumeEnabled": True}


def get_selected_archive(_message=None):
    root = load_selected_archive_root()
    if root is None:
        return {"ok": True, "selected": False, "portableResumeEnabled": False}
    return {"ok": True, "selected": True, "portableResumeEnabled": True,
            "downloadsRoot": str(root), "archiveRoot": str(root / "UYAP_Arsiv")}


def _perf_ms(start):
    return round((time.perf_counter() - start) * 1000, 2)


def read_exact(stream, size, allow_eof=False):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            if allow_eof and not chunks:
                return None
            raise ValueError("Eksik Native Messaging cercevesi")
        chunks.extend(chunk)
    return bytes(chunks)


def read_message():
    raw_length = read_exact(sys.stdin.buffer, 4, allow_eof=True)
    if not raw_length:
        return None
    length = struct.unpack("=I", raw_length)[0]
    if not 0 < length <= MAX_MESSAGE_BYTES:
        raise ValueError("Native Messaging iletisi çok büyük")
    payload = read_exact(sys.stdin.buffer, length)
    message = json.loads(payload.decode("utf-8"))
    if not isinstance(message, dict):
        raise ValueError("Native Messaging iletisi nesne olmali")
    return message


def write_message(message):
    payload = json.dumps(message, ensure_ascii=False).encode("utf-8")
    if len(payload) > MAX_RESPONSE_BYTES:
        payload = b'{"ok":false,"error":"NATIVE_RESPONSE_TOO_LARGE"}'
    sys.stdout.buffer.write(struct.pack("=I", len(payload)))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def sha256_file(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_html(head: bytes):
    if head.startswith((b"%PDF-", b"PK\x03\x04", b"II*\x00", b"MM\x00*")):
        return False
    probe = head.lstrip(b"\xef\xbb\xbf\x00\t\r\n ").lower()
    return any(marker in probe.replace(b"\x00", b"")[:8192] for marker in HTML_MARKERS)


_HTML_SESSION_RE = re.compile(
    r"(?:oturum(?:unuz|unuzun|\s+süreniz)?\s+(?:sona\s+er(?:di|miştir)|sonlan(?:dı|mıştır)|kapat(?:ıldı|ılmıştır)|dol(?:du|muştur))|"
    r"oturum\s+süresi\s+(?:dol(?:du|muştur)|sona\s+er(?:di|miştir))|"
    r"yeniden\s+giriş\s+yap(?:ınız|manız)?|tekrar\s+giriş\s+yap(?:ınız|manız)?|"
    r"giriş\s+yapmanız\s+gerekmektedir|giriş\s+ekranına\s+yönlendir|"
    r"sistemden\s+çıkış\s+(?:yapıldı|yapılmıştır|gerçekleştirildi)|"
    r"zaman\s+aşımı|session\s+(?:expired|timeout|ended))",
    re.IGNORECASE,
)
_HTML_LOGIN_FORM_RE = re.compile(
    r"(?:<input\b[^>]{0,500}\btype\s*=\s*['\"]?password|"
    r"<form\b[^>]{0,1000}(?:login|giris|giriş|oauth|sso))",
    re.IGNORECASE | re.DOTALL,
)
_HTML_ERROR_PAGE_RE = re.compile(
    r"(?:<title[^>]*>\s*(?:hata|error)\b|"
    r"işleminiz\s+sırasında\s+bir\s+hata\s+oluştu|beklenmeyen\s+bir\s+hata\s+oluştu|"
    r"sistem\s+hatası|evrak\s+(?:görüntülenemedi|bulunamadı)|"
    r"işlem\s+gerçekleştirilemedi|yetkiniz\s+bulunmamaktadır|"
    r"internal\s+server\s+error|gateway\s+timeout|temporarily\s+unavailable)",
    re.IGNORECASE | re.DOTALL,
)
_HTML_REPORT_AREA_RE = re.compile(r"\bid\s*=\s*['\"]?divarea\d+_\d+", re.IGNORECASE)


def _read_html_probe(path: Path, limit: int = 2 * 1024 * 1024):
    raw = path.read_bytes()[:limit]
    # UYAP has produced both UTF-8 and UTF-16 HTML responses in the field.
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "cp1254", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _strong_uyap_document_provenance(context=None):
    """Return True only for a uniquely correlated UYAP-native document attempt.

    This is intentionally stronger than a filename extension. It requires an exact
    one-candidate browser/native correlation plus a learned UYAP source id and a
    native UYAP binding. It is safe to use as provenance evidence when URL hints
    are missing from Chrome's DownloadItem.
    """
    context = context or {}
    source_id = str(context.get("sourceDocumentId") or "").strip()
    binding_method = str(context.get("bindingMethod") or "").strip().lower()
    correlation_mode = str(context.get("correlationMode") or "").strip().lower()
    try:
        candidate_count = int(context.get("correlationCandidateCount") or 0)
    except (TypeError, ValueError):
        candidate_count = 0
    return bool(
        source_id and
        correlation_mode == "exact" and
        candidate_count == 1 and
        (binding_method.startswith("native-uyap-") or context.get("directDownloadEvidence") is True)
    )



def _binary_opaque_probe(path: Path):
    """Return True only for payloads that are genuinely opaque/binary.

    Unknown textual payloads are deliberately *not* accepted by the opaque
    fallback because login/error/API responses can be valid text without being
    valid documents. The opaque path exists only for exact UYAP-origin binary
    attachments whose format is not yet known to the sniffer.
    """
    try:
        if not path.is_file() or path.stat().st_size <= 0:
            return False
        with path.open("rb") as stream:
            sample = stream.read(64 * 1024)
    except OSError:
        return False
    if not sample or is_html(sample):
        return False
    stripped = sample.lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    if stripped.startswith((b"<", b"{", b"[")):
        return False
    if b"<!DOCTYPE" in sample.upper() or b"<HTML" in sample.upper():
        return False
    # A high printable-text ratio without NUL bytes is more likely an
    # unclassified text/error response than an opaque binary attachment.
    if b"\x00" not in sample:
        printable = sum(1 for byte in sample if byte in b"\t\r\n" or 32 <= byte <= 126)
        if sample and printable / len(sample) >= 0.92:
            return False
    return True


def _opaque_download_provenance(path: Path, message, context, completion_diag, detection_profile):
    """Strict proof for preserving an unknown UYAP binary without format guessing."""
    document = message.get("document") or {}
    if str(document.get("sourceSystem") or "").upper() not in {"UYAP", "UYAP_KURUM"}:
        return False
    if not _strong_uyap_document_provenance(context):
        return False
    if str((detection_profile or {}).get("extension") or "").lower() != ".bin":
        return False
    if str((detection_profile or {}).get("mode") or "") not in {"unknown", "ole_compound_unknown"}:
        return False
    if message.get("browserState") != "complete" or message.get("browserExists") is False:
        return False
    try:
        observed = int((completion_diag or {}).get("observedSize") or path.stat().st_size)
    except (OSError, TypeError, ValueError):
        return False
    browser_sizes = []
    for value in (message.get("browserFileSize"), message.get("browserTotalBytes")):
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            browser_sizes.append(number)
    if not browser_sizes or any(number != observed for number in browser_sizes):
        return False
    return _binary_opaque_probe(path)


def _opaque_quarantine_provenance(path: Path, document, context, detection_profile):
    """Byte-bound upgrade path for an old UNSUPPORTED_FORMAT quarantine entry."""
    if str(document.get("sourceSystem") or "").upper() not in {"UYAP", "UYAP_KURUM"}:
        return False
    if not _strong_uyap_document_provenance(context):
        return False
    if str((detection_profile or {}).get("extension") or "").lower() != ".bin":
        return False
    if str((detection_profile or {}).get("mode") or "") not in {"unknown", "ole_compound_unknown"}:
        return False
    try:
        size = path.stat().st_size
    except OSError:
        return False
    proven_sizes = []
    for key in ("browserFileSize", "browserTotalBytes", "actualSize", "observedInvalidSize"):
        try:
            value = int(document.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            proven_sizes.append(value)
    if not proven_sizes or any(value != size for value in proven_sizes):
        return False
    invalid_hash = str(document.get("invalidSha256") or "").strip().lower()
    if invalid_hash:
        if not re.fullmatch(r"[0-9a-f]{64}", invalid_hash):
            return False
        try:
            if sha256_file(path).lower() != invalid_hash:
                return False
        except OSError:
            return False
    return _binary_opaque_probe(path)


def classify_html_payload(path: Path, context=None):
    """Distinguish a real UYAP HTML document from an error/login HTML response.

    HTML is not inherently an error. Some UYAP records are intentionally delivered
    as printable HTML. We accept only when strong document evidence exists or the
    browser/native binding proves a canonical UYAP evrak_<id>.html download and no
    session/login markers are present.
    """
    try:
        text = _read_html_probe(path)
    except OSError:
        return {"kind": "unknown", "valid": False, "mode": "read_failed"}

    decoded = html.unescape(text)
    lower = decoded.lower()
    compact = re.sub(r"\s+", " ", decoded)

    if _HTML_SESSION_RE.search(compact) or _HTML_LOGIN_FORM_RE.search(decoded):
        return {"kind": "error_response", "valid": False, "mode": "session_or_login_marker"}
    if _HTML_ERROR_PAGE_RE.search(decoded):
        return {"kind": "error_response", "valid": False, "mode": "uyap_error_page_marker"}

    context = context or {}
    declared_kind = str(context.get("expectedDocumentKind") or "").strip().lower()
    # RC15.9.5: UYAP can intentionally serve legal records as HTML through
    # view_document_brd.uyap?mimeType=Html&evrakId=... . These records can be
    # tiny/simple (for example a KEP delivery message), so layout heuristics
    # alone are insufficient. The extension sets expectedDocumentKind only from
    # a validated UYAP document endpoint; login/error markers above still win.
    if declared_kind == "uyap_html_document" and decoded.strip():
        return {
            "kind": "document",
            "valid": True,
            "mode": "uyap_declared_html_document_v2",
            "reportAreaCount": len(_HTML_REPORT_AREA_RE.findall(decoded)),
            "tableCount": lower.count("<table"),
        }

    # RC15.9.10 CONTENT-TRUTH: Chrome does not always preserve finalUrl/url for
    # UYAP's attachment-style responses. In those cases the URL hint can vanish
    # even though correlation is exact. After login/error markers above have
    # been rejected, a uniquely correlated native UYAP evrak is strong source
    # evidence that a non-empty HTML payload is the requested legal document.
    # This fixes tiny KEP/notification records without weakening generic HTML.
    if _strong_uyap_document_provenance(context) and decoded.strip():
        return {
            "kind": "document",
            "valid": True,
            "mode": "uyap_exact_correlated_html_v3",
            "reportAreaCount": len(_HTML_REPORT_AREA_RE.findall(decoded)),
            "tableCount": lower.count("<table"),
        }

    # Common UYAP print/report output (the field sample had 40 divArea cells and
    # 45 table tags). Requiring several independent layout markers avoids accepting
    # a generic error page merely because it is HTML.
    report_areas = len(_HTML_REPORT_AREA_RE.findall(decoded))
    table_count = lower.count("<table")
    has_html_shell = "<html" in lower and "<body" in lower
    static_report = has_html_shell and report_areas >= 3 and table_count >= 2

    source_id = str(context.get("sourceDocumentId") or "").strip()
    original_name = Path(str(context.get("originalFilename") or "")).name
    binding_method = str(context.get("bindingMethod") or "")
    canonical_exact = False
    if source_id and original_name:
        canonical_exact = bool(re.fullmatch(
            rf"evrak[_ -]?{re.escape(source_id)}(?: \(\d+\))?\.html?",
            original_name,
            flags=re.IGNORECASE,
        )) and binding_method.startswith("native-uyap-exact-source-id")

    # Canonical exact-source binding is strong source evidence, but we still
    # require an HTML shell and reject active login/session forms above.
    canonical_document = has_html_shell and canonical_exact

    if static_report:
        return {
            "kind": "document",
            "valid": True,
            "mode": "uyap_static_report",
            "reportAreaCount": report_areas,
            "tableCount": table_count,
        }
    if canonical_document:
        return {
            "kind": "document",
            "valid": True,
            "mode": "canonical_exact_source_html",
            "reportAreaCount": report_areas,
            "tableCount": table_count,
        }
    return {
        "kind": "unknown_html",
        "valid": False,
        "mode": "insufficient_document_evidence",
        "reportAreaCount": report_areas,
        "tableCount": table_count,
    }



_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp'}
_JPEG_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}


def _jpeg_dimensions(path: Path):
    """Strict legacy JPEG pre-scan used as the first validation tier."""
    try:
        with path.open('rb') as stream:
            if stream.read(2) != b'\xff\xd8':
                return None
            dimensions = None
            while True:
                byte = stream.read(1)
                if not byte:
                    return None
                if byte != b'\xff':
                    continue
                while byte == b'\xff':
                    marker = stream.read(1)
                    if not marker:
                        return None
                    byte = marker
                code = byte[0]
                if code in (0xD8, 0xD9) or 0xD0 <= code <= 0xD7 or code == 0x01:
                    continue
                raw_length = stream.read(2)
                if len(raw_length) != 2:
                    return None
                seg_length = int.from_bytes(raw_length, 'big')
                if seg_length < 2:
                    return None
                payload_length = seg_length - 2
                if code in _JPEG_SOF_MARKERS:
                    payload = stream.read(payload_length)
                    if len(payload) < 5:
                        return None
                    height = int.from_bytes(payload[1:3], 'big')
                    width = int.from_bytes(payload[3:5], 'big')
                    if width <= 0 or height <= 0:
                        return None
                    dimensions = (width, height)
                    continue
                if code == 0xDA:  # Start of Scan proves an actual image stream follows.
                    if payload_length <= 0:
                        return None
                    payload = stream.read(payload_length)
                    if len(payload) != payload_length:
                        return None
                    return dimensions
                stream.seek(payload_length, os.SEEK_CUR)
    except OSError:
        return None


def _jpeg_compatible_profile(path: Path):
    """Fail-closed JPEG parser for UYAP/scanner variants rejected by tier 1.

    The compatibility tier still requires a real JPEG container: SOI, a
    dimension-bearing SOF, at least one SOS with entropy data, and EOI.  It
    additionally understands valid DNL (Define Number of Lines) streams where
    SOF height is initially zero, multiple scans/progressive streams, stuffed
    0xFF bytes and restart markers.  Arbitrary bytes after EOI remain rejected.
    """
    try:
        size = path.stat().st_size
        if size < 8:
            return {"valid": False, "mode": "jpeg_compatible_too_small"}
        with path.open('rb') as stream:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
                n = len(data)
                if data[0:2] != b'\xff\xd8':
                    return {"valid": False, "mode": "jpeg_compatible_missing_soi"}

                i = 2
                width = None
                height = None
                saw_sof = False
                saw_sos = False
                saw_entropy = False
                dnl_used = False
                scan_count = 0
                eoi_offset = None

                def read_segment(code_index):
                    length_index = code_index + 1
                    if length_index + 2 > n:
                        return None
                    seg_length = int.from_bytes(data[length_index:length_index + 2], 'big')
                    if seg_length < 2:
                        return None
                    payload_start = length_index + 2
                    segment_end = length_index + seg_length
                    if segment_end > n:
                        return None
                    return seg_length, payload_start, segment_end

                while i < n:
                    if data[i] != 0xFF:
                        return {"valid": False, "mode": "jpeg_compatible_marker_sync"}
                    marker_start = i
                    while i < n and data[i] == 0xFF:
                        i += 1
                    if i >= n:
                        return {"valid": False, "mode": "jpeg_compatible_truncated_marker"}
                    code = data[i]
                    code_index = i
                    i += 1

                    if code == 0x00:
                        return {"valid": False, "mode": "jpeg_compatible_stuffed_outside_scan"}
                    if code == 0xD9:
                        eoi_offset = marker_start
                        break
                    if code == 0xD8:
                        return {"valid": False, "mode": "jpeg_compatible_nested_soi"}
                    if 0xD0 <= code <= 0xD7 or code == 0x01:
                        continue

                    segment = read_segment(code_index)
                    if not segment:
                        return {"valid": False, "mode": "jpeg_compatible_bad_segment"}
                    seg_length, payload_start, segment_end = segment
                    payload_len = seg_length - 2

                    if code in _JPEG_SOF_MARKERS:
                        if payload_len < 6:
                            return {"valid": False, "mode": "jpeg_compatible_short_sof"}
                        precision = data[payload_start]
                        candidate_height = int.from_bytes(data[payload_start + 1:payload_start + 3], 'big')
                        candidate_width = int.from_bytes(data[payload_start + 3:payload_start + 5], 'big')
                        components = data[payload_start + 5]
                        if precision == 0 or candidate_width <= 0 or components <= 0:
                            return {"valid": False, "mode": "jpeg_compatible_invalid_sof"}
                        if payload_len < 6 + 3 * components:
                            return {"valid": False, "mode": "jpeg_compatible_short_sof_components"}
                        width = candidate_width
                        height = candidate_height
                        saw_sof = True

                    elif code == 0xDC:  # DNL - valid when SOF height was zero/unknown.
                        if payload_len != 2:
                            return {"valid": False, "mode": "jpeg_compatible_bad_dnl"}
                        lines = int.from_bytes(data[payload_start:payload_start + 2], 'big')
                        if lines <= 0:
                            return {"valid": False, "mode": "jpeg_compatible_zero_dnl"}
                        if height in (None, 0):
                            height = lines
                            dnl_used = True

                    elif code == 0xDA:  # SOS
                        if payload_len < 4:
                            return {"valid": False, "mode": "jpeg_compatible_short_sos"}
                        scan_components = data[payload_start]
                        if scan_components <= 0 or payload_len < 4 + 2 * scan_components:
                            return {"valid": False, "mode": "jpeg_compatible_bad_sos"}
                        saw_sos = True
                        scan_count += 1
                        i = segment_end

                        # Entropy-coded scan. 0xFF00 is a stuffed data byte and
                        # RST0..RST7 are legal in-stream markers. DNL may also
                        # interrupt the scan and then entropy data continues.
                        while i < n:
                            ff = data.find(b'\xff', i)
                            if ff < 0:
                                return {"valid": False, "mode": "jpeg_compatible_missing_eoi"}
                            if ff > i:
                                saw_entropy = True
                            j = ff
                            while j < n and data[j] == 0xFF:
                                j += 1
                            if j >= n:
                                return {"valid": False, "mode": "jpeg_compatible_truncated_scan_marker"}
                            scan_code = data[j]
                            if scan_code == 0x00:
                                saw_entropy = True
                                i = j + 1
                                continue
                            if 0xD0 <= scan_code <= 0xD7:
                                saw_entropy = True
                                i = j + 1
                                continue
                            if scan_code == 0xDC:
                                dnl_segment = read_segment(j)
                                if not dnl_segment:
                                    return {"valid": False, "mode": "jpeg_compatible_bad_scan_dnl"}
                                dnl_length, dnl_payload_start, dnl_end = dnl_segment
                                if dnl_length != 4:
                                    return {"valid": False, "mode": "jpeg_compatible_bad_scan_dnl_length"}
                                lines = int.from_bytes(data[dnl_payload_start:dnl_payload_start + 2], 'big')
                                if lines <= 0:
                                    return {"valid": False, "mode": "jpeg_compatible_zero_scan_dnl"}
                                if height in (None, 0):
                                    height = lines
                                    dnl_used = True
                                i = dnl_end
                                continue
                            # EOI or another real marker ends this entropy span;
                            # let the outer marker parser consume it.
                            i = ff
                            break
                        continue

                    i = segment_end

                if eoi_offset is None or not saw_sof or not saw_sos or not saw_entropy:
                    return {"valid": False, "mode": "jpeg_compatible_incomplete_structure"}
                if not width or not height or width <= 0 or height <= 0:
                    return {"valid": False, "mode": "jpeg_compatible_missing_dimensions"}

                trailing = bytes(data[eoi_offset + 2:])
                if trailing.strip(b' \t\r\n\f\x00'):
                    return {"valid": False, "mode": "jpeg_compatible_trailing_data"}

                return {
                    "valid": True,
                    "kind": "JPEG",
                    "extension": ".jpg",
                    "mode": "jpeg_uyap_compatible_dnl" if dnl_used else "jpeg_uyap_compatible_marker_stream",
                    "width": int(width),
                    "height": int(height),
                    "scanCount": int(scan_count),
                    "dnlUsed": bool(dnl_used),
                }
    except (OSError, ValueError):
        return {"valid": False, "mode": "jpeg_compatible_read_error"}



def _windows_wic_jpeg_probe(path: Path):
    """Fail-closed JPEG raster decode through Windows Imaging Component.

    WPF's JpegBitmapDecoder is backed by WIC on supported desktop Windows.
    We instantiate the JPEG-specific decoder, validate positive dimensions, and
    copy a small pixel rectangle to force actual raster decoding. This is a
    stronger compatibility fallback for files Explorer/Windows can render when
    the lightweight marker parser rejects a scanner/vendor-specific stream.
    """
    if os.name != 'nt':
        return {"valid": False, "mode": "jpeg_windows_wic_unavailable"}
    try:
        if not path.is_file() or path.stat().st_size < 8:
            return {"valid": False, "mode": "jpeg_windows_wic_missing"}
        script = r"""$ErrorActionPreference='Stop'
Add-Type -AssemblyName WindowsBase
Add-Type -AssemblyName PresentationCore
$p=$env:UYAP_IMAGE_PROBE_PATH
$fs=[System.IO.File]::Open($p,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::ReadWrite)
try {
  $decoder=[System.Windows.Media.Imaging.JpegBitmapDecoder]::new($fs,[System.Windows.Media.Imaging.BitmapCreateOptions]::PreservePixelFormat,[System.Windows.Media.Imaging.BitmapCacheOption]::OnLoad)
  if ($decoder.Frames.Count -lt 1) { throw 'NO_FRAME' }
  $frame=$decoder.Frames[0]
  $w=[int]$frame.PixelWidth; $h=[int]$frame.PixelHeight
  if ($w -le 0 -or $h -le 0) { throw 'BAD_DIMENSIONS' }
  $sw=[Math]::Min(32,$w); $sh=[Math]::Min(32,$h)
  $bpp=[Math]::Max(1,[int]$frame.Format.BitsPerPixel)
  $stride=[Math]::Max(1,[int][Math]::Ceiling(($sw*$bpp)/8.0))
  $buf=[System.Array]::CreateInstance([byte],($stride*$sh))
  $rect=[System.Windows.Int32Rect]::new(0,0,$sw,$sh)
  $frame.CopyPixels($rect,$buf,$stride,0)
  [Console]::Out.Write((@{ok=$true;width=$w;height=$h;sampleBytes=$buf.Length}|ConvertTo-Json -Compress))
} finally { $fs.Dispose() }
"""
        import base64
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        env = os.environ.copy()
        env['UYAP_IMAGE_PROBE_PATH'] = str(path)
        completed = subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            timeout=15, check=False,
        )
        if completed.returncode != 0:
            detail = completed.stderr.decode('utf-8-sig', errors='replace').strip()
            return {"valid": False, "mode": "jpeg_windows_wic_rejected", "decoderDetail": detail[-240:] if detail else None}
        raw = completed.stdout.decode('utf-8-sig', errors='replace').strip()
        payload = json.loads(raw) if raw else {}
        width = int(payload.get('width') or 0)
        height = int(payload.get('height') or 0)
        if payload.get('ok') is True and width > 0 and height > 0 and int(payload.get('sampleBytes') or 0) > 0:
            return {
                "valid": True, "kind": "JPEG", "extension": ".jpg",
                "mode": "jpeg_windows_wic_decode", "width": width,
                "height": height,
            }
        return {"valid": False, "mode": "jpeg_windows_wic_invalid_result"}
    except subprocess.TimeoutExpired:
        return {"valid": False, "mode": "jpeg_windows_wic_timeout"}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"valid": False, "mode": "jpeg_windows_wic_error"}


def _windows_gdiplus_jpeg_probe(path: Path):
    # Fail-closed third-tier JPEG decode using the Windows built-in GDI+ codec.
    if os.name != 'nt':
        return {"valid": False, "mode": "jpeg_windows_decoder_unavailable"}
    try:
        if not path.is_file() or path.stat().st_size < 8:
            return {"valid": False, "mode": "jpeg_windows_decoder_missing"}
        script = """$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$p=$env:UYAP_IMAGE_PROBE_PATH
$fs=[System.IO.File]::Open($p,[System.IO.FileMode]::Open,[System.IO.FileAccess]::Read,[System.IO.FileShare]::ReadWrite)
try {
  $img=[System.Drawing.Image]::FromStream($fs,$false,$true)
  try {
    if ($img.RawFormat.Guid -ne [System.Drawing.Imaging.ImageFormat]::Jpeg.Guid) { throw 'NOT_JPEG' }
    $w=[int]$img.Width; $h=[int]$img.Height
    if ($w -le 0 -or $h -le 0) { throw 'BAD_DIMENSIONS' }
    $thumb=New-Object System.Drawing.Bitmap(32,32)
    try {
      $g=[System.Drawing.Graphics]::FromImage($thumb)
      try { $g.DrawImage($img,0,0,32,32) } finally { $g.Dispose() }
      $null=$thumb.GetPixel(0,0)
    } finally { $thumb.Dispose() }
    [Console]::Out.Write((@{ok=$true;width=$w;height=$h}|ConvertTo-Json -Compress))
  } finally { $img.Dispose() }
} finally { $fs.Dispose() }
"""
        import base64
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        env = os.environ.copy()
        env['UYAP_IMAGE_PROBE_PATH'] = str(path)
        completed = subprocess.run(
            ['powershell.exe', '-NoLogo', '-NoProfile', '-NonInteractive',
             '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encoded],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
            timeout=12, check=False,
        )
        if completed.returncode != 0:
            return {"valid": False, "mode": "jpeg_windows_decoder_rejected"}
        raw = completed.stdout.decode('utf-8-sig', errors='replace').strip()
        payload = json.loads(raw) if raw else {}
        width = int(payload.get('width') or 0)
        height = int(payload.get('height') or 0)
        if payload.get('ok') is True and width > 0 and height > 0:
            return {
                "valid": True, "kind": "JPEG", "extension": ".jpg",
                "mode": "jpeg_windows_gdiplus_decode", "width": width,
                "height": height,
            }
        return {"valid": False, "mode": "jpeg_windows_decoder_invalid_result"}
    except subprocess.TimeoutExpired:
        return {"valid": False, "mode": "jpeg_windows_decoder_timeout"}
    except (OSError, ValueError, json.JSONDecodeError):
        return {"valid": False, "mode": "jpeg_windows_decoder_error"}


def _jpeg_trailing_profile(path: Path):
    """Inspect bytes after the last JPEG EOI without rewriting the source file.

    UYAP/scanner exports seen in the field may append opaque source metadata after
    the final EOI. We preserve those bytes exactly. They are acceptable only when
    the download has strong UYAP provenance and a real Windows JPEG decoder can
    still rasterize the complete original file. This helper only classifies the
    trailer; it never makes a validity decision by itself.
    """
    try:
        size = path.stat().st_size
        window = min(size, JPEG_UYAP_TRAILING_MAX_BYTES + 64 * 1024)
        with path.open('rb') as stream:
            stream.seek(max(0, size - window))
            tail = stream.read(window)
    except OSError:
        return {"present": False, "safe": False, "mode": "jpeg_trailing_read_error", "bytes": 0}
    eoi = tail.rfind(b'\xff\xd9')
    if eoi < 0:
        return {"present": False, "safe": False, "mode": "jpeg_trailing_eoi_not_in_window", "bytes": 0, "bodyEnd": None}
    body_end = (size - window) + eoi + 2
    trailing = bytes(tail[eoi + 2:])
    if not trailing:
        return {"present": False, "safe": True, "mode": "jpeg_clean_end", "bytes": 0, "bodyEnd": body_end}
    if not trailing.strip(b' \t\r\n\f\x00'):
        return {"present": True, "safe": True, "mode": "jpeg_whitespace_trailer", "bytes": len(trailing), "bodyEnd": body_end}
    if len(trailing) > JPEG_UYAP_TRAILING_MAX_BYTES:
        return {"present": True, "safe": False, "mode": "jpeg_trailer_too_large", "bytes": len(trailing), "bodyEnd": body_end}
    probe = trailing.lstrip(b' \t\r\n\f\x00')[:512]
    lower = probe.lower()
    forbidden = (
        b'MZ', b'PK\x03\x04', b'PK\x05\x06', b'%PDF-', b'\x7fELF',
        b'Rar!\x1a\x07', b'7z\xbc\xaf\x27\x1c', b'\x89PNG\r\n\x1a\n',
        b'GIF87a', b'GIF89a', b'II*\x00', b'MM\x00*', b'\xff\xd8\xff'
    )
    if any(probe.startswith(sig) for sig in forbidden):
        return {"present": True, "safe": False, "mode": "jpeg_trailer_embedded_file_signature", "bytes": len(trailing), "bodyEnd": body_end}
    if lower.startswith((b'<!doctype html', b'<html', b'<body', b'<form', b'<script')):
        return {"present": True, "safe": False, "mode": "jpeg_trailer_html_payload", "bytes": len(trailing), "bodyEnd": body_end}
    return {
        "present": True,
        "safe": True,
        "mode": "uyap_opaque_trailer_preserved",
        "bytes": len(trailing),
        "bodyEnd": body_end,
    }


def _windows_decode_preserved_jpeg(path: Path, body_end=None):
    """Decode original JPEG, then a temporary EOI-bounded view if trailer confuses Windows.

    The archive source is never modified. The temporary file exists only for
    raster validation and contains bytes 0..EOI inclusive.
    """
    wic = _windows_wic_jpeg_probe(path)
    if wic.get("valid"):
        wic["decoderInput"] = "original"
        return wic
    gdiplus = _windows_gdiplus_jpeg_probe(path)
    if gdiplus.get("valid"):
        gdiplus["decoderInput"] = "original"
        gdiplus["wicFallbackMode"] = wic.get("mode")
        return gdiplus
    try:
        end = int(body_end or 0)
    except (TypeError, ValueError):
        end = 0
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    if end <= 0 or end >= size:
        return {
            "valid": False, "mode": "jpeg_windows_decoders_rejected",
            "wicFallbackMode": wic.get("mode"),
            "gdiplusFallbackMode": gdiplus.get("mode"),
        }
    temp_path = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix="uyap-jpeg-probe-", suffix=".jpg")
        os.close(fd)
        temp_path = Path(temp_name)
        remaining = end
        with path.open('rb') as src, temp_path.open('wb') as dst:
            while remaining > 0:
                chunk = src.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                dst.write(chunk)
                remaining -= len(chunk)
        if remaining != 0:
            return {"valid": False, "mode": "jpeg_body_probe_copy_incomplete"}
        wic_body = _windows_wic_jpeg_probe(temp_path)
        if wic_body.get("valid"):
            wic_body["decoderInput"] = "eoi_bounded_copy"
            wic_body["fullFileWicMode"] = wic.get("mode")
            wic_body["fullFileGdiplusMode"] = gdiplus.get("mode")
            return wic_body
        gdip_body = _windows_gdiplus_jpeg_probe(temp_path)
        if gdip_body.get("valid"):
            gdip_body["decoderInput"] = "eoi_bounded_copy"
            gdip_body["fullFileWicMode"] = wic.get("mode")
            gdip_body["fullFileGdiplusMode"] = gdiplus.get("mode")
            gdip_body["wicFallbackMode"] = wic_body.get("mode")
            return gdip_body
        return {
            "valid": False, "mode": "jpeg_windows_decoders_rejected",
            "wicFallbackMode": wic_body.get("mode") or wic.get("mode"),
            "gdiplusFallbackMode": gdip_body.get("mode") or gdiplus.get("mode"),
            "fullFileWicMode": wic.get("mode"),
            "fullFileGdiplusMode": gdiplus.get("mode"),
        }
    except OSError:
        return {"valid": False, "mode": "jpeg_body_probe_temp_error"}
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def classify_image_payload(path: Path, context=None):
    """Detect and structurally validate common UYAP image attachments.

    Detection is based on the payload signature, never only on the filename.
    The checks deliberately stay lightweight so multi-thousand document runs do
    not decode image pixels merely to prove that the container is structurally
    plausible.
    """
    if not path.is_file():
        return {"valid": False, "kind": None, "extension": None, "mode": "missing"}
    size = path.stat().st_size
    if size <= 0:
        return {"valid": False, "kind": None, "extension": None, "mode": "empty"}
    try:
        with path.open('rb') as stream:
            head = stream.read(64)
            tail_len = min(size, 64 * 1024)
            stream.seek(max(0, size - tail_len))
            tail = stream.read(tail_len)
    except OSError:
        return {"valid": False, "kind": None, "extension": None, "mode": "read_error"}

    if head.startswith(b'\xff\xd8\xff'):
        # Tier 1 preserves the original strict validator. Tier 2 accepts only
        # structurally complete UYAP/scanner JPEG variants (not merely SOI bytes).
        dims = _jpeg_dimensions(path)
        trailer = _jpeg_trailing_profile(path)
        clean_end = bool(trailer.get("safe")) and (
            not trailer.get("present") or trailer.get("mode") == "jpeg_whitespace_trailer"
        )
        if dims and clean_end:
            return {
                "valid": True, "kind": "JPEG", "extension": ".jpg",
                "mode": "jpeg_marker_structure", "width": dims[0], "height": dims[1],
                "trailingBytes": int(trailer.get("bytes") or 0),
                "trailingKind": trailer.get("mode"),
            }
        compatible = _jpeg_compatible_profile(path)
        if compatible.get("valid"):
            compatible["trailingBytes"] = int(trailer.get("bytes") or 0)
            compatible["trailingKind"] = trailer.get("mode")
            return compatible

        fallback_from = compatible.get("mode") or "invalid_jpeg_structure"

        # RC15.9.10: UYAP-origin JPEGs with source metadata after EOI are kept
        # byte-for-byte. We never trim/rewrite them. They are accepted only with
        # exact one-candidate UYAP provenance, a bounded/non-embedded trailer, and
        # a successful real Windows raster decode of the complete original file.
        strong_uyap = _strong_uyap_document_provenance(context)
        trailer_can_decode = bool(trailer.get("safe")) and (
            not trailer.get("present") or strong_uyap
        )
        if trailer_can_decode:
            decoded = _windows_decode_preserved_jpeg(path, trailer.get("bodyEnd"))
            if decoded.get("valid"):
                decoded["fallbackFromMode"] = fallback_from
                decoded["trailingBytes"] = int(trailer.get("bytes") or 0)
                decoded["trailingKind"] = trailer.get("mode")
                if trailer.get("present") and trailer.get("mode") != "jpeg_whitespace_trailer":
                    decoded["mode"] = "jpeg_uyap_trailing_preserved_wic" if "wic" in str(decoded.get("mode") or "") else "jpeg_uyap_trailing_preserved_gdiplus"
                return decoded
            return {
                "valid": False, "kind": "JPEG", "extension": ".jpg",
                "mode": decoded.get("mode") or "jpeg_windows_decoders_rejected",
                "fallbackFromMode": fallback_from,
                "wicFallbackMode": decoded.get("wicFallbackMode"),
                "gdiplusFallbackMode": decoded.get("gdiplusFallbackMode"),
                "trailingBytes": int(trailer.get("bytes") or 0),
                "trailingKind": trailer.get("mode"),
            }
        return {
            "valid": False, "kind": "JPEG", "extension": ".jpg",
            "mode": fallback_from,
            "trailingBytes": int(trailer.get("bytes") or 0),
            "trailingKind": trailer.get("mode"),
        }

    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        if len(head) >= 24 and head[12:16] == b'IHDR':
            width = int.from_bytes(head[16:20], 'big')
            height = int.from_bytes(head[20:24], 'big')
            iend = tail.rfind(b'IEND')
            if width > 0 and height > 0 and iend >= 4 and tail[iend - 4:iend] == b'\x00\x00\x00\x00':
                return {
                    "valid": True, "kind": "PNG", "extension": ".png",
                    "mode": "png_signature_ihdr_iend", "width": width, "height": height,
                }
        return {"valid": False, "kind": "PNG", "extension": ".png", "mode": "invalid_png_structure"}

    if head.startswith((b'GIF87a', b'GIF89a')):
        if len(head) >= 10:
            width = int.from_bytes(head[6:8], 'little')
            height = int.from_bytes(head[8:10], 'little')
            if width > 0 and height > 0 and tail.rstrip(b' \t\r\n\x00').endswith(b';'):
                return {
                    "valid": True, "kind": "GIF", "extension": ".gif",
                    "mode": "gif_header_trailer", "width": width, "height": height,
                }
        return {"valid": False, "kind": "GIF", "extension": ".gif", "mode": "invalid_gif_structure"}

    if head.startswith(b'BM') and len(head) >= 30:
        declared_size = int.from_bytes(head[2:6], 'little')
        pixel_offset = int.from_bytes(head[10:14], 'little')
        dib_size = int.from_bytes(head[14:18], 'little')
        width = abs(int.from_bytes(head[18:22], 'little', signed=True))
        height = abs(int.from_bytes(head[22:26], 'little', signed=True))
        declared_ok = declared_size in (0, size) or (0 < declared_size <= size)
        if dib_size >= 12 and width > 0 and height > 0 and 14 + dib_size <= size and pixel_offset < size and declared_ok:
            return {
                "valid": True, "kind": "BMP", "extension": ".bmp",
                "mode": "bmp_header_structure", "width": width, "height": height,
            }
        return {"valid": False, "kind": "BMP", "extension": ".bmp", "mode": "invalid_bmp_structure"}

    return {"valid": False, "kind": None, "extension": None, "mode": "not_image"}




def _local_zip_first_name(path: Path):
    """Return the first local-file entry name without requiring a ZIP central directory.

    Some UYAP UDF downloads are valid streaming ZIP payloads consisting of local
    file headers + data descriptors only. Python's zipfile module intentionally
    rejects those because the central directory is absent. The UYAP document
    editor still treats them as UDF packages, so type detection must not demote
    them to .bin merely because the directory footer is missing.
    """
    try:
        with path.open('rb') as stream:
            fixed = stream.read(30)
            if len(fixed) < 30 or fixed[:4] != b'PK\x03\x04':
                return None
            (_sig, _ver, flags, _method, _mtime, _mdate, _crc, _csize, _usize,
             name_len, extra_len) = struct.unpack('<IHHHHHIIIHH', fixed)
            if flags & 0x0001:
                return None
            if name_len <= 0 or name_len > 65535 or extra_len > 65535:
                return None
            raw_name = stream.read(name_len)
            encoding = 'utf-8' if flags & 0x0800 else 'cp437'
            return raw_name.decode(encoding, errors='replace').replace('\\', '/')
    except (OSError, struct.error, UnicodeError):
        return None


def _safe_udf_member_name(name: str):
    normalized = str(name or '').replace('\\', '/')
    pure = PurePosixPath(normalized)
    if not normalized or pure.is_absolute() or any(part in ('', '.', '..') for part in pure.parts):
        raise ValueError('UDF paketinde guvensiz dosya adi var.')
    return normalized


def _xml_local_name(tag):
    return str(tag or '').split('}', 1)[-1].lower()


def _is_uyap_template_xml_bytes(raw: bytes):
    """Return (is_uyap_template, parsed_root_or_none) without external entities."""
    if not raw:
        return False, None
    upper = raw.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
        return False, None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False, None
    if _xml_local_name(root.tag) != 'template':
        return False, root
    format_id = str(root.attrib.get('format_id') or root.attrib.get('formatId') or '').strip()
    if not format_id or len(format_id) > 32:
        return False, root
    child_names = {_xml_local_name(child.tag) for child in list(root)}
    required = {'content', 'elements', 'styles'}
    return required.issubset(child_names), root


def _safe_zip_members(archive: zipfile.ZipFile):
    members = archive.infolist()
    if len(members) > ZIP_MAX_MEMBERS:
        raise ValueError('ZIP çok fazla üye içeriyor.')
    seen = set()
    total = 0
    for item in members:
        name = item.filename.replace('\\', '/')
        lowered = name.lower()
        if lowered in seen:
            raise ValueError('ZIP tekrarlanan dosya adı içeriyor.')
        seen.add(lowered)
        parts = PurePosixPath(name).parts
        if PurePosixPath(name).is_absolute() or any(part in ('..', '.') for part in parts):
            raise ValueError(f'ZIP güvenli olmayan yol içeriyor: {name}')
        unix_mode = (item.external_attr >> 16) & 0o170000
        if unix_mode == 0o120000:
            raise ValueError(f'ZIP sembolik bağ içeriyor: {name}')
        if item.flag_bits & 0x1:
            raise ValueError(f'ZIP şifreli üye içeriyor: {name}')
        if item.file_size > ZIP_MAX_SINGLE_MEMBER_BYTES:
            raise ValueError(f'ZIP üyesi boyut sınırını aşıyor: {name}')
        total += item.file_size
        if total > ZIP_MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError('ZIP toplam açılmış boyut sınırını aşıyor.')
    return members, total


def _zip_family_profile(path: Path):
    """Classify ZIP containers without confusing UDF, OOXML and ODF families."""
    try:
        with zipfile.ZipFile(path) as archive:
            members, total = _safe_zip_members(archive)
            lowered = {x.filename.replace('\\','/').lower(): x for x in members}
            names = set(lowered)
            bad = archive.testzip()
            if bad:
                return {'extension':'.zip','mode':'zip_crc_failed','valid':False,'errorCode':'ZIP_CRC_FAILED','detail':f'ZIP CRC doğrulaması başarısız: {bad}'}

            if '[content_types].xml' in names and 'word/document.xml' in names:
                return {'extension':'.docx','mode':'ooxml_docx','valid':True,'totalUncompressed':total}
            if '[content_types].xml' in names and 'xl/workbook.xml' in names:
                return {'extension':'.xlsx','mode':'ooxml_xlsx','valid':True,'totalUncompressed':total}
            if '[content_types].xml' in names and 'ppt/presentation.xml' in names:
                return {'extension':'.pptx','mode':'ooxml_pptx','valid':True,'totalUncompressed':total}

            if 'mimetype' in names:
                try:
                    mt = archive.read(lowered['mimetype'])[:256].decode('ascii','ignore').strip()
                except Exception:
                    mt = ''
                odf = {
                    'application/vnd.oasis.opendocument.text':('.odt','odf_text'),
                    'application/vnd.oasis.opendocument.spreadsheet':('.ods','odf_spreadsheet'),
                    'application/vnd.oasis.opendocument.presentation':('.odp','odf_presentation'),
                }.get(mt)
                if odf:
                    required = {'content.xml','meta.xml'}
                    if 'content.xml' not in names:
                        return {'extension':odf[0],'mode':odf[1],'valid':False,'errorCode':'ODF_STRUCTURE_INVALID','detail':'ODF content.xml içermiyor.'}
                    return {'extension':odf[0],'mode':odf[1],'valid':True,'totalUncompressed':total}

            if 'content.xml' in names:
                content = lowered['content.xml']
                if content.file_size <= 32 * 1024 * 1024:
                    try:
                        raw = archive.read(content)
                        is_udf, _ = _is_uyap_template_xml_bytes(raw)
                    except Exception:
                        is_udf = False
                    if is_udf:
                        return {'extension':'.udf','mode':'udf_zip_central_directory','valid':True,'totalUncompressed':total}
            return {'extension':'.zip','mode':'zip_central_directory','valid':True,'totalUncompressed':total}
    except (zipfile.BadZipFile, zipfile.LargeZipFile, RuntimeError, OSError, ValueError, NotImplementedError) as error:
        return {'extension':'.zip','mode':'zip_unreadable','valid':False,'errorCode':'ZIP_STRUCTURE_INVALID','detail':str(error)[:500]}

def _validate_raw_xml_udf(path: Path):
    """Validate the legacy/raw XML UDF variant returned by some Kurum UYAP records.

    Not every UDF is a ZIP container. Older/template-style UDF documents may be
    returned directly as UTF-8 XML with a <template format_id="..."> root. Accept
    only this specific UYAP shape, reject DTD/entity declarations, and keep the
    size bounded so arbitrary XML files are not silently treated as UDF.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return False, 'SIGNATURE_MISMATCH', 'UDF XML dosyasi okunamadi.', None
    if size <= 0 or size > 32 * 1024 * 1024:
        return False, 'UDF_LIMIT_EXCEEDED', 'UDF XML boyutu siniri asiyor.', None
    try:
        raw = path.read_bytes()
    except OSError:
        return False, 'SIGNATURE_MISMATCH', 'UDF XML dosyasi okunamadi.', None

    probe = raw.lstrip(b'\xef\xbb\xbf\x00\t\r\n ')
    if not probe.startswith((b'<?xml', b'<template', b'<TEMPLATE')):
        return False, 'SIGNATURE_MISMATCH', 'UDF gecerli bir UYAP XML sablonu degil.', None
    upper = raw.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
        return False, 'INVALID_UDF_XML', 'UDF XML varlik bildirimi kabul edilmiyor.', None

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False, 'INVALID_UDF_XML', 'UDF XML olarak okunamadi.', None

    if _xml_local_name(root.tag) != 'template':
        return False, 'SIGNATURE_MISMATCH', 'UDF XML kok elemani template degil.', None
    format_id = str(root.attrib.get('format_id') or root.attrib.get('formatId') or '').strip()
    if not format_id or len(format_id) > 32:
        return False, 'SIGNATURE_MISMATCH', 'UDF XML format kimligi bulunamadi.', None

    child_names = {_xml_local_name(child.tag) for child in list(root)}
    required = {'content', 'elements', 'styles'}
    if not required.issubset(child_names):
        return False, 'SIGNATURE_MISMATCH', 'UDF XML sablon yapisi eksik.', None

    return True, None, None, 'uyap_raw_xml_template_udf_v1'


def _inflate_raw_stream(stream, *, compressed_size=None, output_limit=ZIP_MAX_SINGLE_MEMBER_BYTES):
    """Inflate one raw-DEFLATE ZIP member with a strict output bound.

    Returns (payload, crc32, compressed_bytes, uncompressed_bytes). If
    compressed_size is None, consumption stops exactly at the DEFLATE EOF and
    rewinds any bytes read past it so the following data descriptor can be read.
    """
    inflater = zlib.decompressobj(-15)
    payload = bytearray()
    crc = 0
    compressed_count = 0
    uncompressed_count = 0
    remaining = compressed_size

    while True:
        if remaining is not None:
            if remaining <= 0:
                chunk = b''
            else:
                chunk = stream.read(min(64 * 1024, remaining))
                if not chunk:
                    raise EOFError('UDF sikistirilmis veri eksik.')
                remaining -= len(chunk)
        else:
            chunk = stream.read(64 * 1024)
            if not chunk:
                raise EOFError('UDF DEFLATE akisi tamamlanmadi.')

        before = len(chunk)
        out = inflater.decompress(chunk, max(0, output_limit - uncompressed_count + 1))
        consumed = before - len(inflater.unused_data)
        compressed_count += consumed
        uncompressed_count += len(out)
        if uncompressed_count > output_limit:
            raise OverflowError('UDF uye boyutu siniri asildi.')
        crc = zlib.crc32(out, crc)
        payload.extend(out)

        if inflater.eof:
            if inflater.unused_data:
                stream.seek(-len(inflater.unused_data), os.SEEK_CUR)
            break
        if remaining is not None and remaining <= 0:
            raise EOFError('UDF DEFLATE sonlandirma bilgisi eksik.')

    return bytes(payload), crc & 0xffffffff, compressed_count, uncompressed_count


def _read_streaming_udf(path: Path):
    """Validate UYAP's central-directory-less streaming UDF variant.

    UYAP can return .udf payloads that are structurally complete at the member
    level but intentionally omit the ZIP central directory. Each member is
    represented by a local file header and, when bit 3 is set, a data descriptor.
    We verify member names, compression, CRC, sizes, XML safety and total limits
    instead of silently accepting any PK-prefixed blob.
    """
    members = []
    total_uncompressed = 0
    content_xml = None
    file_size = path.stat().st_size

    with path.open('rb') as stream:
        while stream.tell() < file_size:
            sig = stream.read(4)
            if not sig:
                break
            if sig in (b'PK\x01\x02', b'PK\x05\x06'):
                # A central directory should have been handled by zipfile.ZipFile.
                raise zipfile.BadZipFile('Beklenmeyen ZIP merkezi dizini.')
            if sig != b'PK\x03\x04':
                raise zipfile.BadZipFile('UDF yerel dosya basligi bulunamadi.')

            header = stream.read(26)
            if len(header) != 26:
                raise EOFError('UDF yerel dosya basligi eksik.')
            (version_needed, flags, method, _mtime, _mdate, header_crc,
             header_csize, header_usize, name_len, extra_len) = struct.unpack('<HHHHHIIIHH', header)
            if flags & 0x0001:
                raise RuntimeError('UDF sifreli uye iceriyor.')
            if method not in (0, 8):
                raise RuntimeError(f'UDF desteklenmeyen sikistirma yontemi: {method}')
            if name_len <= 0 or name_len > 65535 or extra_len > 65535:
                raise zipfile.BadZipFile('UDF uye basligi gecersiz.')

            raw_name = stream.read(name_len)
            if len(raw_name) != name_len:
                raise EOFError('UDF uye adi eksik.')
            encoding = 'utf-8' if flags & 0x0800 else 'cp437'
            name = _safe_udf_member_name(raw_name.decode(encoding, errors='strict'))
            extra = stream.read(extra_len)
            if len(extra) != extra_len:
                raise EOFError('UDF extra alani eksik.')

            uses_descriptor = bool(flags & 0x0008)
            member_limit = 32 * 1024 * 1024 if name.lower() == 'content.xml' else ZIP_MAX_SINGLE_MEMBER_BYTES

            if method == 8:
                payload, actual_crc, actual_csize, actual_usize = _inflate_raw_stream(
                    stream,
                    compressed_size=None if uses_descriptor else header_csize,
                    output_limit=member_limit,
                )
            else:
                if uses_descriptor:
                    # Stored + descriptor has no intrinsic end marker. UYAP UDFs
                    # observed in production use DEFLATE; fail closed for this
                    # ambiguous variant rather than scanning binary content for a
                    # descriptor signature that could occur inside the payload.
                    raise RuntimeError('UDF stored+descriptor bicimi desteklenmiyor.')
                if header_usize > member_limit or header_csize > member_limit:
                    raise OverflowError('UDF uye boyutu siniri asildi.')
                payload = stream.read(header_csize)
                if len(payload) != header_csize:
                    raise EOFError('UDF uye verisi eksik.')
                actual_csize = len(payload)
                actual_usize = len(payload)
                actual_crc = zlib.crc32(payload) & 0xffffffff

            expected_crc = header_crc
            expected_csize = header_csize
            expected_usize = header_usize
            if uses_descriptor:
                first = stream.read(4)
                if len(first) != 4:
                    raise EOFError('UDF data descriptor eksik.')
                if first == b'PK\x07\x08':
                    descriptor = stream.read(12)
                    if len(descriptor) != 12:
                        raise EOFError('UDF data descriptor eksik.')
                    expected_crc, expected_csize, expected_usize = struct.unpack('<III', descriptor)
                else:
                    descriptor = stream.read(8)
                    if len(descriptor) != 8:
                        raise EOFError('UDF data descriptor eksik.')
                    expected_crc = struct.unpack('<I', first)[0]
                    expected_csize, expected_usize = struct.unpack('<II', descriptor)

            if expected_crc != actual_crc or expected_csize != actual_csize or expected_usize != actual_usize:
                raise zipfile.BadZipFile(f'UDF CRC/boyut dogrulamasi basarisiz: {name}')

            members.append(name)
            total_uncompressed += actual_usize
            if len(members) > ZIP_MAX_MEMBERS or total_uncompressed > 512 * 1024 * 1024:
                raise OverflowError('UDF acilmis boyut siniri asildi.')
            if name.lower() == 'content.xml':
                content_xml = payload

    lowered = [name.lower() for name in members]
    if len(set(lowered)) != len(lowered):
        raise ValueError('UDF paketinde tekrarlanan dosya adlari var.')
    if 'content.xml' not in lowered or content_xml is None:
        raise ValueError('UDF paketi content.xml icermiyor.')
    xml_probe = content_xml.replace(b'\x00', b'').upper()
    if b'<!DOCTYPE' in xml_probe or b'<!ENTITY' in xml_probe:
        raise ET.ParseError('UDF XML varlik bildirimi kabul edilmiyor.')
    is_udf_template, _ = _is_uyap_template_xml_bytes(content_xml)
    if not is_udf_template:
        raise ValueError('UDF content.xml UYAP template yapısında değil.')
    return {'members': members, 'totalUncompressed': total_uncompressed, 'mode': 'uyap_streaming_udf_v1'}


def _validate_udf_package(path: Path):
    """Return (ok, error_code, detail, mode) for ZIP, streamed or raw-XML UDF."""
    try:
        with zipfile.ZipFile(path) as archive:
            names = [name.lower() for name in archive.namelist()]
            if 'content.xml' not in names:
                return False, 'SIGNATURE_MISMATCH', 'UDF paketi content.xml icermiyor.', None
            members = archive.infolist()
            if len({member.filename.lower() for member in members}) != len(members):
                return False, 'INVALID_UDF_ENTRIES', 'UDF paketinde tekrarlanan dosya adlari var.', None
            if len(members) > ZIP_MAX_MEMBERS or sum(x.file_size for x in members) > 512 * 1024 * 1024:
                return False, 'UDF_LIMIT_EXCEEDED', 'UDF acilmis boyut sinirini asiyor.', None
            content = next(x for x in members if x.filename.lower() == 'content.xml')
            if content.file_size > 32 * 1024 * 1024:
                return False, 'UDF_LIMIT_EXCEEDED', 'UDF XML boyutu siniri asiyor.', None
            xml = archive.read(content)
            xml_probe = xml.replace(b'\x00', b'').upper()
            if b'<!DOCTYPE' in xml_probe or b'<!ENTITY' in xml_probe:
                return False, 'INVALID_UDF_XML', 'UDF XML varlik bildirimi kabul edilmiyor.', None
            is_udf_template, _ = _is_uyap_template_xml_bytes(xml)
            if not is_udf_template:
                return False, 'SIGNATURE_MISMATCH', 'content.xml UYAP template yapısında değil.', None
            bad_member = archive.testzip()
            if bad_member:
                return False, 'SIGNATURE_MISMATCH', f'UDF paketi bozuk: {bad_member}', None
        return True, None, None, 'zip_central_directory'
    except zipfile.BadZipFile:
        pass
    except ET.ParseError:
        return False, 'INVALID_UDF_XML', 'UDF content.xml XML olarak okunamadi.', None
    except (RuntimeError, EOFError, OSError, ValueError):
        return False, 'SIGNATURE_MISMATCH', 'UDF paketi yapisal olarak dogrulanamadi.', None

    raw_xml_ok, raw_xml_error, raw_xml_detail, raw_xml_mode = _validate_raw_xml_udf(path)
    if raw_xml_ok:
        return True, None, None, raw_xml_mode
    try:
        raw_head = path.read_bytes()[:8192].lstrip(b'\xef\xbb\xbf\x00\t\r\n ')
    except OSError:
        raw_head = b''
    if raw_head.startswith((b'<?xml', b'<template', b'<TEMPLATE')):
        return False, raw_xml_error or 'SIGNATURE_MISMATCH', raw_xml_detail or 'UDF XML yapisal olarak dogrulanamadi.', None

    try:
        profile = _read_streaming_udf(path)
        return True, None, None, profile.get('mode')
    except OverflowError:
        return False, 'UDF_LIMIT_EXCEEDED', 'UDF acilmis boyut sinirini asiyor.', None
    except ET.ParseError:
        return False, 'INVALID_UDF_XML', 'UDF content.xml XML olarak okunamadi.', None
    except ValueError as error:
        detail = str(error)
        if 'tekrarlanan' in detail:
            return False, 'INVALID_UDF_ENTRIES', detail, None
        if 'content.xml' in detail:
            return False, 'SIGNATURE_MISMATCH', detail, None
        return False, 'SIGNATURE_MISMATCH', detail, None
    except (zipfile.BadZipFile, RuntimeError, EOFError, OSError, UnicodeError, zlib.error):
        return False, 'SIGNATURE_MISMATCH', 'UDF gecerli bir UYAP UDF paketi degil.', None

SUPPORTED_DOCUMENT_EXTENSIONS = {'.pdf','.udf','.tif','.tiff','.zip','.html','.htm','.jpg','.jpeg','.png','.gif','.bmp','.webp','.svg','.xml','.rtf','.doc','.xls','.ppt','.docx','.xlsx','.pptx','.odt','.ods','.odp','.7z','.rar'}


def _extension_hint(*values):
    """Return a normalized document extension hint without trusting it as proof.

    UYAP Kurum commonly downloads files as application/download and quarantine
    intentionally renames rejected bytes to .invalid.  The hint is therefore
    used only to choose the correct structural validator when magic bytes show
    a compatible container (not to accept content by filename alone).
    """
    for value in values:
        text = str(value or '').strip().replace('\\', '/')
        if not text:
            continue
        if text.startswith('.') and '/' not in text:
            suffix = text.lower()
        else:
            suffix = Path(text).suffix.lower()
        if suffix in SUPPORTED_DOCUMENT_EXTENSIONS:
            return '.jpg' if suffix == '.jpeg' else '.tif' if suffix == '.tiff' else suffix
    return None


def detect_content_profile(path: Path, hint=None):
    """Classify downloaded bytes by structure; filename/MIME are hints only."""
    normalized_hint = _extension_hint(hint)
    profile = {'extension':'.bin','mode':'unknown','hint':normalized_hint,'udfProbeError':None}
    if not path.is_file():
        return {**profile,'mode':'missing'}
    with path.open('rb') as stream:
        head = stream.read(16384)

    if head.startswith(b'\xff\xd8\xff'): return {**profile,'extension':'.jpg','mode':'magic_jpeg'}
    if head.startswith(b'\x89PNG\r\n\x1a\n'): return {**profile,'extension':'.png','mode':'magic_png'}
    if head.startswith((b'GIF87a',b'GIF89a')): return {**profile,'extension':'.gif','mode':'magic_gif'}
    if head.startswith(b'BM'): return {**profile,'extension':'.bmp','mode':'magic_bmp'}
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP': return {**profile,'extension':'.webp','mode':'magic_webp'}
    if is_html(head): return {**profile,'extension':'.html','mode':'html_signature'}
    if head.startswith(b'%PDF-'): return {**profile,'extension':'.pdf','mode':'magic_pdf'}
    if head.startswith((b'II*\x00',b'MM\x00*')): return {**profile,'extension':'.tif','mode':'magic_tiff'}
    if head.startswith((b'II+\x00',b'MM\x00+')): return {**profile,'extension':'.tif','mode':'magic_bigtiff'}
    if head.startswith(b'{\\rtf'): return {**profile,'extension':'.rtf','mode':'magic_rtf'}
    if head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
        ext = normalized_hint if normalized_hint in {'.doc','.xls','.ppt'} else '.bin'
        return {**profile,'extension':ext,'mode':'ole_compound_hint' if ext != '.bin' else 'ole_compound_unknown'}
    if head.startswith(b'7z\xbc\xaf\x27\x1c'): return {**profile,'extension':'.7z','mode':'magic_7z'}
    if head.startswith((b'Rar!\x1a\x07\x00',b'Rar!\x1a\x07\x01\x00')): return {**profile,'extension':'.rar','mode':'magic_rar'}

    xml_probe = head.lstrip(b'\xef\xbb\xbf\x00\t\r\n ')
    if xml_probe.startswith((b'<?xml',b'<template',b'<TEMPLATE',b'<svg',b'<SVG')):
        raw_ok, raw_error, raw_detail, raw_mode = _validate_raw_xml_udf(path)
        if raw_ok:
            return {**profile,'extension':'.udf','mode':raw_mode or 'uyap_raw_xml_template_udf_v1'}
        profile['udfProbeError'] = raw_error or raw_detail
        try:
            raw = path.read_bytes() if path.stat().st_size <= 32*1024*1024 else b''
            upper = raw.replace(b'\x00',b'').upper()
            if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper:
                return {**profile,'extension':'.xml','mode':'xml_unsafe_declaration'}
            root = ET.fromstring(raw)
            if _xml_local_name(root.tag) == 'svg':
                return {**profile,'extension':'.svg','mode':'xml_svg'}
            return {**profile,'extension':'.xml','mode':'generic_xml'}
        except Exception:
            if normalized_hint == '.udf':
                return {**profile,'extension':'.udf','mode':'raw_xml_udf_hint_structural_probe'}

    if head.startswith(b'PK\x03\x04'):
        family = _zip_family_profile(path)
        if family.get('valid'):
            return {**profile, **{k:v for k,v in family.items() if k!='valid'}}
        try:
            udf_ok, udf_error, udf_detail, udf_mode = _validate_udf_package(path)
            if udf_ok:
                return {**profile,'extension':'.udf','mode':udf_mode or 'uyap_streaming_udf_v1'}
            profile['udfProbeError'] = udf_error or udf_detail
        except Exception as error:
            profile['udfProbeError'] = str(error)[:500]
        return {**profile,'extension':family.get('extension') or '.zip','mode':family.get('mode') or 'pk_local_header_no_central_directory'}
    return profile


def detect_extension(path: Path, hint=None):
    return detect_content_profile(path, hint).get('extension') or '.bin'




def canonical_source_id(document, *filenames):
    """Return the numeric UYAP source id when it can be proven from metadata/name."""
    value = str((document or {}).get("sourceDocumentId") or "").strip()
    if re.fullmatch(r"\d{4,30}", value):
        return value
    candidates = [
        (document or {}).get("originalFilename"),
        (document or {}).get("browserFilename"),
        *filenames,
    ]
    for candidate in candidates:
        name = Path(str(candidate or "").replace("\\", "/")).name
        match = re.fullmatch(r"evrak[_ -]?(\d{4,30})(?: \(\d+\))?\.(?:pdf|udf|uyap|zip|html?|tiff?|tif|jpe?g|png|gif|bmp|webp|svg|xml|rtf|docx?|xlsx?|pptx?|odt|ods|odp|7z|rar|bin|part)", name, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def canonical_target(target: Path, document, extension: str, source_id=None):
    """Finalize the archive filename after content type/source id are known."""
    extension = extension if str(extension).startswith('.') else f'.{extension}'
    source_id = source_id or canonical_source_id(document)
    target = target.with_suffix(extension)
    if not source_id:
        return target

    document_id = str((document or {}).get("documentId") or "").strip()
    stem = target.stem
    if document_id and stem.endswith(f"_{document_id}"):
        stem = stem[:-(len(document_id) + 1)]
    # Avoid duplicating an id already present in a correctly rebuilt JS target.
    if not stem.endswith(f"_{source_id}"):
        stem = f"{stem}_{source_id}"
    return target.with_name(f"{stem}{extension}")

def validate_pdf_structure(path: Path):
    """Validate the PDF terminator and xref pointer without rewriting source bytes.

    Older builds only searched the last 4 KiB for one exact regex. Real-world
    producers can leave a larger (but still harmless) whitespace tail or use an
    xref stream instead of a classic xref table. We accept both standard xref
    forms, while still rejecting missing EOF/startxref, out-of-range pointers and
    non-whitespace payload appended after the final %%EOF marker.
    """
    size = path.stat().st_size
    diag = {
        "pdfSize": size,
        "pdfTailWindow": min(size, 256 * 1024),
        "pdfEofOffset": None,
        "pdfTrailingBytes": None,
        "pdfStartXref": None,
        "pdfXrefKind": None,
    }
    if size <= 0:
        return False, "EMPTY_FILE", "Dosya sıfır bayt.", diag

    tail_window = min(size, 256 * 1024)
    with path.open("rb") as stream:
        stream.seek(max(0, size - tail_window))
        tail = stream.read(tail_window)

    eof_matches = list(re.finditer(rb"%%EOF", tail))
    if not eof_matches:
        return False, "TRUNCATED_PDF", "PDF %%EOF sonlandırma işareti bulunamadı.", diag

    eof_match = eof_matches[-1]
    eof_abs = size - tail_window + eof_match.start()
    trailing = tail[eof_match.end():]
    diag["pdfEofOffset"] = eof_abs
    diag["pdfTrailingBytes"] = len(trailing)

    # PDF readers commonly tolerate whitespace/NUL padding after %%EOF.
    # UYAP's Style Report producer also appends the deterministic DOS/EOT
    # control suffix 0x1A 0x04 after a valid %%EOF marker. Field diagnostics
    # across 123 rejected samples showed the same 3-byte tail shape
    # (newline + two non-whitespace control bytes), and a captured sample is
    # accepted by strict pypdf, PyMuPDF and pdfinfo. Accept ONLY this exact
    # control suffix (optionally surrounded by ordinary PDF whitespace/NUL).
    # Any other appended payload remains fail-closed to avoid accepting
    # polyglot/concatenated responses.
    trailing_core = trailing.strip(b" \t\r\n\f\x00")
    diag["pdfTrailingCoreHex"] = trailing_core.hex()
    diag["pdfTrailingKind"] = "whitespace"
    diag["pdfTrailingWarning"] = None
    if trailing_core:
        if trailing_core == b"\x1a\x04":
            diag["pdfTrailingKind"] = "uyap_control_suffix_1a04"
        elif trailing_core == b"%Avision":
            # Some scanner-produced UYAP PDFs (Producer: Avision) append the
            # exact ASCII vendor comment ``%Avision`` after the final %%EOF.
            # Keep the source bytes untouched and accept ONLY this exact suffix.
            # The normal startxref/xref checks below still have to pass, so this
            # does not turn arbitrary trailing payloads into valid PDFs.
            diag["pdfTrailingKind"] = "uyap_avision_vendor_comment_suffix"
            diag["pdfTrailingWarning"] = "Avision tarayıcı PDF'si final %%EOF sonrasında bilinen %Avision üretici yorumunu içeriyor; kaynak baytları değiştirilmeden arşivlendi."
        elif (
            len(trailing_core) <= 256
            and re.fullmatch(rb"%[A-Za-z][A-Za-z0-9 ._:/()+\-]{0,254}", trailing_core)
            and not re.search(rb"(?i)(?:%PDF|%%EOF|startxref|javascript|<script|<html|<!doctype|/Launch|/OpenAction)", trailing_core)
        ):
            # PDF comments start with '%' and terminate at the line boundary.
            # Some UYAP scanner/report producers append one short printable
            # producer comment after an otherwise final %%EOF. Accept exactly
            # one bounded ASCII comment only after the xref validation below
            # succeeds. Nested documents and active-content markers stay
            # fail-closed.
            diag["pdfTrailingKind"] = "pdf_producer_comment_suffix"
            diag["pdfTrailingWarning"] = "PDF üreticisi final %%EOF sonrasına tek satırlık zararsız üretici yorumu ekledi; kaynak baytları değiştirilmeden arşivlendi."
        else:
            # Kurum UYAP / Belgenet occasionally appends a UTF-8 diagnostic
            # sentence *after* an otherwise complete PDF's final %%EOF.
            # Preserve the source bytes exactly, but accept only the narrowly
            # recognized upstream diagnostic shape after the PDF body itself
            # passes the normal startxref/xref structural checks below.
            # This avoids false PDF_TRAILING_DATA failures without weakening
            # the fail-closed rule for arbitrary/polyglot trailing payloads.
            belgenet_prefix = (
                "İlgili dokümanın Belgenet sunucusu üzerinden indirilmesi esnasında "
                "hata meydana gelmiştir. Hata:"
            )
            try:
                trailing_text = trailing_core.decode("utf-8")
            except UnicodeDecodeError:
                trailing_text = None
            if (
                trailing_text
                and len(trailing_core) <= 4096
                and trailing_text.startswith(belgenet_prefix)
                and "\x00" not in trailing_text
            ):
                diag["pdfTrailingKind"] = "uyap_belgenet_diagnostic_suffix"
                diag["pdfTrailingWarning"] = "UYAP/Belgenet PDF sonuna tanılama metni ekledi; PDF gövdesi yapısal olarak geçerli, kaynak baytları değiştirilmeden arşivlendi."
            else:
                diag["pdfTrailingKind"] = "unexpected"
                return False, "PDF_TRAILING_DATA", "PDF sonlandırmasından sonra beklenmeyen veri bulundu.", diag

    before_eof = tail[:eof_match.start()]
    startxref_matches = list(re.finditer(rb"startxref\s+(\d+)", before_eof))
    if not startxref_matches:
        return False, "TRUNCATED_PDF", "PDF startxref kaydı bulunamadı.", diag

    startxref_match = startxref_matches[-1]
    try:
        xref_offset = int(startxref_match.group(1))
    except (TypeError, ValueError):
        return False, "TRUNCATED_PDF", "PDF startxref değeri okunamadı.", diag
    diag["pdfStartXref"] = xref_offset

    if xref_offset < 0 or xref_offset >= size:
        return False, "TRUNCATED_PDF", "PDF startxref dosya sınırları dışında.", diag

    with path.open("rb") as stream:
        stream.seek(xref_offset)
        probe = stream.read(8192)
    stripped = probe.lstrip(b" \t\r\n\f\x00")

    if stripped.startswith(b"xref"):
        diag["pdfXrefKind"] = "table"
        return True, None, None, diag

    # PDF 1.5+ can store the cross-reference table as a stream object.
    # startxref then points to an indirect object containing /Type /XRef.
    if re.match(rb"\d+\s+\d+\s+obj\b", stripped[:128]) and re.search(
        rb"/Type\s*/XRef\b", stripped[:8192]
    ):
        diag["pdfXrefKind"] = "stream"
        return True, None, None, diag

    return False, "TRUNCATED_PDF", "PDF startxref geçerli bir xref tablosuna/akışına işaret etmiyor.", diag


def validate_content(path: Path, expected_extension: str, html_context=None):
    if not path.exists() or not path.is_file():
        return False, 'FILE_NOT_CONFIRMED', 'İndirilen dosya diskte bulunamadı.'
    if path.stat().st_size == 0:
        return False, 'EMPTY_FILE', 'Dosya sıfır bayt.'
    with path.open('rb') as stream:
        head = stream.read(16384)
    extension = str(expected_extension or '').lower()

    if is_html(head):
        html_profile = classify_html_payload(path, html_context)
        if extension in ('.html','.htm') and html_profile.get('valid'):
            return True, None, None
        if html_profile.get('kind') == 'error_response':
            return False, 'HTML_RESPONSE', 'UYAP oturum/hata HTML yanıtı döndürdü.'
        return False, 'HTML_RESPONSE', 'HTML içerik gerçek evrak olarak doğrulanamadı.'

    if extension == '.pdf':
        if not head.startswith(b'%PDF-'): return False,'SIGNATURE_MISMATCH','PDF imzası bulunamadı.'
        ok, code, detail, _ = validate_pdf_structure(path); return ok, code, detail
    if extension in ('.tif','.tiff'):
        if head.startswith((b'II+\x00',b'MM\x00+')):
            # BigTIFF header: endian, magic=43, offset-size=8, reserved=0, first IFD offset (8 bytes)
            endian = '<' if head[:2] == b'II' else '>'
            if len(head) < 16: return False,'TRUNCATED_TIFF','BigTIFF başlığı eksik.'
            offset_size, reserved = struct.unpack(endian+'HH',head[4:8])
            offset = struct.unpack(endian+'Q',head[8:16])[0]
            if offset_size != 8 or reserved != 0 or offset < 16 or offset + 8 > path.stat().st_size:
                return False,'TRUNCATED_TIFF','BigTIFF ilk görüntü dizini geçersiz.'
            return True,None,None
        if not head.startswith((b'II*\x00',b'MM\x00*')): return False,'SIGNATURE_MISMATCH','TIFF imzası bulunamadı.'
        if len(head) < 8: return False,'TRUNCATED_TIFF','TIFF başlığı eksik.'
        endian = '<' if head[:2] == b'II' else '>'
        offset = struct.unpack(endian+'I',head[4:8])[0]
        if offset < 8 or offset + 2 > path.stat().st_size: return False,'TRUNCATED_TIFF','TIFF ilk görüntü dizini dosya sınırları dışında.'
        with path.open('rb') as stream:
            stream.seek(offset); raw_count=stream.read(2)
        if len(raw_count)!=2: return False,'TRUNCATED_TIFF','TIFF görüntü dizini eksik.'
        count=struct.unpack(endian+'H',raw_count)[0]
        if not count or offset+2+count*12+4>path.stat().st_size: return False,'TRUNCATED_TIFF','TIFF görüntü dizini eksik.'
        return True,None,None
    if extension == '.udf':
        ok, code, detail, _ = _validate_udf_package(path); return ok, code, detail
    if extension in _IMAGE_EXTENSIONS:
        image_profile=classify_image_payload(path, html_context)
        if not image_profile.get('valid'): return False,'INVALID_IMAGE','Görüntü dosyası yapısal olarak doğrulanamadı.'
        detected=image_profile.get('extension')
        if extension in ('.jpg','.jpeg') and detected=='.jpg': return True,None,None
        if detected!=extension: return False,'SIGNATURE_MISMATCH','Görüntü uzantısı gerçek içerik imzasıyla uyuşmuyor.'
        return True,None,None
    if extension == '.webp':
        return (True,None,None) if head.startswith(b'RIFF') and head[8:12]==b'WEBP' else (False,'SIGNATURE_MISMATCH','WebP imzası bulunamadı.')
    if extension == '.rtf':
        if not head.startswith(b'{\\rtf'): return False,'SIGNATURE_MISMATCH','RTF imzası bulunamadı.'
        if path.stat().st_size < 7: return False,'TRUNCATED_RTF','RTF dosyası eksik.'
        return True,None,None
    if extension in {'.doc','.xls','.ppt'}:
        return (True,None,None) if head.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1') else (False,'SIGNATURE_MISMATCH','OLE belge imzası bulunamadı.')
    if extension in {'.docx','.xlsx','.pptx','.odt','.ods','.odp','.zip'}:
        family=_zip_family_profile(path)
        if not family.get('valid'): return False,family.get('errorCode') or 'ZIP_STRUCTURE_INVALID',family.get('detail') or 'ZIP yapısı doğrulanamadı.'
        if family.get('extension') != extension:
            return False,'SIGNATURE_MISMATCH',f"ZIP konteyneri {family.get('extension')} olarak tanındı; beklenen {extension}."
        return True,None,None
    if extension in {'.xml','.svg'}:
        try:
            raw=path.read_bytes()
            upper=raw.replace(b'\x00',b'').upper()
            if b'<!DOCTYPE' in upper or b'<!ENTITY' in upper: return False,'INVALID_XML','XML varlık/DTD bildirimi kabul edilmiyor.'
            root=ET.fromstring(raw)
            if extension=='.svg' and _xml_local_name(root.tag)!='svg': return False,'SIGNATURE_MISMATCH','SVG kök elemanı bulunamadı.'
            if extension=='.xml' and _xml_local_name(root.tag)=='template' and _is_uyap_template_xml_bytes(raw)[0]: return False,'SIGNATURE_MISMATCH','UYAP template XML, UDF olarak sınıflandırılmalıdır.'
            return True,None,None
        except (ET.ParseError,OSError): return False,'INVALID_XML','XML yapısal olarak doğrulanamadı.'
    if extension == '.7z':
        return (True,None,None) if head.startswith(b'7z\xbc\xaf\x27\x1c') else (False,'SIGNATURE_MISMATCH','7z imzası bulunamadı.')
    if extension == '.rar':
        return (True,None,None) if head.startswith((b'Rar!\x1a\x07\x00',b'Rar!\x1a\x07\x01\x00')) else (False,'SIGNATURE_MISMATCH','RAR imzası bulunamadı.')
    return False,'UNSUPPORTED_FORMAT','Dosya türü için doğrulayıcı bulunmuyor; otomatik doğrulanmadı.'


def _staging_parts(staging_path: str):
    if not isinstance(staging_path, str) or "\\" in staging_path:
        raise ValueError("Geçersiz geçici dosya yolu")
    parts = PurePosixPath(staging_path).parts
    if len(parts) != 4 or parts[:2] != ("UYAP_Arsiv", "_incoming") or ".." in parts:
        raise ValueError("Kaynak yalnızca UYAP geçici alanından alınabilir")
    return parts


def _lexical_abs(path: Path):
    """Absolute path normalization that does not require the path to exist.

    Chrome download history can still point at the staging filename after the
    native agent has already moved the bytes. Windows Path.resolve() may raise
    WinError 3 for that perfectly recoverable situation, especially under
    redirected/OneDrive-backed Desktop folders. Keep the security comparison
    lexical and case-normalized instead of requiring the directory to exist.
    """
    return Path(os.path.abspath(os.path.expanduser(str(path))))


def downloads_root(source: Path, staging_path: str):
    parts = _staging_parts(staging_path)
    current = source
    for _ in parts:
        current = current.parent
    root = _lexical_abs(current)
    expected_parent = _lexical_abs(root.joinpath(*parts[:-1]))
    source_parent = _lexical_abs(source.parent)
    if os.path.normcase(str(source_parent)) != os.path.normcase(str(expected_parent)) or not re.fullmatch(
            re.escape(Path(parts[-1]).stem) + r"(?: \(\d+\))?\.part", source.name):
        raise ValueError("Kaynak geçici kayıtla eşleşmiyor")
    return root


def direct_download_evidence(message):
    document = message.get("document") or {}
    download_id = message.get("browserDownloadId")
    return bool(
        message.get("directDownloadEvidence") is True
        and type(download_id) is int
        and download_id == document.get("downloadId")
        and document.get("bindingMethod") == "extension-download-id"
        and message.get("browserState") == "complete"
        and message.get("attemptId")
        and message.get("attemptId") == document.get("attemptId")
        and message.get("sessionId") == document.get("sessionId")
    )


def normalize_source_to_staging(source: Path, message):
    staging_path = message["stagingPath"]
    try:
        detected_root = downloads_root(source, staging_path)
        selected_root = load_selected_archive_root() if message.get("useSelectedArchiveRoot") is True else None
        if selected_root is None or os.path.normcase(str(selected_root)) == os.path.normcase(str(detected_root)):
            return source, detected_root
        parts = _staging_parts(staging_path)
        selected_staging = selected_root.joinpath(*parts).resolve()
        if selected_root != selected_staging and selected_root not in selected_staging.parents:
            raise ValueError("Seçili arşiv geçici hedefi güvenli değil")
        selected_staging.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file():
            if selected_staging.is_file():
                return selected_staging, selected_root
            raise FileNotFoundError(str(source))
        if selected_staging.exists():
            source_digest = sha256_file(source)
            if sha256_file(selected_staging) == source_digest:
                source.unlink(missing_ok=True)
                return selected_staging, selected_root
            collision = selected_staging.with_name(f"{selected_staging.stem}__portable_{source_digest[:16]}{selected_staging.suffix}")
            if collision.exists() and sha256_file(collision) != source_digest:
                collision = selected_staging.with_name(f"{selected_staging.stem}__portable_{source_digest[:16]}_{uuid.uuid4().hex}{selected_staging.suffix}")
            if collision.exists():
                source.unlink(missing_ok=True)
            else:
                os.replace(source, collision)
            return collision, selected_root
        os.replace(source, selected_staging)
        return selected_staging, selected_root
    except ValueError:
        pass

    if message.get("allowUnmatchedAdoption"):
        try:
            session_id = str(message.get("sessionId") or "").strip()
            attempt_id = str(message.get("attemptId") or "").strip()
            provisional_rel = str(message.get("provisionalUnmatchedPath") or "").replace("\\", "/")
            provisional_parts = PurePosixPath(provisional_rel).parts
            if (
                len(provisional_parts) == 4
                and provisional_parts[:2] == ("UYAP_Arsiv", "_unmatched")
                and provisional_parts[2] == session_id
                and provisional_parts[3].startswith(attempt_id + "__")
                and source.is_file()
                and source.name == provisional_parts[3]
                and source.parent.name == session_id
                and source.parent.parent.name == "_unmatched"
                and source.parent.parent.parent.name == "UYAP_Arsiv"
            ):
                root = source.parent.parent.parent.parent.resolve()
                staging_parts = _staging_parts(staging_path)
                staging = root.joinpath(*staging_parts).resolve()
                if root != staging and root not in staging.parents:
                    raise ValueError("Geçici hedef indirme kökünün dışında")
                staging.parent.mkdir(parents=True, exist_ok=True)
                if staging.exists():
                    staging.unlink()
                os.replace(source, staging)
                return staging, root
        except (OSError, ValueError):
            pass

    if not message.get("allowCanonicalAdoption"):
        raise ValueError("Kaynak yalnızca UYAP geçici alanından alınabilir")

    document = message.get("document") or {}
    if document.get("bindingMethod") == "extension-download-id" and not direct_download_evidence(message):
        raise ValueError("Doğrudan indirme kimliği kanıtlanamadı")
    source_id = canonical_source_id(document, source.name)
    lease_adoption = bool(message.get("allowLeaseAdoption")) and str(document.get("bindingMethod") or "").startswith("native-uyap-single-candidate-lease")
    if not source_id and not lease_adoption:
        raise ValueError("Dış kaynak evrak kimliği doğrulanamadı")
    if source_id:
        # Preserve the identity learned from the canonical UYAP filename for final naming.
        document["sourceDocumentId"] = source_id

    basename = source.name
    exact_browser_adoption = direct_download_evidence(message)
    if source.is_symlink():
        raise ValueError("Kaynak sembolik bağlantı olamaz")
    if source_id:
        pattern = rf"evrak[_ -]?{re.escape(source_id)}(?: \(\d+\))?\.(?:pdf|udf|uyap|zip|html?|tiff?|jpe?g|png|gif|bmp|webp|svg|xml|rtf|docx?|xlsx?|pptx?|odt|ods|odp|7z|rar|bin)"
        if not re.fullmatch(pattern, basename, flags=re.IGNORECASE):
            # RC15.9.15: Kurum/Chrome can persist a non-canonical local filename
            # even when the extension has already bound this exact DownloadItem
            # to this exact attempt/session. In that case the browser download ID
            # is stronger provenance than the basename. Never apply this bypass to
            # lease-only or ambiguous candidates.
            if not exact_browser_adoption:
                raise ValueError("Dış kaynak dosya adı UYAP evrak kimliğiyle eşleşmiyor")
            if not basename or basename in ('.', '..'):
                raise ValueError("Doğrudan bağlanan dosya adı geçersiz")
    elif lease_adoption:
        # The extension proved there was exactly one new UYAP-origin browser
        # download inside this attempt lease. Content is still validated below
        # before anything can become VERIFIED.
        if not basename or basename in ('.', '..'):
            raise ValueError("Lease ile bağlanan dosya adı geçersiz")

    parts = _staging_parts(staging_path)
    selected_root = load_selected_archive_root() if message.get("useSelectedArchiveRoot") is True and exact_browser_adoption else None
    root = selected_root or source.parent.resolve()
    staging = root.joinpath(*parts).resolve()
    if root != staging and root not in staging.parents:
        raise ValueError("Geçici hedef indirme kökünün dışında")
    staging.parent.mkdir(parents=True, exist_ok=True)
    if staging.exists():
        # Never overwrite a different staged document on a retry. RC15.9.15
        # additionally recovers an exact Chrome DownloadItem collision by moving
        # the newly-bound bytes to a sibling staging file. Both byte streams are
        # preserved, and only the exact browser-bound source continues to content
        # validation. Lease/ambiguous adoption remains fail-closed.
        existing_digest = sha256_file(staging)
        source_digest = sha256_file(source)
        if existing_digest != source_digest:
            if not exact_browser_adoption:
                raise ValueError("Geçici hedefte farklı içerik mevcut; iki dosya da korundu")
            collision = staging.with_name(f"{staging.stem}__bound_{source_digest[:16]}{staging.suffix}")
            if collision.exists():
                if sha256_file(collision) == source_digest:
                    source.unlink(missing_ok=True)
                    return collision, root
                collision = staging.with_name(f"{staging.stem}__bound_{source_digest[:16]}_{uuid.uuid4().hex}{staging.suffix}")
            os.replace(source, collision)
            return collision, root
        return staging, root
    os.replace(source, staging)
    return staging, root


def safe_target(root: Path, relative_path: str):
    parts = PurePosixPath(relative_path).parts
    if not parts or PurePosixPath(relative_path).is_absolute() or any(x in ("..", ".") or ":" in x or "\\" in x for x in parts):
        raise ValueError("Geçersiz göreli hedef yolu")
    target = (root / Path(*parts)).resolve()
    if root != target and root not in target.parents:
        raise ValueError("Hedef yol indirme kökünün dışında")
    return target


# Archive target path budget. Windows long-path support is opt-in (registry
# LongPathsEnabled plus an app manifest) and is off on typical user PCs. Even
# when the agent could write a longer path, Explorer, the UYAP document editor
# and ZIP extraction commonly cannot open it. Every archive file therefore
# stays inside the classic MAX_PATH budget, measured in UTF-16 code units.
TARGET_PATH_MAX_CHARS = 259  # MAX_PATH (260) minus the terminating NUL
TARGET_DIR_MAX_CHARS = 247   # CreateDirectoryW limit: MAX_PATH - 12, minus NUL
TARGET_TITLE_MIN_CHARS = 12
_TARGET_ID_SUFFIX = re.compile(r"_(?:d-[a-f0-9]{12,128}|\d{4,30})$", re.IGNORECASE)


class TargetPathTooLong(Exception):
    def __init__(self, diagnostics):
        super().__init__("TARGET_PATH_TOO_LONG")
        self.diagnostics = diagnostics


def _win_path_len(value) -> int:
    return len(str(value).encode("utf-16-le")) // 2


def _fit_target_path_budget(target: Path, reserve: int = 0):
    """Keep an absolute archive target inside the Windows path budget.

    A target that already fits is returned unchanged, so every previously
    archived document keeps its exact name. Only an over-budget target is
    shortened, and only in its descriptive title: the trailing UYAP/internal id
    and the extension carry identity and are never cut. The result is
    deterministic for a given archive root, so a retry lands on the same name.
    """
    limit = TARGET_PATH_MAX_CHARS - max(0, int(reserve))
    directory_len = _win_path_len(target.parent)
    original_len = _win_path_len(target)

    def diagnostics(part):
        return {
            "targetPathLength": original_len,
            "targetDirLength": directory_len,
            "targetPathLimit": limit,
            "targetPathOverflowPart": part,
        }

    if directory_len > TARGET_DIR_MAX_CHARS:
        raise TargetPathTooLong(diagnostics("directory"))
    if original_len <= limit:
        return target, None

    stem, suffix = target.stem, target.suffix
    match = _TARGET_ID_SUFFIX.search(stem)
    identity_tail = match.group(0) if match else ""
    title = stem[: len(stem) - len(identity_tail)] if identity_tail else stem
    fitted = target
    while _win_path_len(fitted) > limit:
        overflow = _win_path_len(fitted) - limit
        title = title[: max(0, len(title) - overflow)].rstrip(" ._-")
        if len(title) < TARGET_TITLE_MIN_CHARS:
            raise TargetPathTooLong(diagnostics("filename"))
        fitted = target.with_name(f"{title}{identity_tail}{suffix}")
    return fitted, {
        "targetPathShortened": True,
        "targetPathOriginalLength": original_len,
        "targetPathLength": _win_path_len(fitted),
        "targetDirLength": directory_len,
        "targetPathLimit": limit,
    }


def _versioned_target(target: Path):
    """Collision-safe sibling name that still fits the path budget."""
    marker = f"__v{uuid.uuid4().hex}"
    base, _ = _fit_target_path_budget(target, reserve=len(marker))
    return base.with_name(f"{base.stem}{marker}{base.suffix}")


def _target_path_too_long_payload(diagnostics):
    return {
        "ok": True, "valid": False, "errorCode": "TARGET_PATH_TOO_LONG",
        "detail": ("Evrak eksiksiz indirildi ancak arşiv klasörünün yolu Windows'un 260 karakter "
                   "sınırını aşıyor; yeniden indirmek sorunu çözmez. Daha kısa bir arşiv konumu seçin."),
        "retryHint": None, "sourcePreserved": True, **(diagnostics or {}),
    }


def _file_error_diagnostics(error):
    names = [getattr(error, "filename", None), getattr(error, "filename2", None)]
    lengths = [_win_path_len(name) for name in names if name]
    return {
        "fileErrorWinError": getattr(error, "winerror", None),
        "fileErrorErrno": getattr(error, "errno", None),
        "fileErrorPathLength": max(lengths) if lengths else None,
    }


def _target_unavailable_payload(error, source: Path, target: Path):
    target_len = _win_path_len(target)
    too_long = target_len > TARGET_PATH_MAX_CHARS or _win_path_len(target.parent) > TARGET_DIR_MAX_CHARS
    return {
        "ok": True, "valid": False,
        "errorCode": "TARGET_PATH_TOO_LONG" if too_long else "TARGET_PATH_UNAVAILABLE",
        "detail": ("Evrak eksiksiz indirildi ancak arşiv hedef klasörüne yazılamadı; "
                   "yeniden indirmek sorunu çözmez. Arşiv konumunu ve klasör erişimini kontrol edin."),
        "retryHint": None, "sourcePreserved": source.is_file(),
        "targetPathLength": target_len,
        "targetDirLength": _win_path_len(target.parent),
        "targetPathLimit": TARGET_PATH_MAX_CHARS,
        **_file_error_diagnostics(error),
    }



def _identity_text(value):
    value = html.unescape(str(value or ""))
    return re.sub(r"\s+", " ", value).strip().casefold()


def _canonical_case_id(value):
    """Normalize harmless UYAP case separators without conflating fallback scopes."""
    raw = html.unescape(str(value or "")).strip()
    match = re.fullmatch(r"(20\d{2})[\s_/-]+(\d{1,10})(?:[\s_-]+(?:dosya|esas))?", raw, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1)}/{int(match.group(2))}"
    return _identity_text(raw)


def _case_id_variants(value):
    canonical = _canonical_case_id(value)
    match = re.fullmatch(r"(20\d{2})/(\d{1,10})", canonical)
    if not match:
        return [str(value or "").strip()] if str(value or "").strip() else []
    year, number = match.groups()
    return list(dict.fromkeys([
        str(value or "").strip(), canonical, f"{year}_{number}", f"{year}-{number}",
        f"{year}_{number}_Dosya", f"{year}_{number}_Esas",
    ]))


def _archive_identity_key(document):
    """Stable cross-session semantic identity; excludes render-scoped Kurum tokens."""
    document = document or {}
    payload = [
        _identity_text(document.get("sourceSystem")),
        _identity_text(document.get("institutionName") or document.get("courtName")),
        _canonical_case_id(document.get("caseId")),
        [_identity_text(item) for item in (document.get("folderNames") or []) if _identity_text(item)],
        _identity_text(document.get("documentTitle")),
        _identity_text(document.get("documentDate")),
        _identity_text(document.get("unitDocumentNumber")),
        _identity_text(document.get("documentType")),
        _identity_text(document.get("sender")),
    ]
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _legacy_semantic_key(case_id, display_name, uyap_path):
    return "|".join((_canonical_case_id(case_id), _identity_text(display_name), _identity_text(uyap_path)))


def _archive_file_match_key(relative_path):
    """Path identity that ignores extension and the final volatile document/source id suffix."""
    raw = str(relative_path or "").replace("\\", "/").strip("/")
    if not raw:
        return None
    path = PurePosixPath(raw)
    stem = Path(path.name).stem
    stem = re.sub(r"_(?:d-[a-f0-9]{12,128}|\d{4,30})$", "", stem, flags=re.IGNORECASE)
    parent = "/".join(_identity_text(part) for part in path.parts[:-1])
    return f"{parent}/{_identity_text(stem)}"


def _portable_row_paths(root: Path, local_path, target_path=None, source_document_id=None, loose_source_index=None):
    """Resolve copied DB rows against the currently selected archive root."""
    candidates = []
    raw_target = str(target_path or "").replace("\\", "/").strip()
    if raw_target:
        try:
            target = safe_target(root, raw_target)
            if len(target.relative_to(root).parts) >= 2 and target.relative_to(root).parts[0].casefold() == "uyap_arsiv":
                candidates.append(target)
        except (OSError, ValueError):
            pass
    raw_local = str(local_path or "").strip()
    if raw_local:
        try:
            candidates.append(Path(raw_local).expanduser().resolve())
        except (OSError, ValueError):
            pass
        parts = [part for part in re.split(r"[\\/]+", raw_local) if part]
        archive_index = next((index for index, part in enumerate(parts) if part.casefold() == "uyap_arsiv"), None)
        if archive_index is not None:
            try:
                rebased = safe_target(root, "/".join(parts[archive_index:]))
                candidates.append(rebased)
            except (OSError, ValueError):
                pass
        # The first releases kept Chrome's canonical evrak_<id> download
        # beside UYAP_Arsiv. A copied DB still contains the old computer's
        # absolute path, so also try that exact basename in the selected root.
        # Arbitrary DB filenames are deliberately excluded.
        basename = Path(raw_local.replace("\\", "/")).name
        if re.fullmatch(
            r"evrak[_ -]?\d{4,30}(?: \(\d+\))?\.(?:pdf|udf|uyap|zip|html?|tiff?|jpe?g|png|gif|bmp|webp|svg|xml|rtf|docx?|xlsx?|pptx?|odt|ods|odp|7z|rar|bin)",
            basename,
            flags=re.IGNORECASE,
        ):
            candidates.append(root / basename)
    source_id = str(source_document_id or "").strip()
    if re.fullmatch(r"\d{4,30}", source_id):
        candidates.extend((loose_source_index or {}).get(source_id, []))
    unique = []
    seen = set()
    for candidate in candidates:
        key = os.path.normcase(str(candidate))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique



def _registry_row_path(root: Path, row):
    """Resolve a registry row against the currently selected archive root.

    Copied databases may retain the previous PC's absolute local_path. Prefer
    an existing target_path/rebased candidate so Workspace reflects the bytes
    that are actually present on this machine.
    """
    row = row or {}
    candidates = _portable_row_paths(
        root,
        row.get("local_path"),
        row.get("target_path"),
        row.get("source_document_id"),
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    if candidates:
        return candidates[0]
    raw_local = str(row.get("local_path") or "").strip()
    return Path(raw_local).expanduser().resolve() if raw_local else (root / "UYAP_Arsiv" / "_missing")

def _row_file_match_key(root: Path, local_path, target_path=None):
    if target_path:
        key = _archive_file_match_key(target_path)
        if key:
            return key
    try:
        candidate = Path(str(local_path or "")).resolve()
        relative = candidate.relative_to(root).as_posix()
        return _archive_file_match_key(relative)
    except (OSError, ValueError):
        return None


def _verified_file_proof(local_path, expected_size, expected_hash):
    try:
        path = Path(str(local_path or "")).resolve()
        if not path.is_file():
            return None
        size = path.stat().st_size
        if expected_size not in (None, "") and size != int(expected_size or 0):
            return None
        digest = sha256_file(path)
        if expected_hash and digest != str(expected_hash):
            return None
        return path, size, digest
    except (OSError, ValueError, TypeError):
        return None


def _is_legacy_loose_download(root: Path, path: Path):
    """True only for a canonical evrak file directly beside UYAP_Arsiv."""
    try:
        candidate = path.resolve()
        selected_root = root.resolve()
        if path.is_symlink() or os.path.normcase(str(candidate.parent)) != os.path.normcase(str(selected_root)):
            return False
        return canonical_source_id({}, candidate.name) is not None
    except (OSError, ValueError):
        return False


def _import_legacy_loose_download(root: Path, source: Path, row, document, expected_hash):
    """Validate and non-destructively adopt a proven legacy root download."""
    if not _is_legacy_loose_download(root, source):
        return None
    target_path = str(document.get("targetPath") or row.get("target_path") or "").strip()
    if not target_path:
        return None
    try:
        target = safe_target(root, target_path)
        relative = target.relative_to(root)
        if len(relative.parts) < 2 or relative.parts[0].casefold() != "uyap_arsiv":
            return None

        detected = detect_extension(source, source.suffix)
        declared = source.suffix.lower()
        equivalent = (
            ({declared, detected} <= {".jpg", ".jpeg"})
            or ({declared, detected} <= {".tif", ".tiff"})
            or ({declared, detected} <= {".htm", ".html"})
        )
        if declared != detected and not equivalent:
            return None
        source_id = canonical_source_id({}, source.name)
        html_context = {
            "sourceDocumentId": source_id,
            "originalFilename": source.name,
            "bindingMethod": "legacy-loose-db-sha256",
            "expectedDocumentKind": "uyap_html_document" if str(row.get("validation_kind") or "") == "HTML_DOCUMENT" else "",
        }
        valid, _, _ = validate_content(source, detected, html_context=html_context)
        if not valid:
            return None

        target = target.with_suffix(detected)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = _verified_file_proof(target, source.stat().st_size, expected_hash)
            return (target.resolve(), "existing-canonical") if existing else None

        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.legacy-import")
        mode = "hardlink"
        try:
            try:
                os.link(source, temporary)
            except OSError:
                mode = "copy"
                shutil.copy2(source, temporary)
            proof = _verified_file_proof(temporary, source.stat().st_size, expected_hash)
            if not proof:
                return None
            with temporary.open("rb") as stream:
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            return target.resolve(), mode
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    except (OSError, ValueError, TypeError):
        return None


def database(root: Path):
    archive_root = root / "UYAP_Arsiv"
    archive_root.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(archive_root / "_archive.db", timeout=30.0)
    connection.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=NORMAL;
        PRAGMA busy_timeout=30000;
        PRAGMA wal_autocheckpoint=4000;
        CREATE TABLE IF NOT EXISTS documents (
          session_id TEXT NOT NULL, document_id TEXT NOT NULL,
          case_id TEXT, display_name TEXT, uyap_path TEXT, local_path TEXT,
          size INTEGER, sha256 TEXT, source_fingerprint TEXT,
          source_system TEXT, institution_name TEXT, source_document_id TEXT,
          archive_identity_key TEXT, document_date TEXT, target_path TEXT,
          validation_kind TEXT, validation_mode TEXT,
          status TEXT NOT NULL, updated_at TEXT NOT NULL,
          PRIMARY KEY (session_id, document_id)
        );
        CREATE TABLE IF NOT EXISTS download_attempts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
          document_id TEXT NOT NULL, attempt_no INTEGER, response_type TEXT,
          error_code TEXT, detail TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS archive_jobs (
          session_id TEXT PRIMARY KEY, expected INTEGER, verified INTEGER,
          unresolved INTEGER, current_state TEXT, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transfer_journal (
          attempt_id TEXT PRIMARY KEY, request_key TEXT NOT NULL,
          result_json TEXT NOT NULL, committed INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_documents_status_case ON documents(status, case_id);
        CREATE INDEX IF NOT EXISTS idx_documents_scope ON documents(status, case_id, source_system, institution_name);
        CREATE INDEX IF NOT EXISTS idx_documents_source_id ON documents(source_document_id);
        CREATE INDEX IF NOT EXISTS idx_documents_archive_identity ON documents(archive_identity_key);
        CREATE INDEX IF NOT EXISTS idx_documents_local_path ON documents(local_path);
        CREATE INDEX IF NOT EXISTS idx_documents_hash_scope ON documents(status, case_id, sha256, size);
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(documents)")}
    migrations = {
        "source_fingerprint": "TEXT",
        "source_system": "TEXT",
        "institution_name": "TEXT",
        "source_document_id": "TEXT",
        "archive_identity_key": "TEXT",
        "document_date": "TEXT",
        "target_path": "TEXT",
        "validation_kind": "TEXT",
        "validation_mode": "TEXT",
    }
    changed = False
    for column, column_type in migrations.items():
        if column not in columns:
            connection.execute(f"ALTER TABLE documents ADD COLUMN {column} {column_type}")
            changed = True
    if changed:
        connection.commit()
    return connection


def record_attempt(connection, message, status, error_code=None, detail=None, commit=True, **values):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    document = message.get("document") or {}
    connection.execute(
        """INSERT INTO download_attempts
           (session_id, document_id, attempt_no, response_type, error_code, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message.get("sessionId"), document.get("documentId"), document.get("attempts"),
         status, error_code, detail, now),
    )
    connection.execute(
        """INSERT INTO documents
           (session_id, document_id, case_id, display_name, uyap_path, local_path,
            size, sha256, source_fingerprint, source_system, institution_name,
            source_document_id, archive_identity_key, document_date, target_path,
            validation_kind, validation_mode, status, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id, document_id) DO UPDATE SET
             case_id=excluded.case_id, display_name=excluded.display_name, uyap_path=excluded.uyap_path,
             local_path=excluded.local_path, size=excluded.size, sha256=excluded.sha256,
             source_fingerprint=excluded.source_fingerprint, source_system=excluded.source_system,
             institution_name=excluded.institution_name, source_document_id=excluded.source_document_id,
             archive_identity_key=excluded.archive_identity_key, document_date=excluded.document_date,
             target_path=excluded.target_path, validation_kind=excluded.validation_kind,
             validation_mode=excluded.validation_mode, status=excluded.status, updated_at=excluded.updated_at""",
        (message.get("sessionId"), document.get("documentId"), document.get("caseId"),
         document.get("documentTitle"), "/".join(document.get("folderNames") or []),
         values.get("local_path"), values.get("size"), values.get("sha256"),
         document.get("sourceFingerprint"), document.get("sourceSystem"),
         document.get("institutionName") or document.get("courtName"), document.get("sourceDocumentId"),
         _archive_identity_key(document), document.get("documentDate"), document.get("targetPath"),
         values.get("validation_kind"), values.get("validation_mode"), status, now),
    )
    if commit:
        connection.commit()




def _journal_root_candidate(source: Path, message):
    """Infer the download root without moving the source file.

    This is used only to replay an already-committed attempt after the native
    response was lost and the source has already been moved into the archive.
    """
    staging_path = message.get("stagingPath")
    if staging_path:
        try:
            return downloads_root(source, staging_path)
        except (OSError, ValueError):
            pass
    if message.get("allowCanonicalAdoption") or message.get("allowLeaseAdoption"):
        return _lexical_abs(source.parent)
    return None


def _journal_identity_matches(request_key, message):
    try:
        values = json.loads(request_key)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(values, list) or len(values) != 5:
        return False
    document = message.get("document") or {}
    return (
        str(values[0] or "") == str(message.get("sessionId") or "")
        and str(values[1] or "") == str(document.get("documentId") or "")
        and str(values[3] or "") == str(message.get("stagingPath") or "")
        and str(values[4] or "") == str(message.get("targetPath") or "")
    )


def _journal_attempt_owner_matches(request_key, message):
    """Match the immutable owner of an idempotent browser attempt.

    A completion callback may learn the real extension/source id after the
    native host has already committed the bytes. That legitimately changes the
    requested target path, but it must never make the same attempt look like a
    different document. Replays always return the original committed path, so
    ignoring late target-path drift here cannot redirect or overwrite bytes.
    """
    try:
        values = json.loads(request_key)
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    if not isinstance(values, list) or len(values) != 5:
        return False
    document = message.get("document") or {}
    session_id = str(message.get("sessionId") or "")
    document_id = str(document.get("documentId") or "")
    staging_path = str(message.get("stagingPath") or "")
    return bool(
        session_id and document_id and staging_path
        and str(values[0] or "") == session_id
        and str(values[1] or "") == document_id
        and str(values[3] or "") == staging_path
    )


def _replay_committed_attempt_when_source_missing(message, source: Path):
    """Replay a byte-proven transfer journal entry before source adoption.

    Native messaging can lose a successful response after the file has already
    been moved. A retry must not require the vanished browser source path.
    Attempt/session/document/staging/target identity plus destination size/hash
    remain mandatory; the ignored request-key source path is the one that was
    intentionally moved by the original successful operation.
    """
    attempt_id = str(message.get("attemptId") or "").strip()
    if not attempt_id:
        return None
    root = _journal_root_candidate(source, message)
    if root is None:
        return None
    db_path = root / "UYAP_Arsiv" / "_archive.db"
    if not db_path.is_file():
        return None
    connection = database(root)
    try:
        journal = connection.execute(
            "SELECT request_key, result_json, committed FROM transfer_journal WHERE attempt_id=?",
            (attempt_id,),
        ).fetchone()
        if not journal:
            return None
        if not _journal_attempt_owner_matches(journal[0], message):
            raise ValueError("ATTEMPT_ID_REUSED_WITH_DIFFERENT_DOCUMENT")
        result = json.loads(journal[1])
        destination = Path(str(result.get("localPath") or ""))
        expected_size = int(result.get("size") or -1)
        expected_hash = str(result.get("sha256") or "")
        if not destination.is_file() or destination.stat().st_size != expected_size:
            return {"ok": True, "valid": False, "errorCode": "TRANSFER_INTEGRITY_FAILED",
                    "detail": "Kayitli aktarim dosyasi kayip veya degismis.",
                    "retryHint": "fresh_redownload", "sourcePathRecovery": True}
        if sha256_file(destination) != expected_hash:
            return {"ok": True, "valid": False, "errorCode": "TRANSFER_INTEGRITY_FAILED",
                    "detail": "Kayitli aktarim dosyasi kayip veya degismis.",
                    "retryHint": "fresh_redownload", "sourcePathRecovery": True}
        if not (result.get("htmlDocumentValidated") or result.get("imageDocumentValidated") or result.get("opaqueBinaryValidated")):
            if not validate_content(destination, result.get("extension") or destination.suffix)[0]:
                return {"ok": True, "valid": False, "errorCode": "TRANSFER_INTEGRITY_FAILED",
                        "detail": "Kayitli aktarim dosyasi içerik doğrulamasını artık geçmiyor.",
                        "retryHint": "fresh_redownload", "sourcePathRecovery": True}
        if not journal[2]:
            record_attempt(
                connection, message, 'VERIFIED', commit=False, local_path=str(destination),
                size=expected_size, sha256=expected_hash,
                validation_kind=result.get('contentKind'),
                validation_mode=(result.get('htmlValidationMode') or result.get('udfValidationMode') or result.get('imageValidationMode') or result.get('opaqueValidationMode')),
            )
            connection.execute('UPDATE transfer_journal SET committed=1 WHERE attempt_id=?', (attempt_id,))
            connection.commit()
        target_drift = not _journal_identity_matches(journal[0], message)
        return {**result, "replayed": True, "replayMode": "attempt-owner-target-drift" if target_drift else "missing-source-journal",
                "requestedTargetPathIgnored": target_drift, "downloadsRoot": str(root)}
    finally:
        connection.close()

def _error_category(error_code: str):
    code = str(error_code or "UNKNOWN").upper()
    if code in {"HTML_RESPONSE", "HTML_RESPONSE_RECEIVED"}:
        return "UYAP_HTML_Yanitlari"
    if code == "UNSUPPORTED_FORMAT":
        return "Desteklenmeyen_Format"
    if code == "INVALID_IMAGE":
        return "Goruntu_Yapi_Sorunu"
    if code.startswith("PDF_") or code == "TRUNCATED_PDF":
        return "PDF_Yapi_Sorunu"
    if code.startswith("TRUNCATED_") or code in {"DOWNLOAD_SIZE_MISMATCH", "DOWNLOAD_NOT_COMPLETE", "FILE_NOT_CONFIRMED"}:
        return "Eksik_Indirme"
    if code.startswith("ZIP_") or code.startswith("UDF_") or code == "INVALID_UDF_XML":
        return "Arsiv_Yapi_Sorunu"
    return "Diger"


def quarantine(source: Path, root: Path, message, error_code: str):
    document = message.get("document") or {}
    # HTML is detected from the downloaded bytes, not from the temporary browser
    # filename. Chrome/UYAP may stage a real HTML response as .bin/.tmp/no suffix.
    # Persist known HTML failures with an .html suffix so later user review and
    # cleanup do not conflict with the quarantine naming policy.
    normalized_error = str(error_code or "").upper()
    if normalized_error in {"HTML_RESPONSE", "HTML_RESPONSE_RECEIVED"}:
        suffix = ".html"
    else:
        suffix = source.suffix if source.suffix.lower() in (".html", ".htm") else ".invalid"
    session_ref = hashlib.sha256(str(message.get("sessionId")).encode()).hexdigest()[:24]
    folder = root / "UYAP_Arsiv" / "Hatalı Dosyalar" / _error_category(error_code) / session_ref
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex}_{error_code}{suffix}"
    os.replace(source, target)
    return target


def _positive_size(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _settled_source_size(source: Path, expected_size=None):
    """After Chrome says complete, tolerate a very short filesystem metadata lag."""
    expected = _positive_size(expected_size)
    observed = source.stat().st_size if source.is_file() else None
    if expected is None or observed == expected:
        return observed
    for _ in range(4):
        time.sleep(0.075)
        observed = source.stat().st_size if source.is_file() else None
        if observed == expected:
            break
    return observed


def _await_visible_candidate(candidates, expected_size=None, delays=(0.10, 0.25, 0.50, 1.00)):
    expected = _positive_size(expected_size)
    diagnostics = {}
    waited_ms = 0
    unique = []
    seen = set()
    for mode, path in candidates:
        try:
            normalized = _lexical_abs(Path(path))
            key = os.path.normcase(str(normalized))
        except (OSError, ValueError, TypeError):
            continue
        if key in seen:
            continue
        seen.add(key)
        unique.append((mode, normalized))

    for step in range(len(delays) + 1):
        for mode, candidate in unique:
            try:
                exists = candidate.is_file()
                observed = candidate.stat().st_size if exists else None
                size_matches = observed is not None and (expected is None or observed == expected)
                diagnostics[mode] = {
                    "exists": bool(exists),
                    "observedSize": observed,
                    "expectedSize": expected,
                    "sizeMatches": bool(size_matches),
                    "waitedMs": waited_ms,
                }
                if size_matches:
                    return candidate, mode, diagnostics
            except OSError:
                diagnostics[mode] = {
                    "exists": False,
                    "observedSize": None,
                    "expectedSize": expected,
                    "sizeMatches": False,
                    "waitedMs": waited_ms,
                }
        if step >= len(delays):
            break
        delay = float(delays[step])
        time.sleep(delay)
        waited_ms += int(round(delay * 1000))
    return None, None, diagnostics


def _completion_guard(source: Path, message):
    state = message.get("browserState")
    browser_exists = message.get("browserExists")
    browser_file_size = _positive_size(message.get("browserFileSize"))
    browser_total_bytes = _positive_size(message.get("browserTotalBytes"))
    observed_size = _settled_source_size(source, browser_file_size) if source.is_file() else None
    diagnostics = {
        "browserState": state,
        "browserExists": browser_exists,
        "browserFileSize": browser_file_size,
        "browserTotalBytes": browser_total_bytes,
        "observedSize": observed_size,
    }
    if state and state != "complete":
        return False, "DOWNLOAD_NOT_COMPLETE", "Tarayıcı indirmeyi tamamlanmış olarak işaretlemedi.", diagnostics
    if browser_exists is False or observed_size is None:
        return False, "SOURCE_FILE_MISSING", "Tamamlanan indirme kaydı mevcut ancak yerel geçici dosya diskte bulunamadı.", diagnostics
    if browser_file_size is not None and observed_size != browser_file_size:
        return False, "DOWNLOAD_SIZE_MISMATCH", f"Disk boyutu tarayıcı kaydıyla uyuşmuyor: {observed_size}/{browser_file_size} bayt.", diagnostics
    if browser_total_bytes and observed_size < browser_total_bytes:
        return False, "DOWNLOAD_SIZE_MISMATCH", f"İndirme beklenen boyuttan kısa: {observed_size}/{browser_total_bytes} bayt.", diagnostics
    return True, None, None, diagnostics


def _verified_content_hash_candidate(connection, root: Path, document, digest: str, size: int):
    """Return one physically proven VERIFIED file with the same SHA-256 in scope.

    Metadata is only a candidate filter. The final decision is byte identity: the
    candidate file must still exist, have the expected size and hash back to the
    exact digest. This is intentionally safe for UYAP/Kurum rows that reuse titles,
    dates or folder names across distinct documents.
    """
    case_id = str(document.get("caseId") or "").strip()
    source_system = _identity_text(document.get("sourceSystem"))
    institution = _identity_text(document.get("institutionName") or document.get("courtName"))
    sql = (
        "SELECT local_path, size, sha256, source_system, institution_name, case_id "
        "FROM documents WHERE status='VERIFIED' AND sha256=? AND size=?"
    )
    params = [str(digest), int(size)]
    if case_id:
        sql += " AND case_id=?"
        params.append(case_id)
    sql += " ORDER BY updated_at DESC"
    archive_root = (root / "UYAP_Arsiv").resolve()
    seen = set()
    for local_path, row_size, row_hash, row_source, row_inst, row_case in connection.execute(sql, tuple(params)).fetchall():
        if source_system and _identity_text(row_source) and source_system != _identity_text(row_source):
            continue
        if institution and _identity_text(row_inst) and institution != _identity_text(row_inst):
            continue
        if case_id and str(row_case or "").strip() and str(row_case or "").strip() != case_id:
            continue
        try:
            candidate = Path(str(local_path or "")).resolve()
            if candidate in seen:
                continue
            seen.add(candidate)
            if archive_root not in candidate.parents:
                continue
            proof = _verified_file_proof(candidate, row_size, row_hash)
            if not proof:
                continue
            proven_path, proven_size, proven_hash = proof
            if int(proven_size) == int(size) and str(proven_hash) == str(digest):
                return proven_path
        except (OSError, ValueError, TypeError):
            continue
    return None


def validate_download(message):
    perf_started = time.perf_counter()
    source_path_recovery_mode = None
    source_path_recovery_checks = {}
    perf = {
        "databaseOpenMs": 0.0,
        "journalLookupMs": 0.0,
        "typeDetectMs": 0.0,
        "contentValidationMs": 0.0,
        "hashMs": 0.0,
        "fsyncMs": 0.0,
        "existingTargetHashMs": 0.0,
        "journalPrepareMs": 0.0,
        "moveMs": 0.0,
        "databaseCommitMs": 0.0,
        "replayCheckMs": 0.0,
        "invalidPersistMs": 0.0,
        "contentHashReconcileMs": 0.0,
    }

    def finish(result):
        perf["agentTotalMs"] = _perf_ms(perf_started)
        result["nativePerf"] = {key: round(float(value or 0), 2) for key, value in perf.items()}
        if source_path_recovery_mode:
            result["sourcePathRecovered"] = True
            result["sourcePathRecoveryMode"] = source_path_recovery_mode
        if source_path_recovery_checks:
            result["sourcePathRecoveryChecks"] = source_path_recovery_checks
        return result

    raw_filename = str(message.get("filename") or "").strip()
    if not raw_filename:
        return finish({"ok": True, "valid": False, "errorCode": "SOURCE_FILE_MISSING",
                       "detail": "Tarayıcı tamamlanan indirme için yerel dosya yolunu sağlayamadı; evrak taze indirme ile otomatik kurtarılacak.",
                       "retryHint": "fresh_redownload", "sourcePathRecovery": True})
    source = _lexical_abs(Path(raw_filename))
    original_source = source
    staging_path = message["stagingPath"]

    if (
        not source.is_file()
        and message.get("browserState") == "complete"
        and message.get("browserExists") is not False
    ):
        try:
            staging_parts = _staging_parts(staging_path)
        except ValueError:
            staging_parts = None
        if staging_parts:
            candidates = [("browser-reported-file", original_source)]
            if message.get("downloadsRoot"):
                known_root = _lexical_abs(Path(str(message.get("downloadsRoot"))))
                candidates.append(("known-downloads-root-staging", known_root.joinpath(*staging_parts)))
            candidates.append(("browser-reported-root-staging", _lexical_abs(original_source.parent).joinpath(*staging_parts)))
            try:
                selected_root = load_selected_archive_root()
                if selected_root is not None:
                    candidates.append(("selected-archive-root-staging", _lexical_abs(selected_root).joinpath(*staging_parts)))
            except (OSError, ValueError):
                pass
            recovered, mode, checks = _await_visible_candidate(
                candidates,
                message.get("browserFileSize"),
                delays=(0.10, 0.25, 0.50, 1.00),
            )
            source_path_recovery_checks.update(checks)
            if recovered is not None:
                source = recovered
                source_path_recovery_mode = mode

    # RC15.9.17: canonical/lease adoption can legitimately start from a browser
    # path outside _incoming, but it must never override Chrome's completion/size proof.
    # Check the untouched browser file before any move into staging. A stale
    # history path that now points at different bytes is left exactly where it is
    # and the extension is instructed to perform a fresh redownload.
    if message.get("allowCanonicalAdoption") and source.is_file():
        pre_ok, pre_error, pre_detail, pre_diag = _completion_guard(source, message)
        if not pre_ok:
            return finish({
                "ok": True, "valid": False, "errorCode": pre_error, "detail": pre_detail,
                **pre_diag, "retryHint": "fresh_redownload", "sourcePreserved": True,
                "preAdoptionGuard": True
            })

    normalize_started = time.perf_counter()
    try:
        source, root = normalize_source_to_staging(source, message)
    except FileNotFoundError:
        replay = _replay_committed_attempt_when_source_missing(message, original_source)
        if replay is not None:
            return finish(replay)
        return finish({"ok": True, "valid": False, "errorCode": "SOURCE_FILE_MISSING",
                       "detail": "Tarayıcı indirme kaydı mevcut ancak geçici dosya yolu diskte bulunamadı; evrak taze indirme ile otomatik kurtarılacak.",
                       "retryHint": "fresh_redownload", "sourcePathRecovery": True})
    perf["normalizeMs"] = _perf_ms(normalize_started)

    target = safe_target(root, message["targetPath"])
    if len(target.relative_to(root).parts) < 5 or target.relative_to(root).parts[0] != "UYAP_Arsiv" or target.relative_to(root).parts[1].startswith("_"):
        raise ValueError("Hedef dava arşivi içinde olmalı")

    db_started = time.perf_counter()
    connection = database(root)
    perf["databaseOpenMs"] = _perf_ms(db_started)

    attempt_id = message.get('attemptId') or uuid.uuid4().hex
    request_key = json.dumps([message.get('sessionId'), (message.get('document') or {}).get('documentId'),
                              str(original_source), staging_path, message['targetPath']], ensure_ascii=False)
    try:
        journal_started = time.perf_counter()
        journal = connection.execute('SELECT request_key, result_json, committed FROM transfer_journal WHERE attempt_id=?', (attempt_id,)).fetchone()
        perf["journalLookupMs"] = _perf_ms(journal_started)
        if journal:
            if journal[0] != request_key:
                if not _journal_attempt_owner_matches(journal[0], message):
                    raise ValueError('ATTEMPT_ID_REUSED_WITH_DIFFERENT_DOCUMENT')
            result = json.loads(journal[1])
            destination = Path(result['localPath'])
            replay_started = time.perf_counter()
            replay_ok = False
            if destination.is_file() and destination.stat().st_size == result['size']:
                hash_started = time.perf_counter()
                replay_hash_ok = sha256_file(destination) == result['sha256']
                perf["hashMs"] += _perf_ms(hash_started)
                validate_started = time.perf_counter()
                if replay_hash_ok and (result.get("htmlDocumentValidated") or result.get("imageDocumentValidated") or result.get("opaqueBinaryValidated")):
                    # The hash proves this is byte-identical to content already
                    # accepted with structural validation/source evidence.
                    replay_content_ok = True
                else:
                    replay_content_ok = validate_content(destination, result['extension'])[0] if replay_hash_ok else False
                perf["contentValidationMs"] += _perf_ms(validate_started)
                replay_ok = replay_hash_ok and replay_content_ok
            perf["replayCheckMs"] = _perf_ms(replay_started)
            if replay_ok:
                if not journal[2]:
                    commit_started = time.perf_counter()
                    record_attempt(
                        connection, message, 'VERIFIED', commit=False, local_path=str(destination),
                        size=result['size'], sha256=result['sha256'],
                        validation_kind=result.get('contentKind'),
                        validation_mode=(result.get('htmlValidationMode') or result.get('udfValidationMode') or result.get('imageValidationMode') or result.get('opaqueValidationMode')),
                    )
                    connection.execute('UPDATE transfer_journal SET committed=1 WHERE attempt_id=?', (attempt_id,))
                    connection.commit()
                    perf["databaseCommitMs"] += _perf_ms(commit_started)
                target_drift = journal[0] != request_key
                return finish({**result, 'replayed':True,
                               'replayMode':'attempt-owner-target-drift' if target_drift else 'exact-journal',
                               'requestedTargetPathIgnored':target_drift})
            if not source.is_file():
                return finish({'ok':True, 'valid':False, 'errorCode':'TRANSFER_INTEGRITY_FAILED', 'detail':'Kayitli aktarim dosyasi kayip veya degismis.'})
            cleanup_started = time.perf_counter()
            connection.execute('DELETE FROM transfer_journal WHERE attempt_id=?', (attempt_id,))
            connection.commit()
            perf["databaseCommitMs"] += _perf_ms(cleanup_started)

        completion_ok, completion_error, completion_detail, completion_diag = _completion_guard(source, message)

        document = message.get("document") or {}
        detection_hint = _extension_hint(
            message.get("extension"),
            document.get("extension"),
            document.get("targetPath"),
            document.get("originalFilename"),
            document.get("browserFilename"),
            message.get("filename"),
        )
        detect_started = time.perf_counter()
        if source.is_file():
            detection_profile = detect_content_profile(source, detection_hint)
            expected_extension = detection_profile.get("extension") or ".bin"
        else:
            detection_profile = {"extension": detection_hint or ".bin", "mode": "source_missing", "hint": detection_hint, "udfProbeError": None}
            expected_extension = detection_profile["extension"]
        perf["typeDetectMs"] = _perf_ms(detect_started)

        learned_source_id = canonical_source_id(document, message.get("filename"), source.name)
        if learned_source_id:
            document["sourceDocumentId"] = learned_source_id
        target = canonical_target(target, document, expected_extension, learned_source_id)
        # Fit the absolute path into the Windows budget before any hashing or
        # journaling. An over-long target used to fail at os.replace with
        # FileNotFoundError and was reported as SOURCE_FILE_MISSING, which
        # triggered endless fresh re-downloads of perfectly good files.
        try:
            target, target_budget = _fit_target_path_budget(target)
        except TargetPathTooLong as error:
            target_budget = None
            # An incomplete download keeps its own, more precise error below.
            if completion_ok:
                return finish(_target_path_too_long_payload(error.diagnostics))

        html_context = {
            "sourceDocumentId": learned_source_id or document.get("sourceDocumentId"),
            "originalFilename": document.get("originalFilename") or Path(str(message.get("filename") or "")).name,
            "bindingMethod": document.get("bindingMethod") or "",
            "correlationMode": document.get("correlationMode") or message.get("correlationMode") or "",
            "correlationCandidateCount": document.get("correlationCandidateCount") or message.get("correlationCandidateCount") or 0,
            "expectedDocumentKind": document.get("expectedDocumentKind") or message.get("expectedDocumentKind") or "",
            "directDownloadEvidence": direct_download_evidence(message),
        }
        if html_context["directDownloadEvidence"]:
            html_context["correlationMode"] = "exact"
            html_context["correlationCandidateCount"] = 1
        html_profile = classify_html_payload(source, html_context) if source.is_file() and expected_extension.lower() in (".html", ".htm") else None
        image_profile = classify_image_payload(source, html_context) if source.is_file() and expected_extension.lower() in _IMAGE_EXTENSIONS else None
        udf_validation_mode = None
        opaque_validation_mode = None

        validation_started = time.perf_counter()
        content_diag = {
            "contentDetectedExtension": expected_extension,
            "contentDetectionMode": detection_profile.get("mode"),
            "contentDetectionHint": detection_profile.get("hint"),
            "udfProbeError": detection_profile.get("udfProbeError"),
        }
        if completion_ok:
            if expected_extension.lower() == ".pdf" and source.is_file():
                valid, error_code, detail, pdf_diag = validate_pdf_structure(source)
                content_diag.update(pdf_diag or {})
            elif expected_extension.lower() == ".udf" and source.is_file():
                valid, error_code, detail, udf_validation_mode = _validate_udf_package(source)
                if udf_validation_mode:
                    content_diag["udfValidationMode"] = udf_validation_mode
            else:
                valid, error_code, detail = validate_content(source, expected_extension, html_context=html_context)
            if (
                not valid
                and error_code == "UNSUPPORTED_FORMAT"
                and source.is_file()
                and _opaque_download_provenance(source, message, html_context, completion_diag, detection_profile)
            ):
                valid, error_code, detail = True, None, None
                opaque_validation_mode = "uyap_exact_opaque_binary_v1"
                content_diag["opaqueBinaryValidated"] = True
                content_diag["opaqueValidationMode"] = opaque_validation_mode
        else:
            valid, error_code, detail = False, completion_error, completion_detail
        perf["contentValidationMs"] += _perf_ms(validation_started)
        if not valid:
            if image_profile:
                content_diag["imageDetectedType"] = image_profile.get("kind")
                content_diag["imageValidationMode"] = image_profile.get("mode")
                content_diag["imageFallbackFromMode"] = image_profile.get("fallbackFromMode")
                content_diag["imageWicFallbackMode"] = image_profile.get("wicFallbackMode")
                content_diag["imageGdiplusFallbackMode"] = image_profile.get("gdiplusFallbackMode")
                content_diag["imageTrailingBytes"] = image_profile.get("trailingBytes")
                content_diag["imageTrailingKind"] = image_profile.get("trailingKind")
            observed_size = completion_diag.get("observedSize")
            browser_total = completion_diag.get("browserTotalBytes")
            if error_code in ("TRUNCATED_PDF", "TRUNCATED_TIFF") and browser_total:
                detail = f"{detail} Boyut: {observed_size}/{browser_total} bayt."
            invalid_hash = None
            if source.is_file():
                try:
                    invalid_hash = sha256_file(source)
                except OSError:
                    invalid_hash = None
            persist_started = time.perf_counter()
            rejected = quarantine(source, root, message, error_code) if source.is_file() else None
            record_attempt(connection, message, "INVALID_CONTENT", error_code, detail,
                           local_path=str(rejected) if rejected else None, size=rejected.stat().st_size if rejected else 0,
                           sha256=invalid_hash)
            perf["invalidPersistMs"] = _perf_ms(persist_started)
            return finish({"ok": True, "valid": False, "errorCode": error_code,
                    "detail": detail, "quarantinePath": str(rejected) if rejected else None,
                    "invalidSha256": invalid_hash,
                    **completion_diag, **content_diag,
                    "retryHint": "fresh_redownload" if error_code in ("TRUNCATED_PDF", "TRUNCATED_TIFF", "DOWNLOAD_SIZE_MISMATCH", "DOWNLOAD_NOT_COMPLETE", "SOURCE_FILE_MISSING") else None})

        size = source.stat().st_size
        hash_started = time.perf_counter()
        digest = sha256_file(source)
        perf["hashMs"] += _perf_ms(hash_started)

        fsync_started = time.perf_counter()
        with source.open("r+b") as stream:
            os.fsync(stream.fileno())
        perf["fsyncMs"] = _perf_ms(fsync_started)

        # RC15.9.10 CONTENT-HASH RECONCILIATION:
        # metadata may produce several plausible local matches in Kurum. Never
        # block the source download on that ambiguity. After structural validation
        # the fresh SHA-256 becomes the authority. If identical verified bytes
        # already exist in the same case/scope, create a distinct archive path as
        # a hard-link where possible. This preserves every UYAP document identity
        # and final-manifest path while avoiding duplicate disk blocks.
        hash_reconcile_started = time.perf_counter()
        hash_candidate = _verified_content_hash_candidate(connection, root, document, digest, size)
        perf["contentHashReconcileMs"] = _perf_ms(hash_reconcile_started)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except FileNotFoundError as error:
            # The bytes are complete and validated; only the archive folder is
            # unreachable. A fresh re-download cannot fix that.
            return finish(_target_unavailable_payload(error, source, target))
        final_target = target
        reuse = False
        content_hash_reconciled = False
        content_hash_reuse_mode = None
        if target.exists():
            existing_hash_started = time.perf_counter()
            if sha256_file(target) == digest:
                reuse = True
                content_hash_reconciled = bool(hash_candidate)
                content_hash_reuse_mode = "existing-target" if hash_candidate else None
            else:
                try:
                    final_target = _versioned_target(target)
                except TargetPathTooLong as error:
                    return finish(_target_path_too_long_payload(error.diagnostics))
            perf["existingTargetHashMs"] = _perf_ms(existing_hash_started)

        if not reuse and hash_candidate and hash_candidate.resolve() != final_target.resolve():
            try:
                os.link(hash_candidate, final_target)
                reuse = True
                content_hash_reconciled = True
                content_hash_reuse_mode = "hardlink"
            except OSError:
                # Hard links can be unavailable on unusual/redirected filesystems.
                # Integrity wins over storage optimization: keep the newly
                # downloaded, validated bytes as the distinct document path.
                content_hash_reconciled = True
                content_hash_reuse_mode = "downloaded-copy-fallback"

        result = {"ok": True, "valid": True, "size": size, "sha256": digest,
                  "localPath": str(final_target), "downloadsRoot": str(root),
                  "extension":expected_extension, "targetPath":final_target.relative_to(root).as_posix(),
                  "sourceDocumentId": learned_source_id,
                  "contentDetectedExtension": content_diag.get("contentDetectedExtension"),
                  "contentDetectionMode": content_diag.get("contentDetectionMode"),
                  "contentDetectionHint": content_diag.get("contentDetectionHint"),
                  "contentHashReconciled": content_hash_reconciled,
                  "contentHashReuseMode": content_hash_reuse_mode}
        if content_diag:
            for key in ("pdfEofOffset", "pdfTrailingBytes", "pdfStartXref", "pdfXrefKind", "pdfTrailingKind"):
                if key in content_diag:
                    result[key] = content_diag.get(key)
            if content_diag.get("pdfTrailingWarning"):
                # A recognized Belgenet suffix is a non-actionable provenance
                # note, not a problem-center warning. Preserve it for support
                # diagnostics without asking the user to review a valid PDF.
                result["validationNotes"] = [str(content_diag["pdfTrailingWarning"])]
        if html_profile and html_profile.get("valid"):
            result["contentKind"] = "HTML_DOCUMENT"
            result["htmlDocumentValidated"] = True
            result["htmlValidationMode"] = html_profile.get("mode")
        if udf_validation_mode:
            result["contentKind"] = "UDF_DOCUMENT"
            result["udfDocumentValidated"] = True
            result["udfValidationMode"] = udf_validation_mode
        if image_profile and image_profile.get("valid"):
            result["contentKind"] = "IMAGE_DOCUMENT"
            result["imageDocumentValidated"] = True
            result["imageDetectedType"] = image_profile.get("kind")
            result["imageValidationMode"] = image_profile.get("mode")
            result["imageWidth"] = image_profile.get("width")
            result["imageHeight"] = image_profile.get("height")
            result["imageTrailingBytes"] = image_profile.get("trailingBytes")
            result["imageTrailingKind"] = image_profile.get("trailingKind")
            image_mode = str(image_profile.get("mode") or "")
            if image_profile.get("fallbackFromMode"):
                result["imageFallbackFromMode"] = image_profile.get("fallbackFromMode")
            if image_mode.startswith("jpeg_uyap_compatible"):
                notes = list(result.get("validationNotes") or [])
                notes.append("JPEG, standart sıkı kontrolden geçmedi; UYAP/scanner uyumlu yapısal doğrulama ile güvenli biçimde doğrulandı.")
                result["validationNotes"] = notes
            elif image_mode == "jpeg_windows_wic_decode":
                notes = list(result.get("validationNotes") or [])
                notes.append("JPEG, dahili marker kontrolünden geçmedi; Windows WIC gerçek raster decoderı ile açılıp boyutları doğrulanarak kabul edildi.")
                result["validationNotes"] = notes
            elif image_mode == "jpeg_windows_gdiplus_decode":
                notes = list(result.get("validationNotes") or [])
                notes.append("JPEG, WIC/dahili marker kontrolünden geçmedi; Windows GDI+ gerçek görüntü decoderı ile açılıp boyutları doğrulanarak kabul edildi.")
                result["validationNotes"] = notes
            elif image_mode in ("jpeg_uyap_trailing_preserved_wic", "jpeg_uyap_trailing_preserved_gdiplus"):
                notes = list(result.get("validationNotes") or [])
                notes.append("UYAP kaynaklı JPEG, EOI sonrasındaki sınırlı kaynak metadatası korunarak gerçek Windows raster decoderı ile doğrulandı; orijinal baytlar değiştirilmedi.")
                result["validationNotes"] = notes

        if opaque_validation_mode:
            result["contentKind"] = "UYAP_OPAQUE_BINARY"
            result["opaqueBinaryValidated"] = True
            result["opaqueValidationMode"] = opaque_validation_mode
            notes = list(result.get("validationNotes") or [])
            notes.append("UYAP kaynağı ve tarayıcı byte kanıtı birebir doğrulandı; dosya biçimi tanınmadığı için baytları değiştirilmeden opak ikili evrak olarak korundu.")
            result["validationNotes"] = notes

        if target_budget:
            result.update(target_budget)
            result["targetPathLength"] = _win_path_len(final_target)
            notes = list(result.get("validationNotes") or [])
            notes.append("Dosya adı Windows'un 260 karakterlik yol sınırına sığması için kısaltıldı; evrak numarası ve uzantı korundu.")
            result["validationNotes"] = notes

        prepare_started = time.perf_counter()
        connection.execute('INSERT INTO transfer_journal(attempt_id,request_key,result_json) VALUES(?,?,?)',
                           (attempt_id, request_key, json.dumps(result, ensure_ascii=False)))
        connection.commit()
        perf["journalPrepareMs"] = _perf_ms(prepare_started)

        move_started = time.perf_counter()
        if not reuse:
            try:
                os.replace(source, final_target)
            except FileNotFoundError as error:
                if not source.is_file():
                    raise
                # Source is intact, so the target side failed. Drop the
                # uncommitted journal row instead of leaving it behind.
                connection.execute('DELETE FROM transfer_journal WHERE attempt_id=? AND committed=0', (attempt_id,))
                connection.commit()
                return finish(_target_unavailable_payload(error, source, final_target))
        perf["moveMs"] = _perf_ms(move_started)

        commit_started = time.perf_counter()
        record_attempt(
            connection, message, "VERIFIED", commit=False, local_path=str(final_target),
            size=size, sha256=digest,
            validation_kind=result.get("contentKind"),
            validation_mode=(result.get("htmlValidationMode") or result.get("udfValidationMode") or result.get("imageValidationMode") or result.get("opaqueValidationMode")),
        )
        connection.execute('UPDATE transfer_journal SET committed=1 WHERE attempt_id=?', (attempt_id,))
        connection.commit()
        perf["databaseCommitMs"] += _perf_ms(commit_started)
        if reuse:
            source.unlink()
        return finish(result)
    finally:
        connection.close()


def _partition_documents(docs, part_size_bytes, max_documents_per_part=0):
    """Create deterministic archive parts without splitting a document."""
    limit = max(1, min(int(part_size_bytes or DEFAULT_ARCHIVE_PART_BYTES), MAX_ARCHIVE_PART_BYTES))
    max_docs = max(0, int(max_documents_per_part or 0))
    groups, current, current_bytes = [], [], 0
    for doc in docs:
        size = max(0, int(doc.get("size") or 0))
        would_overflow = current and current_bytes + size > limit
        would_hit_count = current and max_docs and len(current) >= max_docs
        if would_overflow or would_hit_count:
            groups.append(current)
            current, current_bytes = [], 0
        current.append(doc)
        current_bytes += size
    if current:
        groups.append(current)
    return groups


def _part_manifest(session_id, part_number, part_count, filename, docs, part_size_bytes):
    return {
        "schemaVersion": 1,
        "archiveFormat": "UYAP_CHUNKED_ZIP_V1",
        "sessionId": session_id,
        "partNumber": part_number,
        "partCount": part_count,
        "filename": filename,
        "documentCount": len(docs),
        "uncompressedBytes": sum(max(0, int(d.get("size") or 0)) for d in docs),
        "partSizeTargetBytes": int(part_size_bytes),
        "documents": [{
            "documentId": d.get("documentId"),
            "sourceDocumentId": d.get("sourceDocumentId"),
            "targetPath": d.get("targetPath"),
            "size": int(d.get("size") or 0),
            "sha256": d.get("sha256")
        } for d in docs]
    }


def _verify_archive_part(zip_path, expected_manifest):
    expected_docs = expected_manifest["documents"]
    expected_names = [d["targetPath"] for d in expected_docs] + [ARCHIVE_PART_MANIFEST]
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or set(names) != set(expected_names):
                return False, "PART_MEMBER_MISMATCH"
            if archive.testzip() is not None:
                return False, "PART_CRC_FAILED"
            actual_manifest = json.loads(archive.read(ARCHIVE_PART_MANIFEST).decode("utf-8"))
            if actual_manifest != expected_manifest:
                return False, "PART_MANIFEST_MISMATCH"
            for doc in expected_docs:
                digest = hashlib.sha256()
                with archive.open(doc["targetPath"]) as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                if digest.hexdigest() != doc["sha256"]:
                    return False, "PART_DOCUMENT_HASH_MISMATCH"
        return True, None
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile, RuntimeError, EOFError):
        return False, "PART_OPEN_FAILED"


def _build_or_reuse_archive_part(output, session_id, part_number, part_count, docs, row_by_id, part_size_bytes):
    filename = f"archive-part-{part_number:03d}.zip"
    zip_path = output / filename
    temp_zip = output / (filename + ".tmp")
    manifest = _part_manifest(session_id, part_number, part_count, filename, docs, part_size_bytes)
    reused = False
    if zip_path.is_file():
        valid, _ = _verify_archive_part(zip_path, manifest)
        if valid:
            reused = True
        else:
            zip_path.unlink(missing_ok=True)
    if not reused:
        temp_zip.unlink(missing_ok=True)
        with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED, allowZip64=True, compresslevel=1) as archive:
            for doc in docs:
                row = row_by_id[str(doc["documentId"])]
                archive.write(Path(row[4]), doc["targetPath"])
            archive.writestr(ARCHIVE_PART_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        valid, error = _verify_archive_part(temp_zip, manifest)
        if not valid:
            temp_zip.unlink(missing_ok=True)
            raise ValueError(f"ZIP parçası doğrulanamadı: {filename}: {error}")
        os.replace(temp_zip, zip_path)
    valid, error = _verify_archive_part(zip_path, manifest)
    if not valid:
        raise ValueError(f"ZIP parçası yeniden doğrulanamadı: {filename}: {error}")
    return {
        "partNumber": part_number,
        "filename": filename,
        "documentCount": len(docs),
        "uncompressedBytes": manifest["uncompressedBytes"],
        "zipBytes": zip_path.stat().st_size,
        "sha256": sha256_file(zip_path),
        "status": "VERIFIED",
        "reused": reused,
        "oversizeDocument": len(docs) == 1 and manifest["uncompressedBytes"] > int(part_size_bytes)
    }



def _bounded_ids(values, limit=RESPONSE_ID_SAMPLE_LIMIT):
    return sorted(set(str(value) for value in (values or []) if value))[:max(1, int(limit))]


def _write_final_diagnostics(root: Path, session_id: str, payload):
    try:
        folder = root / "UYAP_Arsiv" / "_diagnostics" / hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:24]
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / "final-integrity.json"
        temp = folder / "final-integrity.json.tmp"
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, target)
        return str(target)
    except OSError:
        return None

def _committed_html_validation_proofs(connection, session_id: str):
    """Return byte-bound HTML validation proofs from committed transfer journals.

    RC15.9.13 already persisted htmlDocumentValidated in result_json, but final
    packaging ignored it and revalidated HTML without source/correlation context.
    The proof is accepted only when session, document id, final path, size and
    SHA-256 all still match the current archive bytes.
    """
    proofs = {}
    try:
        rows = connection.execute(
            "SELECT request_key, result_json, committed FROM transfer_journal WHERE committed=1"
        ).fetchall()
    except sqlite3.Error:
        return proofs
    for request_key_raw, result_raw, committed in rows:
        if not committed:
            continue
        try:
            request_key = json.loads(request_key_raw)
            result = json.loads(result_raw)
            if not isinstance(request_key, list) or len(request_key) < 2:
                continue
            if str(request_key[0]) != str(session_id):
                continue
            document_id = str(request_key[1] or "").strip()
            if not document_id or result.get("valid") is not True or result.get("htmlDocumentValidated") is not True:
                continue
            extension = str(result.get("extension") or "").lower()
            if extension not in (".html", ".htm"):
                continue
            local_path = str(result.get("localPath") or "").strip()
            digest = str(result.get("sha256") or "").strip().lower()
            size = int(result.get("size"))
            if not local_path or not re.fullmatch(r"[0-9a-f]{64}", digest) or size < 0:
                continue
            proof = {
                "path": os.path.normcase(str(Path(local_path).resolve())),
                "size": size,
                "sha256": digest,
                "mode": str(result.get("htmlValidationMode") or "journal_html_validation"),
            }
            proofs.setdefault(document_id, []).append(proof)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return proofs


def _matches_html_validation_proof(proofs, document_id, path: Path, size: int, digest: str):
    normalized_path = os.path.normcase(str(path.resolve()))
    for proof in proofs.get(str(document_id), []):
        if (
            proof.get("path") == normalized_path
            and int(proof.get("size") or -1) == int(size)
            and str(proof.get("sha256") or "").lower() == str(digest or "").lower()
        ):
            return True, proof.get("mode")
    return False, None


def finalize_archive(message):
    root = Path(message["downloadsRoot"]).resolve()
    session_id = message["sessionId"]
    expected = int(message.get("expected") or 0)
    inventory = message.get("documents") or []
    ids = message.get("documentIds") or [item.get("documentId") for item in inventory if item.get("documentId")]
    part_size_bytes = max(1, min(int(message.get("partSizeBytes") or DEFAULT_ARCHIVE_PART_BYTES), MAX_ARCHIVE_PART_BYTES))
    max_documents_per_part = max(1, int(message.get("maxDocumentsPerPart") or DEFAULT_MAX_DOCUMENTS_PER_PART))
    allow_partial = bool(message.get("allowPartial"))
    source_inventory_total = int(message.get("sourceInventoryTotal") or expected)
    user_excluded_documents = list(message.get("userExcludedDocuments") or [])
    connection = database(root)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        rows = connection.execute(
            "SELECT document_id, case_id, display_name, uyap_path, local_path, size, sha256, status, source_fingerprint, updated_at, validation_kind, validation_mode "
            "FROM documents WHERE session_id=? ORDER BY uyap_path, display_name", (session_id,)
        ).fetchall()
        inventory_by_id = {str(item.get("documentId")): item for item in inventory if item.get("documentId")}
        committed_html_proofs = _committed_html_validation_proofs(connection, session_id)
        inventory_ids = [str(value) for value in ids if value]
        source_ids = set(inventory_ids)
        excluded_ids = {str(item.get("documentId")) for item in user_excluded_documents if item.get("documentId")}

        # Finalization validates the exact supplied package inventory, not every
        # historical row attached to the session. User-confirmed exclusions can
        # legitimately leave an old native DB row behind and must not poison the
        # ZIP gate. Any other extra row remains a fail-closed inventory conflict.
        package_rows = [row for row in rows if str(row[0]) in source_ids]
        unexpected_rows = [row for row in rows if str(row[0]) not in source_ids and str(row[0]) not in excluded_ids]

        valid_rows, invalid_ids, fingerprint_mismatch, repaired_ids = [], [], [], []
        for row in package_rows:
            path = Path(row[4] or "").resolve()
            meta = inventory_by_id.get(str(row[0]), {})
            try:
                # Stored status is an observation from a previous attempt, not
                # immutable truth. Revalidate the bytes on every finalization so
                # a transient/old INTEGRITY_FAILED marker cannot permanently
                # poison a physically valid archive.
                archive_path_ok = root / "UYAP_Arsiv" in path.parents and path.is_file()
                actual_size = path.stat().st_size if archive_path_ok else -1
                digest = sha256_file(path) if archive_path_ok and actual_size == row[5] else None
                bytes_match = bool(archive_path_ok and actual_size == row[5] and digest == row[6])

                content_valid = False
                if bytes_match:
                    # RC15.9.18: byte-bound native validation proofs survive
                    # final packaging. Some UYAP JPEGs require exact provenance
                    # to accept a safe post-EOI metadata trailer, and unknown
                    # proprietary UYAP binary attachments can be preserved only
                    # through the strict opaque-binary proof. If size/SHA changed,
                    # bytes_match is false and no persisted proof is trusted.
                    persisted_kind = str(row[10] or "").strip()
                    persisted_mode = str(row[11] or "").strip()
                    if persisted_kind in {"IMAGE_DOCUMENT", "UYAP_OPAQUE_BINARY"} and persisted_mode:
                        content_valid = True
                    else:
                        # Historical/local archive rows can carry a stale or generic
                        # suffix (.bin/.uyap/application-download). Detect from bytes,
                        # then run the structural validator for the detected family.
                        detected_extension = detect_extension(path, path.suffix).lower()
                        if detected_extension in (".html", ".htm"):
                            # HTML remains stricter than other types because a
                            # login/error page is also structurally valid HTML.
                            persisted_html_proof = (
                                persisted_kind == "HTML_DOCUMENT" and bool(persisted_mode)
                            )
                            journal_html_proof, _ = _matches_html_validation_proof(
                                committed_html_proofs, row[0], path, actual_size, digest
                            )
                            if persisted_html_proof or journal_html_proof:
                                content_valid = True
                            else:
                                content_valid = validate_content(path, detected_extension)[0]
                        else:
                            content_valid = validate_content(path, detected_extension)[0]

                valid = bytes_match and content_valid
                supplied_fp = meta.get("sourceFingerprint")
                if supplied_fp and row[8] and supplied_fp != row[8]:
                    valid = False
                    fingerprint_mismatch.append(row[0])
            except (OSError, ValueError):
                valid = False
            if valid:
                valid_rows.append(row)
                if row[7] != "VERIFIED":
                    connection.execute("UPDATE documents SET status='VERIFIED' WHERE session_id=? AND document_id=?", (session_id, row[0]))
                    repaired_ids.append(str(row[0]))
            else:
                invalid_ids.append(row[0])
                connection.execute("UPDATE documents SET status='INTEGRITY_FAILED' WHERE session_id=? AND document_id=?", (session_id, row[0]))

        # RC15.9.18: resolve stale duplicate-local-path mappings by evidence
        # strength. A file freshly validated in this session is stronger than a
        # historical reconciliation row. Exact logical target-path ownership is
        # stronger still. If several document identities intentionally reference
        # the same immutable bytes but have distinct logical target paths, sharing
        # the source file is safe because ZIP entries are written using each
        # document's targetPath, not its local source path.
        path_groups = {}
        for row in valid_rows:
            path_groups.setdefault(os.path.normcase(str(Path(row[4]).resolve())), []).append(row)
        duplicate_recovery_ids = []
        retained_rows = []
        safe_shared_paths = set()
        for physical_key, group in path_groups.items():
            if len(group) == 1:
                retained_rows.extend(group)
                continue

            actual_rel = None
            try:
                actual_rel = Path(group[0][4]).resolve().relative_to(root).as_posix().replace("\\", "/").casefold()
            except (OSError, ValueError):
                actual_rel = None

            exact_path_rows = []
            for row in group:
                meta = inventory_by_id.get(str(row[0]), {})
                expected_rel = str(meta.get("targetPath") or "").replace("\\", "/").strip("/").casefold()
                if actual_rel and expected_rel and expected_rel == actual_rel:
                    exact_path_rows.append(row)

            fresh_rows = [
                row for row in group
                if str((inventory_by_id.get(str(row[0]), {}) or {}).get("completedAt") or "").strip()
            ]

            matching_rows = []
            for row in group:
                meta = inventory_by_id.get(str(row[0]), {})
                expected_key = _archive_file_match_key(meta.get("targetPath"))
                actual_key = _row_file_match_key(root, row[4])
                if expected_key and actual_key and expected_key == actual_key:
                    matching_rows.append(row)

            owner = None
            if len(exact_path_rows) == 1:
                owner = exact_path_rows[0]
            elif len(fresh_rows) == 1:
                owner = fresh_rows[0]
            elif len(matching_rows) == 1:
                owner = matching_rows[0]

            if owner is not None:
                retained_rows.append(owner)
                for row in group:
                    if row is owner:
                        continue
                    invalid_ids.append(row[0])
                    duplicate_recovery_ids.append(str(row[0]))
                    connection.execute(
                        "UPDATE documents SET status='INTEGRITY_FAILED' WHERE session_id=? AND document_id=?",
                        (session_id, row[0]),
                    )
                continue

            logical_targets = [
                str((inventory_by_id.get(str(row[0]), {}) or {}).get("targetPath") or "").replace("\\", "/").strip("/").casefold()
                for row in group
            ]
            row_hashes = {str(row[6] or "") for row in group}
            row_sizes = {int(row[5] or 0) for row in group}
            if (
                all(logical_targets)
                and len(set(logical_targets)) == len(logical_targets)
                and len(row_hashes) == 1
                and len(row_sizes) == 1
            ):
                retained_rows.extend(group)
                safe_shared_paths.add(physical_key)
                continue

            retained_rows.extend(group)

        if duplicate_recovery_ids:
            valid_rows = retained_rows

        verified_ids = {str(row[0]) for row in valid_rows}
        missing = sorted(source_ids - verified_ids)
        unexpected = sorted({str(row[0]) for row in unexpected_rows})
        duplicate_document_ids = sorted(x for x, count in Counter(inventory_ids).items() if count > 1)
        local_paths = [os.path.normcase(str(Path(row[4]).resolve())) for row in valid_rows]
        duplicate_paths = sorted(
            path for path, count in Counter(local_paths).items()
            if count > 1 and path not in safe_shared_paths
        )
        source_document_ids = [str(inventory_by_id.get(str(row[0]), {}).get("sourceDocumentId") or "").strip() for row in valid_rows]
        source_document_ids = [x for x in source_document_ids if x]
        duplicate_source_ids = sorted(x for x, count in Counter(source_document_ids).items() if count > 1)

        # Numeric source ids learned from Chrome/UYAP download filenames are useful
        # correlation hints, but they are not always globally unique document identities.
        # Only duplicates that involve a source id which was authoritative at scan time
        # block the final gate. Learned-only duplicates remain visible in diagnostics,
        # while documentId + path + fingerprint + SHA-256/content validation stay strict.
        source_groups = {}
        for row in valid_rows:
            meta = inventory_by_id.get(str(row[0]), {})
            source_id = str(meta.get("sourceDocumentId") or "").strip()
            if not source_id:
                continue
            source_groups.setdefault(source_id, []).append({
                "documentId": str(row[0]),
                "origin": str(meta.get("sourceDocumentIdOrigin") or "").strip(),
                "identityMethod": str(meta.get("identityMethod") or "").strip(),
            })
        blocking_duplicate_source_ids = []
        learned_duplicate_source_ids = []
        for source_id in duplicate_source_ids:
            group = source_groups.get(source_id, [])
            has_authoritative_identity = any(
                item.get("origin") == "scan" or item.get("identityMethod") == "source-id"
                for item in group
            )
            explicitly_learned_only = bool(group) and all(
                item.get("origin") in {"download-filename", "download-correlation", "legacy-post-scan-inferred"}
                or (item.get("identityMethod") and item.get("identityMethod") != "source-id")
                for item in group
            )
            if has_authoritative_identity or not explicitly_learned_only:
                # Fail closed for legacy/unknown provenance. A duplicate is ignored
                # only when every record proves that the numeric id was learned
                # after scan and was not used as the document identity.
                blocking_duplicate_source_ids.append(source_id)
            else:
                learned_duplicate_source_ids.append(source_id)

        hashes = [row[6] for row in valid_rows if row[6]]
        duplicate_hashes = sorted(x for x, count in Counter(hashes).items() if count > 1)

        exact = (expected > 0 and len(inventory_ids) == len(set(inventory_ids)) == expected
                 and source_ids == verified_ids and len(package_rows) == expected and not invalid_ids
                 and not unexpected and not duplicate_paths and not blocking_duplicate_source_ids
                 and not fingerprint_mismatch
                 and (not inventory or len(inventory_by_id) == expected))
        verified = len(valid_rows)
        unresolved = max(len(missing) + len(unexpected), len(invalid_ids), expected - verified, 0)
        dangerous_conflicts = bool(
            unexpected or duplicate_document_ids or blocking_duplicate_source_ids or duplicate_paths or fingerprint_mismatch
        )
        # A partial export is deliberately narrower than a complete archive: it is
        # allowed only when the verified subset is internally consistent. Missing,
        # quarantined, unsupported or review-required documents may remain outside
        # the ZIP, but identity/path conflicts never pass even the partial gate.
        partial_eligible = bool(allow_partial and verified > 0 and not dangerous_conflicts)
        state = "PACKAGING" if (exact or partial_eligible) else "INCOMPLETE"
        def save_state(value):
            connection.execute(
                "INSERT INTO archive_jobs VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                "expected=excluded.expected, verified=excluded.verified, unresolved=excluded.unresolved, "
                "current_state=excluded.current_state, updated_at=excluded.updated_at",
                (session_id, expected, verified, unresolved, value, now))
            connection.commit()
        save_state(state)
        diagnostics_payload = {
            "sessionId": session_id,
            "generatedAt": now,
            "expected": expected,
            "verified": verified,
            "missingIds": missing,
            "unexpectedIds": unexpected,
            "invalidIds": sorted(set(invalid_ids)),
            "duplicateDocumentIds": duplicate_document_ids,
            "duplicateSourceDocumentIds": duplicate_source_ids,
            "blockingDuplicateSourceDocumentIds": blocking_duplicate_source_ids,
            "learnedDuplicateSourceDocumentIds": learned_duplicate_source_ids,
            "duplicatePaths": duplicate_paths,
            "duplicateHashes": duplicate_hashes,
            "fingerprintMismatchIds": sorted(set(fingerprint_mismatch))
        }
        diagnostics_path = _write_final_diagnostics(root, session_id, diagnostics_payload) if any([
            missing, unexpected, invalid_ids, duplicate_document_ids, duplicate_source_ids, duplicate_paths, fingerprint_mismatch
        ]) else None
        integrity = {
            "sourceCount": len(source_ids), "sourceInventoryTotal": source_inventory_total,
            "userExcludedCount": len(user_excluded_documents),
            "verifiedCount": verified, "manifestCount": 0,
            "archiveFileCount": verified, "zipEntryCount": 0, "partCount": 0, "verifiedPartCount": 0,
            "missingCount": len(missing), "unexpectedCount": len(unexpected),
            "invalidCount": len(invalid_ids),
            # Browser-side status is only a pre-finalization observation. Native
            # revalidation above is the authoritative post-finalization state.
            # Count only inventory entries that still lack verified bytes after
            # that revalidation; keep the reported value for diagnostics.
            "reviewRequiredCount": len(missing),
            "reportedReviewRequiredCount": int(message.get("reviewRequired") or 0),
            "repairedStatusCount": len(repaired_ids),
            "duplicateDocumentCount": len(duplicate_document_ids),
            "duplicateSourceDocumentCount": len(duplicate_source_ids),
            "blockingDuplicateSourceDocumentCount": len(blocking_duplicate_source_ids),
            "learnedDuplicateSourceDocumentCount": len(learned_duplicate_source_ids),
            "duplicatePathCount": len(duplicate_paths),
            "sharedBytePathCount": len(safe_shared_paths),
            "duplicateHashCount": len(duplicate_hashes),
            "fingerprintMismatchCount": len(set(fingerprint_mismatch)),
            "duplicateDocumentIds": _bounded_ids(duplicate_document_ids),
            "duplicateSourceDocumentIds": _bounded_ids(duplicate_source_ids),
            "blockingDuplicateSourceDocumentIds": _bounded_ids(blocking_duplicate_source_ids),
            "learnedDuplicateSourceDocumentIds": _bounded_ids(learned_duplicate_source_ids),
            "duplicatePaths": _bounded_ids(duplicate_paths),
            "duplicateHashes": _bounded_ids(duplicate_hashes),
            "fingerprintMismatchIds": _bounded_ids(fingerprint_mismatch), "gatePassed": False
        }
        result = {"ok": True, "archiveStatus": "INCOMPLETE", "expected": expected,
                  "verified": verified, "unresolved": unresolved,
                  "invalidCount": len(set(invalid_ids)), "missingCount": len(missing), "unexpectedCount": len(unexpected),
                  "invalidIds": _bounded_ids(invalid_ids), "missingIds": _bounded_ids(missing),
                  "unexpectedIds": _bounded_ids(unexpected), "diagnosticsPath": diagnostics_path,
                  "repairedIds": _bounded_ids(repaired_ids),
                  "integrity": integrity}
        if not exact and not partial_eligible:
            result["detail"] = (
                "Final paketleme güvenlik kontrolü geçmedi: "
                f"eksik={len(missing)}, geçersiz={len(set(invalid_ids))}, beklenmeyen={len(unexpected)}, "
                f"yinelenenBelge={len(duplicate_document_ids)}, yinelenenKaynak={len(blocking_duplicate_source_ids)}, "
                f"yinelenenYol={len(duplicate_paths)}, parmakIziUyusmazligi={len(set(fingerprint_mismatch))}."
            )
            return result

        archive_base = root / "UYAP_Arsiv"
        case_roots = set()
        for row in valid_rows:
            relative = Path(row[4]).resolve().relative_to(archive_base)
            # A document may live directly under the case directory:
            # UYAP_Arsiv/<court>/<case>/<file>.  Older finalization code
            # incorrectly required at least one extra UYAP subfolder and
            # rejected this perfectly valid three-part relative path.
            if len(relative.parts) < 3:
                raise ValueError("Dava arşiv kökü belirlenemedi")
            case_roots.add(archive_base / relative.parts[0] / relative.parts[1])
        if len(case_roots) != 1:
            raise ValueError("Tek final pakette birden çok dava kökü olamaz")
        case_root = case_roots.pop()
        pending_token = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:16]
        output = case_root / "_exports" / ("_pending_" + pending_token)
        output.mkdir(parents=True, exist_ok=True)
        manifest_path = output / "_manifest.json"
        csv_path = output / "_verification.csv"
        report_path = output / "_archive-report.html"
        db_path = output / "_archive.db"
        parts_manifest_path = output / "_parts.json"
        checksums_path = output / "_checksums.sha256"

        docs = []
        for row in valid_rows:
            meta = inventory_by_id.get(str(row[0]), {})
            local_rel = Path(row[4]).resolve().relative_to(case_root).as_posix()
            docs.append({
                "documentId": row[0], "sourceSystem": meta.get("sourceSystem") or "UYAP",
                "sourceContext": meta.get("sourceContext") or None,
                "institutionName": meta.get("institutionName") or meta.get("courtName") or None,
                "courtName": meta.get("courtName") or None, "sourceDocumentId": meta.get("sourceDocumentId") or None,
                "sourceIdentityHash": meta.get("sourceIdentityHash") or None,
                "sourceFingerprint": row[8] or meta.get("sourceFingerprint") or None,
                "caseId": row[1], "folderNames": meta.get("folderNames") or [], "documentDate": meta.get("documentDate") or None,
                "displayName": row[2], "uyapPath": row[3], "targetPath": local_rel, "localPath": local_rel,
                "extension": Path(row[4]).suffix.lower(), "size": row[5], "sha256": row[6],
                "verifiedAt": row[9], "status": row[7]
            })
        docs.sort(key=lambda d: (d["uyapPath"] or "", d["displayName"] or "", str(d["documentId"])))
        integrity["manifestCount"] = len(docs)
        integrity["archiveFileCount"] = len({d["targetPath"] for d in docs})
        full_gate_passed = (
            integrity["sourceCount"] == integrity["verifiedCount"] == integrity["manifestCount"] == integrity["archiveFileCount"] == expected
            and integrity["missingCount"] == integrity["unexpectedCount"] == integrity["invalidCount"] == integrity["reviewRequiredCount"] == 0
            and not duplicate_document_ids and not blocking_duplicate_source_ids and not duplicate_paths and not fingerprint_mismatch
        )
        partial_gate_base = (
            partial_eligible
            and integrity["manifestCount"] == integrity["archiveFileCount"] == verified
            and integrity["unexpectedCount"] == 0
            and not duplicate_document_ids and not blocking_duplicate_source_ids and not duplicate_paths and not fingerprint_mismatch
        )
        packaging_gate_passed = full_gate_passed or partial_gate_base
        if not packaging_gate_passed:
            save_state("INCOMPLETE")
            result["detail"] = "Final paketleme güvenlik kapısı başarısız."
            return result

        groups = _partition_documents(docs, part_size_bytes, max_documents_per_part)
        part_count = len(groups)
        for part_index, group in enumerate(groups, start=1):
            filename = f"archive-part-{part_index:03d}.zip"
            for doc in group:
                doc["archivePart"] = filename
                doc["archivePartIndex"] = part_index
        row_by_id = {str(r[0]): r for r in valid_rows}
        # ZIP creation needs extra disk space in addition to the verified source
        # files. Estimate only parts that cannot be safely reused from a previous
        # interrupted finalization, then keep a modest reserve for metadata and
        # filesystem overhead. Fail before writing a multi-gigabyte temp part
        # rather than discovering a full disk halfway through the final gate.
        pending_uncompressed = 0
        for part_index, group in enumerate(groups, start=1):
            filename = f"archive-part-{part_index:03d}.zip"
            zip_path = output / filename
            expected_manifest = _part_manifest(session_id, part_index, part_count, filename, group, part_size_bytes)
            reusable = False
            if zip_path.is_file():
                reusable, _ = _verify_archive_part(zip_path, expected_manifest)
            if not reusable:
                pending_uncompressed += sum(int(doc.get("size") or 0) for doc in group)
        disk_reserve = 256 * 1024 * 1024
        disk_required = int(pending_uncompressed * 1.05) + disk_reserve
        disk_free = shutil.disk_usage(output).free
        integrity["diskFreeBytes"] = int(disk_free)
        integrity["diskRequiredBytes"] = int(disk_required)
        if disk_free < disk_required:
            save_state("INCOMPLETE")
            result.update({
                "ok": False,
                "error": "INSUFFICIENT_DISK_SPACE",
                "errorCode": "INSUFFICIENT_DISK_SPACE",
                "detail": f"Parçalı ZIP için en az {disk_required} bayt boş alan gerekli; kullanılabilir alan {disk_free} bayt.",
                "integrity": integrity,
            })
            return result
        planned_names = {f"archive-part-{i:03d}.zip" for i in range(1, part_count + 1)}
        for stale in output.glob("archive-part-*.zip"):
            if stale.name not in planned_names:
                stale.unlink(missing_ok=True)
        for stale in output.glob("archive-part-*.zip.tmp"):
            stale.unlink(missing_ok=True)

        archive_parts = []
        for part_index, group in enumerate(groups, start=1):
            archive_parts.append(_build_or_reuse_archive_part(
                output, session_id, part_index, part_count, group, row_by_id, part_size_bytes
            ))
        integrity["partCount"] = part_count
        integrity["verifiedPartCount"] = sum(1 for p in archive_parts if p["status"] == "VERIFIED")
        integrity["zipEntryCount"] = sum(p["documentCount"] for p in archive_parts)
        parts_verified = integrity["partCount"] > 0 and integrity["verifiedPartCount"] == integrity["partCount"]
        integrity["partialGatePassed"] = bool(
            partial_gate_base and integrity["zipEntryCount"] == verified and parts_verified
        )
        integrity["gatePassed"] = bool(
            full_gate_passed and integrity["zipEntryCount"] == expected and parts_verified
        )
        if not integrity["gatePassed"] and not integrity["partialGatePassed"]:
            save_state("INCOMPLETE")
            result["detail"] = "Parçalı ZIP bütünlük kapısı başarısız."
            return result

        archive_status = "VERIFIED_COMPLETE" if integrity["gatePassed"] else "PARTIAL_WITH_ERRORS"
        source_systems = sorted({str(d.get("sourceSystem") or "UYAP") for d in docs})
        manifest = {"schemaVersion": 5, "sessionId": session_id, "generatedAt": now,
                    "archiveStatus": archive_status, "archiveFormat": "UYAP_CHUNKED_ZIP_V1",
                    "partSizeTargetBytes": part_size_bytes, "maxDocumentsPerPart": max_documents_per_part,
                    "scope": "Supplied source inventory",
                    "sourceSystems": source_systems,
                    "primarySourceSystem": source_systems[0] if len(source_systems) == 1 else None,
                    "completionGate": ("source=verified=manifest=archive=zipEntries; missing=review_required=0; allParts=VERIFIED"
                        if archive_status == "VERIFIED_COMPLETE" else
                        "PARTIAL ONLY: verified subset=manifest=archive=zipEntries; unresolved documents excluded; allParts=VERIFIED"),
                    "sourceExpectedCount": expected,
                    "sourceInventoryTotal": source_inventory_total,
                    "verifiedPackagedCount": verified,
                    "userExcludedCount": len(user_excluded_documents),
                    "userExcludedDocuments": user_excluded_documents,
                    "unresolvedCount": unresolved,
                    "errorsFolder": "UYAP_Arsiv/Hatalı Dosyalar",
                    "diagnosticsPath": diagnostics_path,
                    "integrity": integrity,
                    "archiveParts": [{k: v for k, v in part.items() if k != "reused"} for part in archive_parts],
                    "documents": docs}
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        manifest_path.write_bytes(manifest_bytes)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()

        with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(docs[0].keys()), delimiter=";")
            writer.writeheader()
            for doc in docs:
                writer.writerow({k: ("'" + v if isinstance(v, str) and v.startswith(("=", "+", "-", "@")) else v) for k, v in doc.items()})

        part_rows = "".join(
            f"<li>{html.escape(p['filename'])}: {p['documentCount']} evrak, {p['zipBytes']} bayt, SHA-256 {html.escape(p['sha256'])}</li>"
            for p in archive_parts
        )
        completion_text = (
            "<strong>Final bütünlük kapısı: GEÇTİ</strong> | Eksik: 0 | Müdahale gereken: 0"
            if archive_status == "VERIFIED_COMPLETE" else
            f"<strong>KISMİ ARŞİV: doğrulanan dosyalar güvenli paketlendi</strong> | Tamlık kapısı: GEÇMEDİ | Çözümlenmemiş: {unresolved}"
        )
        report_path.write_text("<!doctype html><meta charset='utf-8'><title>UYAP Arşiv Bütünlük Raporu</title>"
            + "<h1>UYAP Arşiv Bütünlük Raporu</h1>"
            + f"<p>Kaynak envanteri: {source_inventory_total} | Paket kapsamı: {expected} | Kullanıcı hariç tuttu: {len(user_excluded_documents)} | Doğrulanan/Paketlenen: {verified} | Manifest: {len(docs)} | Arşiv dosyası: {integrity['archiveFileCount']}</p>"
            + f"<p>{completion_text} | ZIP parçası: {integrity['verifiedPartCount']}/{part_count} doğrulandı</p>"
            + ("<p>Hatalı veya doğrulanamayan içerikler <code>UYAP_Arsiv/Hatalı Dosyalar</code> altında korunur. Bu paket eksiksizlik iddiası taşımaz.</p>" if archive_status != "VERIFIED_COMPLETE" else "")
            + "<h2>Arşiv parçaları</h2><ul>" + part_rows + "</ul>"
            + "<p>Kapsam: taramada elde edilen kaynak envanteri ve yerel dosya bütünlüğü. Belge imzalarının hukuki geçerliliği denetlenmez.</p>", encoding="utf-8")

        portable = sqlite3.connect(db_path)
        try:
            portable.execute("CREATE TABLE documents(document_id TEXT PRIMARY KEY, source_system TEXT, source_context TEXT, institution_name TEXT, court_name TEXT, source_document_id TEXT, source_identity_hash TEXT, source_fingerprint TEXT, case_id TEXT, folder_names TEXT, document_date TEXT, display_name TEXT, uyap_path TEXT, target_path TEXT, local_path TEXT, extension TEXT, size INTEGER, sha256 TEXT, verified_at TEXT, status TEXT, archive_part TEXT, archive_part_index INTEGER)")
            portable.executemany("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [(
                d["documentId"], d["sourceSystem"], json.dumps(d.get("sourceContext"), ensure_ascii=False) if d.get("sourceContext") is not None else None,
                d.get("institutionName"), d.get("courtName"), d.get("sourceDocumentId"), d.get("sourceIdentityHash"), d.get("sourceFingerprint"),
                d.get("caseId"), json.dumps(d.get("folderNames") or [], ensure_ascii=False), d.get("documentDate"), d.get("displayName"), d.get("uyapPath"),
                d.get("targetPath"), d.get("localPath"), d.get("extension"), d.get("size"), d.get("sha256"), d.get("verifiedAt"), d.get("status"),
                d.get("archivePart"), d.get("archivePartIndex")
            ) for d in docs])
            portable.commit()
        finally:
            portable.close()

        parts_manifest = {
            "schemaVersion": 1, "archiveFormat": "UYAP_CHUNKED_ZIP_V1", "sessionId": session_id,
            "generatedAt": now, "partSizeTargetBytes": part_size_bytes,
            "partCount": part_count, "verifiedPartCount": integrity["verifiedPartCount"],
            "parts": archive_parts, "manifestSha256": manifest_sha256
        }
        parts_manifest_path.write_text(json.dumps(parts_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        parts_manifest_sha256 = sha256_file(parts_manifest_path)
        checksum_items = [(p["sha256"], p["filename"]) for p in archive_parts]
        checksum_items.extend([
            (manifest_sha256, manifest_path.name),
            (parts_manifest_sha256, parts_manifest_path.name),
            (sha256_file(csv_path), csv_path.name),
            (sha256_file(report_path), report_path.name),
            (sha256_file(db_path), db_path.name),
        ])
        checksums_path.write_text("".join(f"{digest}  {name}\n" for digest, name in checksum_items), encoding="utf-8")
        checksums_sha256 = sha256_file(checksums_path)

        export_id = uuid.uuid4().hex
        visible_stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        final_output = case_root / f"Final_Paket_{visible_stamp}_{export_id[:8]}"
        if final_output.exists():
            final_output = case_root / f"Final_Paket_{visible_stamp}_{export_id[:12]}"
        os.replace(output, final_output)

        # Never mark a job complete merely because the ZIP existed in the
        # temporary export area. After the atomic move, verify that every final
        # ZIP part and the delivery metadata are physically present in the
        # user-visible package directory and that the ZIP hashes are unchanged.
        required_metadata = [
            final_output / manifest_path.name, final_output / parts_manifest_path.name,
            final_output / csv_path.name, final_output / report_path.name,
            final_output / db_path.name, final_output / checksums_path.name,
        ]
        missing_delivery_files = [str(path) for path in required_metadata if not path.is_file()]
        final_parts = []
        for part in archive_parts:
            final_path = final_output / part["filename"]
            if not final_path.is_file():
                missing_delivery_files.append(str(final_path))
                continue
            actual_sha256 = sha256_file(final_path)
            if actual_sha256 != part["sha256"]:
                raise ValueError(f"Final ZIP taşıma sonrası SHA-256 doğrulaması başarısız: {part['filename']}")
            final_parts.append({**part, "path": str(final_path)})
        if missing_delivery_files or len(final_parts) != len(archive_parts):
            raise ValueError("Final paket görünür klasöre taşındı ancak teslim dosyalarının tümü fiziksel olarak doğrulanamadı")

        save_state(archive_status)
        single_part = final_parts[0] if len(final_parts) == 1 else None
        result.update({
            "archiveStatus": archive_status, "archiveFormat": "UYAP_CHUNKED_ZIP_V1", "unresolved": 0 if archive_status == "VERIFIED_COMPLETE" else unresolved,
            "finalPackagePath": str(final_output),
            "manifestPath": str(final_output / manifest_path.name), "manifestSha256": manifest_sha256,
            "partsManifestPath": str(final_output / parts_manifest_path.name), "partsManifestSha256": parts_manifest_sha256,
            "checksumsPath": str(final_output / checksums_path.name), "checksumsSha256": checksums_sha256,
            "archiveParts": final_parts,
            "zipPath": single_part["path"] if single_part else None,
            "zipSha256": single_part["sha256"] if single_part else None,
            "integrity": integrity
        })
        return result
    except Exception:
        connection.execute("UPDATE archive_jobs SET current_state='INCOMPLETE' WHERE session_id=?", (session_id,))
        connection.commit()
        raise
    finally:
        connection.close()

def _reconcile_verified_root_downloads(root: Path, connection, minimum_age_ms=120_000, scan_limit=10_000):
    """Delete only byte-identical root evrak_* duplicates already proven in the archive.

    A Chrome/UYAP native download can land in the downloads root before the
    agent atomically adopts it. If an older extension re-downloaded after a
    transient validation exception, the first copy could remain beside
    UYAP_Arsiv. Cleanup is deliberately fail-closed: exact canonical filename,
    regular non-symlink file, settled age, and a VERIFIED archive row whose
    current bytes hash to the same SHA-256 are all required before unlinking.
    """
    result = {"scanned": 0, "removed": 0, "retained": 0, "errors": 0, "removedBytes": 0}
    now = time.time()
    pattern = re.compile(r"^evrak[_ -]?(\d{4,30})(?: \(\d+\))?\.(pdf|udf|zip|html?|tiff?|jpe?g|png|gif|bmp|webp|svg|xml|rtf|docx?|xlsx?|pptx?|odt|ods|odp|7z|rar|bin)$", re.IGNORECASE)
    try:
        with os.scandir(root) as iterator:
            for entry in iterator:
                if result["scanned"] >= max(1, int(scan_limit or 10_000)):
                    break
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                match = pattern.fullmatch(entry.name)
                if not match:
                    continue
                result["scanned"] += 1
                try:
                    stat = entry.stat(follow_symlinks=False)
                    age_ms = max(0, (now - float(stat.st_mtime)) * 1000)
                    if age_ms < max(0, int(minimum_age_ms or 0)):
                        result["retained"] += 1
                        continue
                    path = Path(entry.path)
                    digest = sha256_file(path)
                    rows = connection.execute(
                        "SELECT local_path, size, sha256 FROM documents "
                        "WHERE status='VERIFIED' AND "
                        "(source_document_id=? OR (size=? AND sha256=?)) "
                        "ORDER BY updated_at DESC",
                        (match.group(1), int(stat.st_size), digest),
                    ).fetchall()
                    duplicate_proven = False
                    for local_path, size, stored_hash in rows:
                        proof = _verified_file_proof(local_path, size, stored_hash)
                        if proof and int(proof[1]) == int(stat.st_size) and str(proof[2]) == digest:
                            duplicate_proven = True
                            break
                    if not duplicate_proven:
                        result["retained"] += 1
                        continue
                    path.unlink()
                    result["removed"] += 1
                    result["removedBytes"] += int(stat.st_size)
                except (OSError, ValueError, TypeError, sqlite3.Error):
                    result["errors"] += 1
    except OSError:
        result["errors"] += 1
    return result


def sync_inventory(message):
    session_id = message["sessionId"]
    documents = message.get("documents") or []
    unchanged = []
    hashes = {}
    match_methods = {}
    physical_proofs = {}
    ambiguous = []
    matched_roots = []
    scope_candidate_rows = 0
    examined_roots = []
    orphan_cleanup = {"scanned": 0, "removed": 0, "retained": 0, "errors": 0, "removedBytes": 0}
    legacy_loose_imported = 0
    legacy_loose_reused = 0

    supplied_root = message.get("downloadsRoot")
    roots = []
    for root in _archive_root_candidates(supplied_root):
        archive_root = root / "UYAP_Arsiv"
        if archive_root.exists() and archive_root.is_dir():
            roots.append(root)
    if not roots:
        fallback = _archive_root_candidates(supplied_root)
        return {
            "ok": True, "unchanged": [], "hashes": {}, "matchMethods": {}, "physicalProofs": {},
            "ambiguous": [], "notFound": len(documents),
            "downloadsRoot": str(fallback[0]) if fallback else None,
            "discoveredRoots": [], "archiveRootCount": 0, "scopeCandidateRows": 0
        }

    def row_dict(row):
        keys = (
            "document_id", "case_id", "display_name", "uyap_path", "local_path", "size", "sha256",
            "source_fingerprint", "source_system", "institution_name", "source_document_id",
            "archive_identity_key", "document_date", "target_path", "updated_at",
            "validation_kind", "validation_mode"
        )
        return dict(zip(keys, row))

    def same_scope(row, document):
        if _canonical_case_id(row.get("case_id")) != _canonical_case_id(document.get("caseId")):
            return False
        row_inst = _identity_text(row.get("institution_name"))
        doc_inst = _identity_text(document.get("institutionName") or document.get("courtName"))
        if row_inst and doc_inst and row_inst != doc_inst:
            return False
        row_source = _identity_text(row.get("source_system"))
        doc_source = _identity_text(document.get("sourceSystem"))
        if row_source and doc_source and row_source != doc_source:
            return False
        return True

    def unique_proven_row(rows, document, proof_cache):
        proven = []
        seen = set()
        proven_paths = set()
        for row in rows:
            if not same_scope(row, document):
                continue
            # RC15.8: historical sessions often contain several VERIFIED DB rows
            # pointing at the exact same immutable archive file. Hashing that same
            # path once per old session made large local reconciliation look frozen.
            # De-duplicate only AFTER a row proves the current bytes, so a stale
            # historical hash cannot hide a later valid row for the same path.
            proof = None
            path_key = ""
            for local_path in _portable_row_paths(
                root, row.get("local_path"), row.get("target_path"),
                row.get("source_document_id"), loose_source_index,
            ):
                path_key = os.path.normcase(str(local_path))
                try:
                    stat = local_path.stat()
                    proof_key = (path_key, int(stat.st_size), int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))), str(row.get("size") or ""), str(row.get("sha256") or ""))
                except (OSError, ValueError, TypeError):
                    proof_key = (path_key, "missing", str(row.get("size") or ""), str(row.get("sha256") or ""))
                if proof_key in proof_cache:
                    candidate_proof = proof_cache[proof_key]
                else:
                    candidate_proof = _verified_file_proof(local_path, row.get("size"), row.get("sha256"))
                    proof_cache[proof_key] = candidate_proof
                if candidate_proof:
                    proof = candidate_proof
                    path_key = os.path.normcase(str(candidate_proof[0]))
                    break
            if not proof:
                continue
            if path_key and path_key in proven_paths:
                continue
            path, size, digest = proof
            if path_key:
                proven_paths.add(path_key)
            signature = (str(path), digest)
            if signature in seen:
                continue
            seen.add(signature)
            proven.append((row, path, size, digest))
        if not proven:
            return None, False
        unique_hashes = {item[3] for item in proven}
        unique_paths = {str(item[1]) for item in proven}
        if len(unique_hashes) == 1 and len(unique_paths) >= 1:
            # Multiple historical session rows may legitimately point at the same
            # immutable bytes. Byte-identical copies are one proof, not ambiguity.
            return proven[0], False
        if len(proven) == 1:
            return proven[0], False
        return None, True

    for root in roots:
        connection = database(root)
        root_matched = False
        physical_cache = {}
        proof_cache = {}
        loose_source_index = {}
        try:
            with os.scandir(root) as iterator:
                for entry in iterator:
                    if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                        continue
                    match = re.fullmatch(
                        r"evrak[_ -]?(\d{4,30})(?: \(\d+\))?\.(?:pdf|udf|uyap|zip|html?|tiff?|jpe?g|png|gif|bmp|webp|svg|xml|rtf|docx?|xlsx?|pptx?|odt|ods|odp|7z|rar|bin)",
                        entry.name,
                        flags=re.IGNORECASE,
                    )
                    if match:
                        loose_source_index.setdefault(match.group(1), []).append(Path(entry.path))
        except OSError:
            pass
        try:
            scope_case_id = str((documents[0] if documents else {}).get("caseId") or "").strip()
            select_sql = (
                "SELECT document_id, case_id, display_name, uyap_path, local_path, size, sha256, "
                "source_fingerprint, source_system, institution_name, source_document_id, "
                "archive_identity_key, document_date, target_path, updated_at, validation_kind, validation_mode "
                "FROM documents WHERE status='VERIFIED'"
            )
            if scope_case_id:
                case_variants = _case_id_variants(scope_case_id)
                placeholders = ",".join("?" for _ in case_variants)
                select_sql += f" AND case_id IN ({placeholders})"
                raw_rows = connection.execute(select_sql + " ORDER BY updated_at DESC", tuple(case_variants)).fetchall()
            else:
                raw_rows = connection.execute(select_sql + " ORDER BY updated_at DESC").fetchall()
            rows = [row_dict(row) for row in raw_rows]
            examined_roots.append(str(root))
            if documents:
                scope_candidate_rows += sum(1 for row in rows if same_scope(row, documents[0]))

            by_document_id = {}
            by_source_id = {}
            by_archive_key = {}
            by_legacy = {}
            by_file_key = {}
            for row in rows:
                by_document_id.setdefault(str(row.get("document_id") or ""), []).append(row)
                source_id = str(row.get("source_document_id") or "").strip()
                if source_id:
                    key = (_identity_text(row.get("source_system")), _identity_text(row.get("institution_name")), _identity_text(row.get("case_id")), source_id)
                    by_source_id.setdefault(key, []).append(row)
                archive_key = str(row.get("archive_identity_key") or "").strip()
                if archive_key:
                    by_archive_key.setdefault(archive_key, []).append(row)
                legacy_key = _legacy_semantic_key(row.get("case_id"), row.get("display_name"), row.get("uyap_path"))
                by_legacy.setdefault(legacy_key, []).append(row)
                file_key = _row_file_match_key(root, row.get("local_path"), row.get("target_path"))
                if file_key:
                    by_file_key.setdefault(file_key, []).append(row)

            def physical_candidates(document):
                target_path = str(document.get("targetPath") or "").strip()
                if not target_path:
                    return []
                try:
                    target = safe_target(root, target_path)
                    relative_parent = target.parent.relative_to(root).as_posix()
                except (OSError, ValueError):
                    return []
                if relative_parent not in physical_cache:
                    entries = {}
                    try:
                        # RC15.9: os.scandir() reuses directory-entry metadata
                        # supplied by Windows and avoids a separate stat call for
                        # every sibling while large archive folders are reconciled.
                        with os.scandir(target.parent) as iterator:
                            for entry in iterator:
                                if entry.name.startswith(".") or not entry.is_file(follow_symlinks=False):
                                    continue
                                child = Path(entry.path)
                                rel = child.relative_to(root).as_posix()
                                key = _archive_file_match_key(rel)
                                if key:
                                    entries.setdefault(key, []).append(child)
                    except OSError:
                        pass
                    physical_cache[relative_parent] = entries
                wanted = _archive_file_match_key(target_path)
                return list(physical_cache[relative_parent].get(wanted, [])) if wanted else []

            for document in documents:
                document_id = str(document.get("documentId") or "").strip()
                if not document_id:
                    continue
                fingerprint = str(document.get("sourceFingerprint") or "").strip()
                current_archive_key = str(document.get("archiveIdentityKey") or "").strip() or _archive_identity_key(document)
                current_source_id = str(document.get("sourceDocumentId") or "").strip()
                matched = None
                method = None
                is_ambiguous = False

                # 1) Stable numeric source id, when UYAP exposes it.
                if current_source_id:
                    source_key = (
                        _identity_text(document.get("sourceSystem")),
                        _identity_text(document.get("institutionName") or document.get("courtName")),
                        _identity_text(document.get("caseId")),
                        current_source_id,
                    )
                    matched, is_ambiguous = unique_proven_row(by_source_id.get(source_key, []), document, proof_cache)
                    if matched:
                        method = "source-document-id"

                # 2) Persistence document id. Fingerprint is useful evidence but
                # no longer a hard gate: Kurum can refresh display metadata while
                # the stable document identity itself remains unchanged.
                if matched is None and not is_ambiguous:
                    candidate_rows = by_document_id.get(document_id, [])
                    exact_fp = [row for row in candidate_rows if fingerprint and row.get("source_fingerprint") == fingerprint]
                    matched, is_ambiguous = unique_proven_row(exact_fp or candidate_rows, document, proof_cache)
                    if matched:
                        method = "document-id+fingerprint" if exact_fp else "document-id"

                # 3) New stable semantic index, intentionally excluding volatile
                # encrypted/render-scoped Kurum tokens.
                if matched is None and not is_ambiguous:
                    matched, is_ambiguous = unique_proven_row(by_archive_key.get(current_archive_key, []), document, proof_cache)
                    if matched:
                        method = "archive-identity"

                # 4) Physical canonical path identity. It ignores the final
                # volatile d-<hash>/numeric suffix and extension, but keeps court,
                # case, folder, date and title. This recovers legacy archives whose
                # Kurum source token changed after reopening the same case.
                if matched is None and not is_ambiguous:
                    target_path = str(document.get("targetPath") or "").strip()
                    file_key = _archive_file_match_key(target_path)
                    matched, is_ambiguous = unique_proven_row(by_file_key.get(file_key, []) if file_key else [], document, proof_cache)
                    if matched:
                        method = "verified-physical-path"

                # 5) Legacy DB rows do not have the new semantic columns. Use the
                # old metadata only when it resolves to one verified byte stream.
                if matched is None and not is_ambiguous:
                    legacy_key = _legacy_semantic_key(document.get("caseId"), document.get("documentTitle"), "/".join(document.get("folderNames") or []))
                    matched, is_ambiguous = unique_proven_row(by_legacy.get(legacy_key, []), document, proof_cache)
                    if matched:
                        method = "legacy-metadata"

                matched_path = None
                matched_size = None
                matched_hash = None
                row_meta = None
                if matched:
                    row_meta, matched_path, matched_size, matched_hash = matched
                    if _is_legacy_loose_download(root, matched_path):
                        imported = _import_legacy_loose_download(
                            root, matched_path, row_meta, document, matched_hash
                        )
                        if imported:
                            matched_path, import_mode = imported
                            matched_size = matched_path.stat().st_size
                            matched_hash = sha256_file(matched_path)
                            method = "legacy-loose-import"
                            if import_mode == "existing-canonical":
                                legacy_loose_reused += 1
                            else:
                                legacy_loose_imported += 1
                        else:
                            matched_path = None
                            matched_size = None
                            matched_hash = None

                # 6) Database may have been reset while the archive files survived.
                # Scan ONLY the deterministic target directory and accept only one
                # canonical title/date/path match after structural validation.
                if matched_path is None and not is_ambiguous:
                    files = physical_candidates(document)
                    if len(files) == 1:
                        candidate = files[0]
                        try:
                            detected = detect_extension(candidate, candidate.suffix)
                            html_context = {
                                "sourceDocumentId": current_source_id or None,
                                "originalFilename": candidate.name,
                                "bindingMethod": "local-archive-reconciliation",
                            }
                            valid = validate_content(candidate, detected, html_context=html_context)[0]
                            validation_kind = None
                            validation_mode = None
                            if valid and detected in (".html", ".htm"):
                                html_profile = classify_html_payload(candidate, html_context)
                                if html_profile.get("valid"):
                                    validation_kind = "HTML_DOCUMENT"
                                    validation_mode = html_profile.get("mode") or "local_archive_html_validation"
                            if valid:
                                matched_path = candidate.resolve()
                                matched_size = candidate.stat().st_size
                                matched_hash = sha256_file(candidate)
                                row_meta = {
                                    "case_id": document.get("caseId"),
                                    "display_name": document.get("documentTitle"),
                                    "uyap_path": "/".join(document.get("folderNames") or []),
                                    "validation_kind": validation_kind,
                                    "validation_mode": validation_mode,
                                }
                                method = "physical-file-import"
                        except (OSError, ValueError):
                            matched_path = None
                    elif len(files) > 1:
                        is_ambiguous = True

                if matched_path is None:
                    if is_ambiguous:
                        ambiguous.append(document_id)
                    continue

                now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                connection.execute(
                    """INSERT OR REPLACE INTO documents
                       (session_id, document_id, case_id, display_name, uyap_path, local_path,
                        size, sha256, source_fingerprint, source_system, institution_name,
                        source_document_id, archive_identity_key, document_date, target_path,
                        status, updated_at, validation_kind, validation_mode)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VERIFIED', ?, ?, ?)""",
                    (
                        session_id, document_id,
                        (row_meta or {}).get("case_id") or document.get("caseId"),
                        (row_meta or {}).get("display_name") or document.get("documentTitle"),
                        (row_meta or {}).get("uyap_path") or "/".join(document.get("folderNames") or []),
                        str(matched_path), matched_size, matched_hash, fingerprint,
                        document.get("sourceSystem"), document.get("institutionName") or document.get("courtName"),
                        current_source_id or (row_meta or {}).get("source_document_id"), current_archive_key,
                        document.get("documentDate"), matched_path.resolve().relative_to(root.resolve()).as_posix(), now,
                        (row_meta or {}).get("validation_kind"),
                        (row_meta or {}).get("validation_mode"),
                    ),
                )
                unchanged.append(document_id)
                hashes[document_id] = matched_hash
                match_methods[document_id] = method or "verified-local-archive"
                # RC15.9.22: ALREADY_ARCHIVED is a physical-integrity claim, not
                # a metadata claim. Every unchanged document carries an explicit
                # SHA-256 proof produced from the bytes that exist on disk now.
                physical_proofs[document_id] = {
                    "level": "sha256", "pathExists": True,
                    "size": int(matched_size or 0), "sha256": matched_hash,
                    "method": method or "verified-local-archive",
                }
                root_matched = True
            connection.commit()
            if message.get("reconcileRootOrphans"):
                cleanup = _reconcile_verified_root_downloads(
                    root,
                    connection,
                    minimum_age_ms=message.get("rootOrphanMinimumAgeMs") or 120_000,
                )
                for key in orphan_cleanup:
                    orphan_cleanup[key] += int(cleanup.get(key) or 0)
        finally:
            connection.close()
        if root_matched:
            matched_roots.append(str(root))

    unchanged_set = set(unchanged)
    ambiguous_set = set(ambiguous) - unchanged_set
    preferred_root = matched_roots[0] if matched_roots else str(roots[0])
    return {
        "ok": True,
        "unchanged": sorted(unchanged_set),
        "hashes": hashes,
        "matchMethods": {key: value for key, value in match_methods.items() if key in unchanged_set},
        "physicalProofs": {key: value for key, value in physical_proofs.items() if key in unchanged_set},
        "ambiguous": sorted(ambiguous_set),
        "notFound": max(0, len(documents) - len(unchanged_set) - len(ambiguous_set)),
        "downloadsRoot": preferred_root,
        "discoveredRoots": [str(root) for root in roots],
        "archiveRootCount": len(roots),
        "scopeCandidateRows": int(scope_candidate_rows),
        "examinedRoots": examined_roots,
        "rootOrphanCleanup": orphan_cleanup,
        "legacyLooseImported": int(legacy_loose_imported),
        "legacyLooseReused": int(legacy_loose_reused),
    }

def _windows_downloads_folder():
    """Resolve the real Windows Downloads known folder, including redirected locations."""
    if os.name != "nt":
        return None
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        value_name = "{374DE290-123F-4565-9164-39C4925E467B}"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, value_name)
        expanded = os.path.expandvars(str(value))
        if expanded:
            return Path(expanded).expanduser().resolve()
    except Exception:
        return None
    return None



def _browser_download_folder_candidates():
    """Discover custom Chromium download folders from local profile preferences.

    This is deliberately read-only and only extracts download directory paths.
    It lets a freshly reloaded extension rediscover an existing UYAP_Arsiv even
    when chrome.storage no longer remembers the previous downloadsRoot.
    """
    if os.name != "nt":
        return []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return []
    bases = [
        Path(local_app_data) / "Google" / "Chrome" / "User Data",
        Path(local_app_data) / "Microsoft" / "Edge" / "User Data",
        Path(local_app_data) / "BraveSoftware" / "Brave-Browser" / "User Data",
        Path(local_app_data) / "Chromium" / "User Data",
    ]
    discovered = []
    for base in bases:
        if not base.is_dir():
            continue
        preference_files = []
        for profile_name in ("Default", "Guest Profile"):
            pref = base / profile_name / "Preferences"
            if pref.is_file():
                preference_files.append(pref)
        try:
            preference_files.extend(path for path in base.glob("Profile */Preferences") if path.is_file())
        except OSError:
            pass
        for pref in preference_files:
            try:
                if pref.stat().st_size > 32 * 1024 * 1024:
                    continue
                payload = json.loads(pref.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
                continue
            values = [
                ((payload.get("download") or {}).get("default_directory")),
                ((payload.get("savefile") or {}).get("default_directory")),
            ]
            for value in values:
                if not value:
                    continue
                try:
                    candidate = Path(os.path.expandvars(str(value))).expanduser().resolve()
                except OSError:
                    continue
                discovered.append(candidate)
    unique = []
    seen = set()
    for root in discovered:
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique

def _archive_root_candidates(supplied_root=None):
    """Return deterministic candidate download roots that may contain UYAP_Arsiv."""
    candidates = []
    if supplied_root:
        try:
            candidates.append(Path(str(supplied_root)).expanduser().resolve())
        except OSError:
            pass
    selected_root = load_selected_archive_root()
    if selected_root is not None:
        candidates.append(selected_root)
    candidates.extend(_browser_download_folder_candidates())
    known_downloads = _windows_downloads_folder()
    if known_downloads is not None:
        candidates.append(known_downloads)
    home = Path.home()
    candidates.extend([(home / "Downloads").resolve(), (home / "İndirilenler").resolve()])
    unique = []
    seen = set()
    for root in candidates:
        key = os.path.normcase(str(root))
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def list_archive_registry(message):
    supplied_root = message.get("downloadsRoot")
    limit = max(1, min(int(message.get("limit") or 10000), 20000))
    roots = []
    candidate_roots = _archive_root_candidates(supplied_root)
    for candidate in candidate_roots:
        archive_root = candidate / "UYAP_Arsiv"
        if archive_root.exists() and archive_root.is_dir():
            roots.append(candidate)
    if not roots:
        fallback = candidate_roots[0] if candidate_roots else Path.home()
        return {
            "ok": True, "kind": "UYAP_ARCHIVE_REGISTRY", "schemaVersion": 4,
            "agentVersion": AGENT_VERSION, "downloadsRoot": str(fallback),
            "archiveRoot": str(fallback / "UYAP_Arsiv"), "archiveExists": False,
            "discoveredRoots": [], "totalRows": 0, "uniqueFiles": 0,
            "duplicateRunLinks": 0, "presentFiles": 0, "missingFiles": 0,
            "partialFiles": 0, "reviewFiles": 0, "physicalDiscoveredFiles": 0,
            "metadataVerifiedFiles": 0, "physicalReadyFiles": 0, "staleMetadataFiles": 0,
            "totalCases": 0, "caseIndex": [], "registryWarnings": [], "documents": []
        }

    rows = []
    partial_entries = []
    registry_warnings = []

    # Registry browsing is deliberately read-only. Opening the archive dashboard
    # must never mutate/migrate _archive.db merely to show a list. Corrupt SQLite
    # pages are salvaged by rowid ranges so one damaged page does not blank the
    # entire local archive index.
    for root in roots:
        archive_root = root / "UYAP_Arsiv"
        database_path = archive_root / "_archive.db"
        if database_path.exists():
            connection = None
            try:
                uri = database_path.resolve().as_uri() + "?mode=ro"
                connection = sqlite3.connect(uri, uri=True, timeout=10.0)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA busy_timeout=5000")
                columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(documents)").fetchall()}
                required = {"session_id", "document_id", "case_id", "display_name", "uyap_path", "local_path", "size", "sha256", "status", "updated_at"}
                if not required.issubset(columns):
                    registry_warnings.append({
                        "code": "REGISTRY_SCHEMA_INCOMPLETE", "archiveRoot": str(archive_root),
                        "detail": "documents tablosunda zorunlu alanlar eksik"
                    })
                else:
                    optional = {
                        "source_fingerprint": "NULL",
                        "source_system": "NULL",
                        "institution_name": "NULL",
                        "target_path": "NULL",
                    }
                    select_parts = [
                        "rowid AS _rowid", "session_id", "document_id", "case_id", "display_name",
                        "uyap_path", "local_path", "size", "sha256",
                    ]
                    for name, fallback_expr in optional.items():
                        select_parts.append(name if name in columns else f"{fallback_expr} AS {name}")
                    select_parts.extend(["status", "updated_at"])
                    select_sql = ", ".join(select_parts)
                    where_sql = "local_path IS NOT NULL AND local_path<>''"
                    db_rows = []
                    try:
                        db_rows = [dict(row) for row in connection.execute(
                            f"SELECT {select_sql} FROM documents WHERE {where_sql} ORDER BY updated_at DESC"
                        ).fetchall()]
                    except sqlite3.DatabaseError as error:
                        registry_warnings.append({
                            "code": "REGISTRY_DB_PARTIAL_RECOVERY", "archiveRoot": str(archive_root),
                            "detail": str(error)[:240]
                        })
                        try:
                            max_rowid = int(connection.execute("SELECT COALESCE(MAX(rowid),0) FROM documents").fetchone()[0] or 0)
                        except sqlite3.DatabaseError:
                            # COUNT(*) often remains readable even when the rowid/index
                            # page used by MAX() is damaged. Use a generous upper bound
                            # so row-level salvage can still recover healthy pages.
                            try:
                                approx_count = int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] or 0)
                            except sqlite3.DatabaseError:
                                approx_count = 0
                            max_rowid = min(200000, approx_count + max(1000, approx_count // 4)) if approx_count else 20000
                        chunk = 250
                        for first in range(1, max_rowid + 1, chunk):
                            last = min(max_rowid, first + chunk - 1)
                            try:
                                batch = connection.execute(
                                    f"SELECT {select_sql} FROM documents WHERE rowid BETWEEN ? AND ? AND {where_sql}",
                                    (first, last)
                                ).fetchall()
                                db_rows.extend(dict(row) for row in batch)
                            except sqlite3.DatabaseError:
                                for rowid in range(first, last + 1):
                                    try:
                                        row = connection.execute(
                                            f"SELECT {select_sql} FROM documents WHERE rowid=? AND {where_sql}", (rowid,)
                                        ).fetchone()
                                        if row is not None:
                                            db_rows.append(dict(row))
                                    except sqlite3.DatabaseError:
                                        continue
                        db_rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
                    rows.extend((root, row) for row in db_rows)
            except sqlite3.DatabaseError as error:
                registry_warnings.append({
                    "code": "REGISTRY_DB_UNREADABLE", "archiveRoot": str(archive_root),
                    "detail": str(error)[:240]
                })
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass

        incoming = archive_root / "_incoming"
        if incoming.exists():
            for path in incoming.rglob("*.part"):
                if not path.is_file():
                    continue
                try:
                    rel = path.relative_to(archive_root).as_posix()
                    size = path.stat().st_size
                    updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))
                except OSError:
                    continue
                partial_entries.append({
                    "sessionId": None, "documentId": None, "caseId": None,
                    "institutionName": None, "caseTitle": None, "sourceSystem": None,
                    "documentTitle": path.stem, "uyapPath": None, "targetPath": rel,
                    "extension": ".part", "size": int(size), "actualSize": int(size),
                    "sha256": None, "sourceFingerprint": None, "status": "partial",
                    "nativeStatus": "STAGING_PART", "updatedAt": updated, "fileModifiedAt": updated, "runCount": 1,
                    "downloadsRoot": str(root)
                })

    def path_case_metadata(archive_root, local_path, target_path, case_id, institution_name):
        parts = []
        raw_target = str(target_path or "").replace("\\", "/")
        if raw_target:
            parts = [part for part in raw_target.split("/") if part]
            if parts and parts[0].lower() == "uyap_arsiv":
                parts = parts[1:]
        if not parts:
            try:
                parts = list(local_path.relative_to(archive_root).parts)
            except ValueError:
                parts = []
        institution = str(institution_name or (parts[0] if parts else "") or "").strip() or None
        normalized_case = str(case_id or "").strip() or None
        if not normalized_case and len(parts) > 1:
            normalized_case = str(parts[1]).removesuffix("_Dosya").removesuffix("_Esas").replace("_", "/") or None
        case_title = None
        if len(parts) > 2:
            candidate = str(parts[2]).strip()
            if candidate and not candidate.startswith("_"):
                case_title = candidate.replace("_", "/", 1) if re.match(r"^20\d{2}_\d+", candidate) else candidate
        if not case_title and normalized_case:
            case_title = f"{normalized_case} (Dava/İcra Dosyası)"
        return institution, normalized_case, case_title

    # A physical file can be linked from more than one archive session. Collapse
    # those rows by absolute path while preserving the newest status metadata.
    unique = {}
    for root, row in rows:
        archive_root = root / "UYAP_Arsiv"
        local_value = str(row.get("local_path") or "")
        target_value = str(row.get("target_path") or "")
        # Copied databases retain the old computer's drive-letter path. Prefer a
        # physically present path safely rebased from target_path/ UYAP_Arsiv;
        # never treat a stale absolute path as authoritative merely on Windows.
        local_path = _registry_row_path(root, row)
        key = os.path.normcase(str(local_path))
        if not key:
            key = f"{row.get('case_id') or ''}|{row.get('document_id') or ''}|{row.get('sha256') or ''}"
        try:
            present = local_path.is_file()
            stat = local_path.stat() if present else None
            actual_size_probe = int(stat.st_size) if stat else None
        except OSError:
            present = False
            stat = None
            actual_size_probe = None
        expected_size_probe = int(row.get("size") or 0)
        size_match = bool(present and (expected_size_probe <= 0 or actual_size_probe == expected_size_probe))
        native_status = str(row.get("status") or "")
        metadata_verified = native_status == "VERIFIED"
        if metadata_verified and size_match:
            public_status = "archived"
            physical_state = "size_match"
        elif metadata_verified and present:
            public_status = "review"
            physical_state = "size_mismatch"
        elif metadata_verified:
            public_status = "missing"
            physical_state = "missing"
        elif native_status == "INVALID_CONTENT":
            public_status = "review" if present else "partial"
            physical_state = "present_unverified" if present else "missing"
        else:
            public_status = "partial" if present else "missing"
            physical_state = "present_unverified" if present else "missing"
        institution_name, normalized_case_id, case_title = path_case_metadata(
            archive_root, local_path, row.get("target_path"), row.get("case_id"), row.get("institution_name")
        )
        current = unique.get(key)
        if current is None:
            try:
                relative = local_path.relative_to(archive_root).as_posix()
            except ValueError:
                relative = local_path.name
            try:
                actual_size = actual_size_probe
                file_modified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)) if stat else None
            except (OSError, ValueError, TypeError):
                actual_size = None
                file_modified_at = None
            unique[key] = {
                "sessionId": row.get("session_id"), "documentId": row.get("document_id"), "caseId": normalized_case_id,
                "institutionName": institution_name, "caseTitle": case_title,
                "sourceSystem": row.get("source_system") or None,
                "documentTitle": row.get("display_name") or local_path.name or "Adsız evrak",
                "uyapPath": row.get("uyap_path") or None, "targetPath": relative,
                "extension": local_path.suffix.lower() or ".bin", "size": int(row.get("size") or 0),
                "actualSize": int(actual_size) if actual_size is not None else None,
                "sha256": row.get("sha256") or None, "sourceFingerprint": row.get("source_fingerprint") or None,
                "status": public_status, "nativeStatus": native_status or None,
                "metadataVerified": metadata_verified, "physicalState": physical_state,
                "physicalProofLevel": "size" if size_match else "none",
                "sizeMatchesMetadata": size_match,
                "updatedAt": row.get("updated_at") or None,
                "fileModifiedAt": file_modified_at,
                "runCount": 1, "downloadsRoot": str(root)
            }
        else:
            current["runCount"] = int(current.get("runCount") or 1) + 1
            if not current.get("caseId") and normalized_case_id: current["caseId"] = normalized_case_id
            if not current.get("institutionName") and institution_name: current["institutionName"] = institution_name
            if not current.get("caseTitle") and case_title: current["caseTitle"] = case_title
            if not current.get("sourceSystem") and row.get("source_system"): current["sourceSystem"] = row.get("source_system")
            if not current.get("documentTitle") and row.get("display_name"): current["documentTitle"] = row.get("display_name")

    # Build the case table from the latest row for each stable document_id.
    # This mirrors the dashboard table users expect: one current logical UYAP
    # document, even if the same document was seen in several archive sessions or
    # stored under more than one historical physical path.
    latest_documents = {}
    for root, row in rows:
        document_key = str(row.get("document_id") or "").strip()
        if not document_key:
            document_key = "|".join([
                str(row.get("source_system") or ""), str(row.get("institution_name") or ""),
                str(row.get("case_id") or ""), str(row.get("target_path") or row.get("local_path") or ""),
            ])
        if document_key not in latest_documents:
            latest_documents[document_key] = (root, row)

    case_groups = {}
    for root, row in latest_documents.values():
        archive_root = root / "UYAP_Arsiv"
        local_value = str(row.get("local_path") or "")
        target_value = str(row.get("target_path") or "")
        local_path = _registry_row_path(root, row)
        institution, case_id, case_title = path_case_metadata(
            archive_root, local_path, target_value, row.get("case_id"), row.get("institution_name")
        )
        institution = institution or "Bilinmeyen Kurum/Daire"
        case_id = str(case_id or "").strip()
        if not case_id:
            continue
        group_key = f"{institution.casefold()}|{case_id.casefold()}"
        group = case_groups.get(group_key)
        if group is None:
            group = {
                "institutionName": institution, "caseId": case_id,
                "caseTitle": case_title or f"{case_id} (Dava/İcra Dosyası)",
                "documentCount": 0, "presentCount": 0, "verifiedCount": 0,
                "metadataVerifiedCount": 0, "physicalReadyCount": 0,
                "problemCount": 0, "missingCount": 0, "sizeMismatchCount": 0,
                "staleMetadataCount": 0, "unregisteredPhysicalCount": 0,
                "unregisteredPhysicalBytes": 0, "totalBytes": 0,
                "latestAt": None, "sourceSystems": set(),
            }
            case_groups[group_key] = group
        group["documentCount"] += 1
        native_status = str(row.get("status") or "")
        metadata_verified = native_status == "VERIFIED"
        if metadata_verified:
            group["verifiedCount"] += 1  # historical DB verification, kept for compatibility
            group["metadataVerifiedCount"] += 1
        try:
            present = local_path.is_file()
            actual_size = int(local_path.stat().st_size) if present else None
        except OSError:
            present = False
            actual_size = None
        expected_size = int(row.get("size") or 0)
        size_match = bool(present and (expected_size <= 0 or actual_size == expected_size))
        if present:
            group["presentCount"] += 1
        else:
            group["missingCount"] += 1
        if metadata_verified and size_match:
            group["physicalReadyCount"] += 1
        else:
            group["problemCount"] += 1
            if metadata_verified:
                group["staleMetadataCount"] += 1
            if present and not size_match:
                group["sizeMismatchCount"] += 1
        group["totalBytes"] += int(row.get("size") or 0)
        when = row.get("updated_at") or None
        if when and (not group["latestAt"] or str(when) > str(group["latestAt"])):
            group["latestAt"] = when
        if row.get("source_system"):
            group["sourceSystems"].add(str(row.get("source_system")))

    db_unique_count = len(unique)

    # Also surface physical document files that exist in the case archive even
    # if an older/crashed run failed to leave a usable registry row. Files under
    # internal, quarantine and export folders are deliberately excluded.
    physical_only = []
    for root in roots:
        archive_root = root / "UYAP_Arsiv"
        for path in archive_root.rglob("*"):
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(archive_root)
            except ValueError:
                continue
            parts = relative.parts
            if not parts or len(parts) < 3:
                continue
            if parts[0].startswith("_") or parts[0] == "Hatalı Dosyalar" or "_exports" in parts:
                continue
            if path.name.startswith("_archive.db") or path.suffix.lower() == ".part":
                continue
            key = os.path.normcase(str(path.resolve()))
            if key in unique:
                continue
            try:
                stat = path.stat()
                updated = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime))
                size = int(stat.st_size)
            except OSError:
                continue
            case_id = parts[1].removesuffix("_Dosya").removesuffix("_Esas").replace("_", "/") if len(parts) > 1 else None
            case_title = str(parts[2]).replace("_", "/", 1) if len(parts) > 2 else None
            item = {
                "sessionId": None, "documentId": None, "caseId": case_id,
                "institutionName": parts[0] if parts else None, "caseTitle": case_title,
                "sourceSystem": None,
                "documentTitle": path.stem, "uyapPath": "/".join(parts[2:-1]) or None,
                "targetPath": relative.as_posix(), "extension": path.suffix.lower() or ".bin",
                "size": size, "actualSize": size, "sha256": None, "sourceFingerprint": None,
                "status": "archived", "nativeStatus": "PHYSICAL_FILE_DISCOVERED",
                "metadataVerified": False, "physicalState": "present",
                "physicalProofLevel": "existence", "sizeMatchesMetadata": None,
                "updatedAt": updated, "fileModifiedAt": updated, "runCount": 1, "downloadsRoot": str(root)
            }
            unique[key] = item
            physical_only.append(item)

    # Files can survive a crash even when their SQLite row was never committed.
    # Keep them visible in the case picker as present-but-unverified recovery
    # entries instead of making Workspace look empty.
    for item in physical_only:
        institution = str(item.get("institutionName") or "Bilinmeyen Kurum/Daire")
        case_id = str(item.get("caseId") or "").strip()
        if not case_id:
            continue
        group_key = f"{institution.casefold()}|{case_id.casefold()}"
        group = case_groups.get(group_key)
        if group is None:
            group = {
                "institutionName": institution, "caseId": case_id,
                "caseTitle": item.get("caseTitle") or f"{case_id} (Dava/İcra Dosyası)",
                "documentCount": 0, "presentCount": 0, "verifiedCount": 0,
                "metadataVerifiedCount": 0, "physicalReadyCount": 0,
                "problemCount": 0, "missingCount": 0, "sizeMismatchCount": 0,
                "staleMetadataCount": 0, "unregisteredPhysicalCount": 0,
                "unregisteredPhysicalBytes": 0, "totalBytes": 0,
                "latestAt": None, "sourceSystems": set(),
            }
            case_groups[group_key] = group
        # Physical-only files are recovery evidence, not logical UYAP
        # documents. They must remain visible for reconciliation, but must not
        # inflate documentCount/problemCount or block AI when every registered
        # logical document is VERIFIED and physically intact.
        group["presentCount"] += 1
        group["unregisteredPhysicalCount"] += 1
        group["unregisteredPhysicalBytes"] += int(item.get("size") or 0)
        group["totalBytes"] += int(item.get("size") or 0)
        when = item.get("updatedAt") or item.get("fileModifiedAt") or None
        if when and (not group["latestAt"] or str(when) > str(group["latestAt"])):
            group["latestAt"] = when

    case_index = []
    for group in case_groups.values():
        systems = sorted(group.pop("sourceSystems"))
        group["sourceSystems"] = systems
        group["sourceSystem"] = systems[0] if len(systems) == 1 else ("MIXED" if systems else None)
        case_index.append(group)
    case_index.sort(key=lambda item: str(item.get("latestAt") or ""), reverse=True)

    docs = list(unique.values()) + partial_entries
    docs.sort(key=lambda item: str(item.get("fileModifiedAt") or item.get("updatedAt") or ""), reverse=True)
    total_unique = len(docs)
    present_files = sum(1 for item in docs if item.get("status") == "archived")
    missing_files = sum(1 for item in docs if item.get("status") == "missing")
    metadata_verified_files = sum(1 for item in docs if item.get("metadataVerified") is True)
    physical_ready_files = sum(1 for item in docs if item.get("physicalProofLevel") in ("size", "sha256"))
    stale_metadata_files = sum(1 for item in docs if item.get("metadataVerified") is True and item.get("physicalProofLevel") == "none")
    partial_files = sum(1 for item in docs if item.get("status") == "partial")
    review_files = sum(1 for item in docs if item.get("status") == "review")
    primary_root = roots[0]
    return {
        "ok": True, "kind": "UYAP_ARCHIVE_REGISTRY", "schemaVersion": 4,
        "agentVersion": AGENT_VERSION, "downloadsRoot": str(primary_root),
        "archiveRoot": str(primary_root / "UYAP_Arsiv"), "archiveExists": True,
        "discoveredRoots": [str(item) for item in roots], "totalRows": len(rows),
        "uniqueFiles": total_unique, "duplicateRunLinks": max(0, len(rows) - db_unique_count),
        "presentFiles": present_files, "missingFiles": missing_files,
        "partialFiles": partial_files, "reviewFiles": review_files,
        "physicalDiscoveredFiles": len(physical_only),
        "metadataVerifiedFiles": metadata_verified_files, "physicalReadyFiles": physical_ready_files,
        "staleMetadataFiles": stale_metadata_files,
        "totalCases": len(case_index), "caseIndex": case_index,
        "registryWarnings": registry_warnings,
        "documents": docs[:limit]
    }

def discard_review_html(message):
    """Delete a quarantined HTML response after an explicit user decision."""
    root = Path(str(message.get("downloadsRoot") or "")).expanduser().resolve()
    session_id = str(message.get("sessionId") or "").strip()
    document = message.get("document") or {}
    document_id = str(document.get("documentId") or "").strip()
    quarantine_value = str(message.get("quarantinePath") or "").strip()
    if not root.exists() or not root.is_dir():
        raise ValueError("Arşiv kökü bulunamadı")
    if not session_id or not document_id:
        raise ValueError("sessionId/documentId gerekli")
    connection = database(root)
    try:
        # Older extension states may have lost quarantinePath after final-recovery
        # rewrites, while _archive.db still has the exact rejected local_path.
        # Recover it from the local database so the user's explicit HTML decision
        # can actually delete the preserved bytes instead of only changing UI state.
        if not quarantine_value:
            row = connection.execute(
                "SELECT local_path FROM documents WHERE session_id=? AND document_id=?",
                (session_id, document_id)
            ).fetchone()
            quarantine_value = str(row[0] or "").strip() if row else ""
        if not quarantine_value:
            raise ValueError("HTML karantina dosyası bulunamadı")
        quarantine_path = Path(quarantine_value).expanduser().resolve()
        allowed_root = (root / "UYAP_Arsiv" / "Hatalı Dosyalar" / "UYAP_HTML_Yanitlari").resolve()
        session_ref = hashlib.sha256(session_id.encode()).hexdigest()[:24]
        if allowed_root not in quarantine_path.parents or session_ref not in quarantine_path.parts:
            raise ValueError("HTML karantina dosyası bu arşiv işine ait değil")
        # v2.7.26 and older could store byte-detected HTML responses with an
        # .invalid suffix because the staging filename did not itself end in
        # .html.  Do not trust the suffix either way: the path must already be
        # inside this session's dedicated HTML quarantine folder, and legacy
        # non-.html files must additionally prove HTML content by signature.
        suffix_is_html = quarantine_path.suffix.lower() in {".html", ".htm"}
        content_is_html = False
        if quarantine_path.exists() and quarantine_path.is_file():
            try:
                with quarantine_path.open("rb") as stream:
                    content_is_html = is_html(stream.read(65536))
            except OSError:
                content_is_html = False
        if not suffix_is_html and not content_is_html:
            raise ValueError("Karantina kaydı gerçek HTML içeriği olarak doğrulanamadı; güvenlik nedeniyle silinmedi")
        if quarantine_path.exists():
            quarantine_path.unlink()
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        connection.execute(
            "INSERT INTO download_attempts(session_id,document_id,attempt_no,response_type,error_code,detail,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (session_id, document_id, int(document.get("attempts") or 0), "USER_EXCLUDED", "USER_CONFIRMED_HTML_RESPONSE",
             "Kullanıcı UYAP kaynağının gerçekten HTML yanıtı verdiğini onayladı; karantina dosyası silindi ve kayıt arşiv tamlık hesabından çıkarıldı.", now)
        )
        # The final packager validates only physical archive documents. Keep the
        # exclusion in download_attempts for audit, but remove INVALID_CONTENT
        # from the physical documents table so it cannot poison the final gate.
        connection.execute("DELETE FROM documents WHERE session_id=? AND document_id=?", (session_id, document_id))
        connection.commit()
    finally:
        connection.close()
    return {"ok": True, "discarded": True, "documentId": document_id}


def open_archive_folder(message):
    supplied_root = message.get("downloadsRoot")
    if not supplied_root:
        raise ValueError("Arşiv kökü belirtilmedi")
    root = Path(supplied_root).resolve()
    archive_root = (root / "UYAP_Arsiv").resolve()
    # The extension may only ask the agent to open its own archive directory.
    # Never accept an arbitrary absolute folder from the UI.
    try:
        archive_root.relative_to(root)
    except ValueError as error:
        raise ValueError("Geçersiz arşiv yolu") from error
    if not archive_root.exists() or not archive_root.is_dir():
        raise ValueError("Arşiv klasörü henüz oluşmamış")
    if os.name == "nt":
        os.startfile(str(archive_root))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(archive_root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(archive_root)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "path": str(archive_root)}



def open_final_package_folder(message):
    supplied_root = message.get("downloadsRoot")
    supplied_path = message.get("packagePath") or message.get("zipPath")
    if not supplied_root:
        raise ValueError("Arşiv kökü belirtilmedi")
    if not supplied_path:
        raise ValueError("Final paket yolu bulunamadı")
    root = Path(supplied_root).resolve()
    archive_root = (root / "UYAP_Arsiv").resolve()
    target = Path(str(supplied_path)).expanduser().resolve()
    if target.is_file():
        target = target.parent
    try:
        target.relative_to(archive_root)
    except ValueError as error:
        raise ValueError("Final paket yolu UYAP_Arsiv dışında; güvenlik nedeniyle açılamadı") from error
    if not target.exists() or not target.is_dir():
        raise ValueError("Final paket klasörü fiziksel olarak bulunamadı")
    if os.name == "nt":
        os.startfile(str(target))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"ok": True, "path": str(target)}


def revalidate_quarantined(message):
    """Re-validate a quarantined download after validator/agent upgrades."""
    root = Path(str(message.get("downloadsRoot") or "")).expanduser().resolve()
    quarantine_path = Path(str(message.get("quarantinePath") or "")).expanduser().resolve()
    session_id = str(message.get("sessionId") or "")
    if not root.exists() or not root.is_dir():
        raise ValueError("Arşiv kökü bulunamadı")
    if not quarantine_path.exists() or not quarantine_path.is_file():
        raise ValueError("Karantinadaki dosya bulunamadı")

    allowed_root = (root / "UYAP_Arsiv" / "Hatalı Dosyalar").resolve()
    if quarantine_path != allowed_root and allowed_root not in quarantine_path.parents:
        raise ValueError("Karantina dosyası arşiv kökü dışında")
    session_ref = hashlib.sha256(session_id.encode()).hexdigest()[:24]
    if session_ref not in quarantine_path.parts:
        raise ValueError("Karantina dosyası bu işe ait değil")

    document = message.get("document") or {}
    detection_hint = _extension_hint(
        message.get("extension"),
        document.get("extension"),
        document.get("targetPath"),
        document.get("originalFilename"),
        document.get("browserFilename"),
        message.get("targetPath"),
        quarantine_path.name,
    )
    detection_profile = detect_content_profile(quarantine_path, detection_hint)
    extension = detection_profile.get("extension") or ".bin"
    udf_validation_mode = None
    revalidation_context = {
        "sourceDocumentId": document.get("sourceDocumentId") or canonical_source_id(document, quarantine_path.name),
        "originalFilename": document.get("originalFilename") or quarantine_path.name,
        "bindingMethod": document.get("bindingMethod") or "",
        "correlationMode": document.get("correlationMode") or "",
        "correlationCandidateCount": document.get("correlationCandidateCount") or 0,
        "expectedDocumentKind": document.get("expectedDocumentKind") or "",
    }
    html_profile = classify_html_payload(quarantine_path, revalidation_context) if extension.lower() in (".html", ".htm") else None
    image_profile = classify_image_payload(quarantine_path, revalidation_context) if extension.lower() in _IMAGE_EXTENSIONS else None
    opaque_validation_mode = None
    content_diag = {
        "contentDetectedExtension": extension,
        "contentDetectionMode": detection_profile.get("mode"),
        "contentDetectionHint": detection_profile.get("hint"),
        "udfProbeError": detection_profile.get("udfProbeError"),
    }
    if extension.lower() == ".udf":
        valid, error_code, detail, udf_validation_mode = _validate_udf_package(quarantine_path)
    elif extension.lower() == ".pdf":
        valid, error_code, detail, pdf_diag = validate_pdf_structure(quarantine_path)
        content_diag.update(pdf_diag or {})
    else:
        valid, error_code, detail = validate_content(quarantine_path, extension, html_context=revalidation_context)
    if (
        not valid
        and error_code == "UNSUPPORTED_FORMAT"
        and _opaque_quarantine_provenance(quarantine_path, document, revalidation_context, detection_profile)
    ):
        valid, error_code, detail = True, None, None
        opaque_validation_mode = "uyap_exact_opaque_binary_v1"
        content_diag["opaqueBinaryValidated"] = True
        content_diag["opaqueValidationMode"] = opaque_validation_mode
    if not valid:
        return {
            "ok": True,
            "valid": False,
            "errorCode": error_code,
            "detail": detail,
            "extension": extension,
            "contentDetectedExtension": content_diag.get("contentDetectedExtension"),
            "contentDetectionMode": content_diag.get("contentDetectionMode"),
            "contentDetectionHint": content_diag.get("contentDetectionHint"),
            "udfProbeError": content_diag.get("udfProbeError"),
            "imageDetectedType": image_profile.get("kind") if image_profile else None,
            "imageValidationMode": image_profile.get("mode") if image_profile else None,
            "imageFallbackFromMode": image_profile.get("fallbackFromMode") if image_profile else None,
            "imageWicFallbackMode": image_profile.get("wicFallbackMode") if image_profile else None,
            "imageGdiplusFallbackMode": image_profile.get("gdiplusFallbackMode") if image_profile else None,
            "imageTrailingBytes": image_profile.get("trailingBytes") if image_profile else None,
            "imageTrailingKind": image_profile.get("trailingKind") if image_profile else None,
        }

    source_id = canonical_source_id(document, quarantine_path.name)
    if source_id:
        document["sourceDocumentId"] = source_id

    target = safe_target(root, message["targetPath"])
    target = canonical_target(target, document, extension, source_id)
    target.parent.mkdir(parents=True, exist_ok=True)

    size = quarantine_path.stat().st_size
    digest = sha256_file(quarantine_path)
    final_target = target
    reuse = False
    if target.exists():
        if target.is_file() and target.stat().st_size == size and sha256_file(target) == digest:
            reuse = True
        else:
            final_target = target.with_name(f"{target.stem}__v{uuid.uuid4().hex}{target.suffix}")

    if reuse:
        quarantine_path.unlink()
    else:
        os.replace(quarantine_path, final_target)

    connection = database(root)
    try:
        record_attempt(
            connection,
            message,
            "VERIFIED",
            local_path=str(final_target),
            size=size,
            sha256=digest,
            validation_kind=(
                "HTML_DOCUMENT" if html_profile and html_profile.get("valid") else
                "UDF_DOCUMENT" if udf_validation_mode else
                "IMAGE_DOCUMENT" if image_profile and image_profile.get("valid") else
                "UYAP_OPAQUE_BINARY" if opaque_validation_mode else None
            ),
            validation_mode=(
                html_profile.get("mode") if html_profile and html_profile.get("valid") else
                udf_validation_mode or
                (image_profile.get("mode") if image_profile and image_profile.get("valid") else None) or
                opaque_validation_mode
            ),
        )
    finally:
        connection.close()

    result = {
        "ok": True,
        "valid": True,
        "size": size,
        "sha256": digest,
        "localPath": str(final_target),
        "downloadsRoot": str(root),
        "extension": extension,
        "targetPath": final_target.relative_to(root).as_posix(),
        "sourceDocumentId": source_id,
        "revalidatedFromQuarantine": True,
        "contentDetectedExtension": content_diag.get("contentDetectedExtension"),
        "contentDetectionMode": content_diag.get("contentDetectionMode"),
        "contentDetectionHint": content_diag.get("contentDetectionHint"),
    }
    if html_profile and html_profile.get("valid"):
        result["contentKind"] = "HTML_DOCUMENT"
        result["htmlDocumentValidated"] = True
        result["htmlValidationMode"] = html_profile.get("mode")
    if udf_validation_mode:
        result["contentKind"] = "UDF_DOCUMENT"
        result["udfDocumentValidated"] = True
        result["udfValidationMode"] = udf_validation_mode
    if opaque_validation_mode:
        result["contentKind"] = "UYAP_OPAQUE_BINARY"
        result["opaqueBinaryValidated"] = True
        result["opaqueValidationMode"] = opaque_validation_mode
        result["validationNotes"] = ["UYAP kaynağı, oturum kimliği, boyut ve SHA-256 kanıtı birebir doğrulandı; bilinmeyen biçim baytları değiştirilmeden opak ikili evrak olarak geri kazanıldı."]
    if image_profile and image_profile.get("valid"):
        result["contentKind"] = "IMAGE_DOCUMENT"
        result["imageDocumentValidated"] = True
        result["imageDetectedType"] = image_profile.get("kind")
        result["imageValidationMode"] = image_profile.get("mode")
        result["imageWidth"] = image_profile.get("width")
        result["imageHeight"] = image_profile.get("height")
        result["imageTrailingBytes"] = image_profile.get("trailingBytes")
        result["imageTrailingKind"] = image_profile.get("trailingKind")
        image_mode = str(image_profile.get("mode") or "")
        if image_profile.get("fallbackFromMode"):
            result["imageFallbackFromMode"] = image_profile.get("fallbackFromMode")
        if image_mode.startswith("jpeg_uyap_compatible"):
            result["validationNotes"] = ["JPEG, standart sıkı kontrolden geçmedi; UYAP/scanner uyumlu yapısal doğrulama ile güvenli biçimde doğrulandı."]
        elif image_mode == "jpeg_windows_wic_decode":
            result["validationNotes"] = ["JPEG, dahili marker kontrolünden geçmedi; Windows WIC gerçek raster decoderı ile açılıp boyutları doğrulanarak kabul edildi."]
        elif image_mode == "jpeg_windows_gdiplus_decode":
            result["validationNotes"] = ["JPEG, WIC/dahili marker kontrolünden geçmedi; Windows GDI+ gerçek görüntü decoderı ile açılıp boyutları doğrulanarak kabul edildi."]
        elif image_mode in ("jpeg_uyap_trailing_preserved_wic", "jpeg_uyap_trailing_preserved_gdiplus"):
            result["validationNotes"] = ["UYAP kaynaklı JPEG, EOI sonrasındaki sınırlı kaynak metadatası korunarak gerçek Windows raster decoderı ile doğrulandı; orijinal baytlar değiştirilmedi."]
    if content_diag:
        for key in ("pdfEofOffset", "pdfTrailingBytes", "pdfStartXref", "pdfXrefKind", "pdfTrailingKind"):
            if key in content_diag:
                result[key] = content_diag.get(key)
        if content_diag.get("pdfTrailingWarning"):
            result["validationNotes"] = [str(content_diag["pdfTrailingWarning"])]
    return result

def format_audit(message):
    """Privacy-light census of already downloaded bytes for one archive session."""
    session_id = str(message.get('sessionId') or '').strip()
    if not session_id:
        raise ValueError('sessionId gerekli')
    root_value = message.get('downloadsRoot')
    if not root_value:
        raise ValueError('downloadsRoot gerekli')
    root = Path(root_value).resolve()
    connection = database(root)
    try:
        rows = connection.execute(
            "SELECT document_id, local_path, status FROM documents WHERE session_id=? AND local_path IS NOT NULL",
            (session_id,),
        ).fetchall()
    finally:
        connection.close()
    counts = Counter(); modes = Counter(); status_counts = Counter(); unknown=[]; invalid=[]
    scanned=0
    for document_id, local_path, status in rows:
        path=Path(str(local_path or ''))
        if not path.is_file():
            status_counts['missing_local_file'] += 1
            continue
        scanned += 1
        status_counts[str(status or 'unknown')] += 1
        hint=path.suffix.lower()
        profile=detect_content_profile(path,hint)
        ext=profile.get('extension') or '.bin'; mode=profile.get('mode') or 'unknown'
        counts[ext]+=1; modes[mode]+=1
        ok, code, detail=validate_content(path,ext)
        ref=hashlib.sha256(str(document_id).encode('utf-8')).hexdigest()[:20]
        if ext=='.bin' or mode in {'unknown','ole_compound_unknown'}:
            unknown.append({'ref':ref,'size':path.stat().st_size,'mode':mode,'hint':profile.get('hint')})
        elif not ok:
            invalid.append({'ref':ref,'extension':ext,'mode':mode,'errorCode':code,'detail':str(detail or '')[:240]})
    return {
        'ok':True,'kind':'UYAP_FORMAT_AUDIT','schemaVersion':1,'agentVersion':AGENT_VERSION,
        'sessionId':session_id,'scannedLocalFiles':scanned,'databaseRows':len(rows),
        'formatCounts':dict(counts.most_common()),'detectionModes':dict(modes.most_common()),
        'statusCounts':dict(status_counts.most_common()),'unknownCount':len(unknown),'invalidCount':len(invalid),
        'unknownSamples':unknown[:20],'invalidSamples':invalid[:20],
        'supportedFamilies':['PDF','TIFF/BigTIFF','JPEG/PNG/GIF/BMP/WebP','UDF ZIP','UDF streaming ZIP','UDF raw XML','DOC/DOCX','XLS/XLSX','PPT/PPTX','ODT/ODS/ODP','RTF','XML/SVG','ZIP','7Z','RAR','HTML']
    }


def dispatch(message):
    action = message.get("action")
    if action == "ping":
        return {"ok": True, "version": AGENT_VERSION, "protocolVersion":PROTOCOL_VERSION, "echo":message.get('probe'),
                "capabilities": ["sqlite", "sha256", "magic_bytes", "atomic_move", "quarantine", "attempt_journal", "attempt_owner_replay_v1", "validation_perf_v1", "persistent_session_v1", "final_integrity_v1", "source_adapter_v1", "chunked_archive_v1", "download_completion_guard_v1", "pdf_structure_v2", "uyap_pdf_control_suffix_1a04_v1", "bulk_reliability_v1", "legitimate_html_document_v1", "legitimate_html_document_v2", "image_document_v1", "uyap_streaming_udf_v1", "quarantine_revalidation_v1", "uyap_pdf_belgenet_suffix_v1", "uyap_pdf_avision_suffix_v1", "pdf_producer_comment_suffix_v1", "unified_document_sniffer_v1", "uyap_raw_xml_udf_v1", "format_lab_v1", "office_container_detection_v1", "bigtiff_detection_v1", "archive_registry_v1", "user_html_exclusion_v1", "visible_final_package_v1", "archive_root_rediscovery_v1", "sqlite_wal_dual_lane_v1", "sqlite_scandir_hotpath_v1", "jpeg_uyap_compatible_v1", "jpeg_windows_decoder_fallback_v1", "jpeg_windows_wic_decoder_v1", "document_type_truth_v1", "uyap_exact_content_truth_v1", "jpeg_uyap_trailing_preserve_v1", "html_circuit_deadline_v1", "source_path_self_heal_v1", "content_hash_reconciliation_v1", "verified_root_orphan_cleanup_v1", "opaque_binary_archive_v1", "finalization_validation_proof_v2", "duplicate_path_fresh_owner_v1", "portable_archive_resume_v1", "legacy_loose_archive_import_v2", "ai_local_index_v1", "ai_provider_gateway_v1", "archive_case_ai_v1", "ai_summary_archive_v1", "target_path_budget_v1"]}
    if action == "ai_build_index":
        return ai_build_index(message["downloadsRoot"], message["sessionId"], message.get("bindings") or [])
    if action == "ai_archive_case_scope":
        return {"ok": True, "scopeId": ai_archive_case_scope_id(message.get("institutionName"), message.get("caseId"))}
    if action == "ai_inspect_archive_case":
        return ai_inspect_archive_case(message["downloadsRoot"], message.get("institutionName"), message.get("caseId"))
    if action == "ai_build_archive_case_index":
        return ai_build_archive_case_index(message["downloadsRoot"], message.get("institutionName"), message.get("caseId"))
    if action == "ai_index_status":
        return ai_index_status(message["downloadsRoot"], message["sessionId"])
    if action == "ai_search":
        return ai_search_index(message["downloadsRoot"], message["sessionId"], message.get("query") or "", message.get("limit") or 8)
    if action == "ai_provider_status":
        return ai_provider_status()
    if action == "ai_save_summary":
        return save_ai_summary(message)
    if action == "ai_list_summaries":
        return list_ai_summaries(message)
    if action == "ai_answer":
        total_documents = message.get("totalDocuments")
        result = ai_answer_from_local_index(
            message["downloadsRoot"],
            message["sessionId"],
            message.get("query") or "",
            message.get("limit") or 8,
            workflow_id=message.get("workflowId"),
            case_context={
                "institutionName": str(message.get("institutionName") or "").strip() or None,
                "caseId": str(message.get("caseId") or "").strip() or None,
                "totalDocuments": total_documents if isinstance(total_documents, int) else None,
            },
        )
        diagnostic_path = _write_ai_diagnostic(message, result)
        if isinstance(result, dict) and diagnostic_path:
            result = {**result, "diagnosticPath": diagnostic_path}
        return result
    if action == "select_existing_archive":
        return select_existing_archive(message)
    if action == "get_selected_archive":
        return get_selected_archive(message)
    if action == "format_audit":
        return format_audit(message)
    if action == "list_archive_registry":
        return list_archive_registry(message)
    if action == "discard_review_html":
        return discard_review_html(message)
    if action == "validate_download":
        try:
            return validate_download(message)
        except TargetPathTooLong as error:
            return _target_path_too_long_payload(error.diagnostics)
        except FileNotFoundError as error:
            # Last-resort net. Target-side failures are classified inside
            # validate_download; what reaches here is reported with privacy-light
            # evidence (WinError, path length) so a mislabel is visible next time.
            return {"ok": True, "valid": False, "errorCode": "SOURCE_FILE_MISSING",
                    "detail": "Yerel geçici dosya yolu doğrulama sırasında kayboldu; evrak taze indirme ile otomatik kurtarılacak.",
                    "retryHint": "fresh_redownload", "sourcePathRecovery": True,
                    "fileErrorStage": "unclassified", **_file_error_diagnostics(error)}
    if action == "revalidate_quarantined":
        return revalidate_quarantined(message)
    if action == "finalize_archive":
        return finalize_archive(message)
    if action == "sync_inventory":
        return sync_inventory(message)
    if action == "open_archive_folder":
        return open_archive_folder(message)
    if action == "open_final_package_folder":
        return open_final_package_folder(message)
    raise ValueError(f"Bilinmeyen işlem: {action}")


def operation_error_payload(error):
    """Return a structured operation failure without impersonating a pipe outage."""
    error_type = type(error).__name__
    detail = str(error)
    lowered = detail.lower()
    if isinstance(error, sqlite3.OperationalError) and ("locked" in lowered or "busy" in lowered):
        code = "NATIVE_DB_BUSY"
    elif isinstance(error, PermissionError) or "sharing violation" in lowered or "used by another process" in lowered:
        code = "NATIVE_FILE_BUSY"
    else:
        code = "NATIVE_OPERATION_FAILED"
    return {
        "ok": False,
        "errorCode": code,
        "errorType": error_type,
        "error": f"{error_type}: {detail}",
        "operationFailure": True,
    }


def main():
    if os.name == 'nt':
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    while True:
        message = read_message()
        if message is None:
            return
        try:
            write_message(dispatch(message))
        except Exception as error:
            write_message(operation_error_payload(error))


if __name__ == "__main__":
    if '--self-test' in sys.argv:
        from host_self_test import check_host
        command = [sys.executable] if getattr(sys, 'frozen', False) else [sys.executable, '-u', str(Path(__file__).resolve())]
        try:
            version = check_host(command)
            print('BAGLANTI TESTI BASARILI - Ajan ' + version + ' - 2/2 ping')
        except Exception as error:
            print('BAGLANTI TESTI BASARISIZ: ' + str(error), file=sys.stderr)
            sys.exit(1)
    else:
        try:
            main()
        except Exception as error:
            print('UYAP_NATIVE_HOST: ' + str(error), file=sys.stderr)
            sys.exit(1)
