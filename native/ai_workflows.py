"""Explicit, case-scoped AI retrieval workflows.

Only entries in the verified, isolated local AI index are read. A comparison
baseline stores hashes, not document text or identity, and is scoped by the
existing per-case SQLite index path.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_local_index import _clean, _index_db, _summary_date_key, summary_context, search_index

WORKFLOW_IDS = (
    "case_summary",
    "important_dates",
    "court_orders",
    "parties",
    "tebligat",
)
# Retired cards whose ids stay callable for older Workspace builds:
# "new_updates" (Yeni Ne Var?) repeated Önemli Tarihler; "classification" and
# "chronology" saw only a few snippets while UYAP document types and the
# Zaman Çizelgesi page already cover every document.
LEGACY_WORKFLOW_IDS = ("new_updates", "classification", "chronology")
ALL_WORKFLOW_IDS = frozenset((*WORKFLOW_IDS, "ask", *LEGACY_WORKFLOW_IDS))
WORKFLOW_LABELS = {
    "case_summary": "Dosya Özeti",
    "important_dates": "Önemli Tarihler",
    "court_orders": "Ara Kararlar ve Yapılacaklar",
    "new_updates": "Yeni Ne Var?",
    "parties": "Taraflar",
    "tebligat": "Tebligatlar",
    "classification": "Akıllı Sınıflandırma",
    "chronology": "Kronoloji",
    "ask": "Dosyada Sor",
}
# Deterministic, deliberately narrow evidence selection; no legal inference.
HINTS = {
    "important_dates": ("duruşma", "tebliğ", "tebligat", "ara karar", "tensip",
                        "süre", "son gün", "müddet", "itiraz", "istinaf"),
    "parties": ("davacı", "davalı", "vekili", "vekil", "şüpheli", "sanık",
                "müşteki", "katılan", "taraf", "müdafi", "kurum"),
    "tebligat": ("tebliğ", "tebligat", "mazbata", "uets", "okundu", "zarfta"),
    "classification": ("dilekçe", "karar", "rapor", "tutanak", "tebligat",
                       "müzekkere", "bilirkişi", "talep", "cevap", "itiraz"),
    "chronology": ("tarihli", "duruşma", "tebliğ", "başvuru", "karar",
                   "tensip", "itiraz", "tutanak", "işlem"),
    "new_updates": (),
    "court_orders": ("gereği düşünüldü", "ara karar", "verilmesine", "istenmesine", "süre"),
}
DATE_IN_TEXT = re.compile(r"(?<!\d)(?:\d{1,2}[./-]\d{1,2}[./-]20\d{2}|20\d{2}[./-]\d{1,2}[./-]\d{1,2})(?!\d)")


# Important dates must be dates of an identifiable event, not merely the
# document's archive timestamp or the UYAP header field "Dava tarihi".
EVENT_HINTS = (
    ("Duruşma", ("duruşma", "duruşmanın", "celse")),
    ("Tebliğ / tebligat", ("tebliğ", "teblig", "tebellüğ")),
    ("Ara karar", ("ara karar",)),
    ("Tensip", ("tensip",)),
    ("Keşif", ("keşif",)),
    ("Bilirkişi incelemesi", ("bilirkişi", "rapor teslim")),
    ("Başvuru / itiraz", ("istinaf", "itiraz", "başvuru")),
    ("Süre işlemi", ("kesin süre", "son gün", "süre son", "müddet")),
    ("Karar", ("gerekçeli karar", "hüküm", "karar ver", "karar tarihi")),
)


def _normalize_event_date(value):
    """Accept only valid calendar dates, normalized for cross-format checks."""
    token = str(value or "").strip()
    parts = re.split(r"[./-]", token)
    if len(parts) != 3:
        return None
    try:
        if len(parts[0]) == 4:
            year, month, day = map(int, parts)
        else:
            day, month, year = map(int, parts)
        return datetime(year, month, day).date().isoformat()
    except (ValueError, TypeError):
        return None


def _date_events(text):
    """Extract only explicit dates close to a named legal event on one line.

    Keep provenance snippets short, avoiding arbitrary unrelated dates in a
    long PDF chunk. This is an evidence candidate, not a calculated deadline.
    """
    events = []
    for raw_line in str(text or "").splitlines():
        line = _clean(raw_line)
        if not line:
            continue
        for match in DATE_IN_TEXT.finditer(line):
            iso = _normalize_event_date(match.group())
            if not iso:
                continue
            # A UYAP header "Dava tarihi: 23/03/2026" is case metadata,
            # even when unrelated event wording appears nearby on the same line.
            preceding_label = line[max(0, match.start() - 48):match.start()].casefold()
            if re.search(r"(?:dava|dosya)\s+tarih[iî]?\s*:\s*$", preceding_label):
                continue
            before = line[max(0, match.start() - 95):match.start()]
            after = line[match.end():min(len(line), match.end() + 95)]
            window = (before + " " + after).casefold()
            label = next(
                (name for name, words in EVENT_HINTS if any(term in window for term in words)),
                None,
            )
            if not label:
                continue
            # "Dava tarihi: ..." alone cannot be a hearing, notice or judgment.
            evidence = _clean(line[max(0, match.start()-110):min(len(line),match.end()+110)])
            events.append({
                "eventDate": iso,
                "eventType": label,
                "snippet": evidence,
            })
    return events


def _important_dates_context(db, scope_id, limit):
    rows = db.execute(
        """SELECT c.source_ref, c.text, d.title, d.document_type,
                  d.document_date, c.chunk_no
           FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref
           WHERE d.session_id=? ORDER BY c.source_ref, c.chunk_no LIMIT 20000""",
        (scope_id,),
    )
    candidates = {}
    for ref, text, title, doc_type, doc_date, chunk_no in rows:
        for event in _date_events(text):
            key = (ref, event["eventDate"], event["eventType"])
            if key in candidates:
                continue
            weight = 20 if event["eventType"] in ("Duruşma","Tebliğ / tebligat","Ara karar") else 10
            candidates[key] = {
                "sourceRef": ref,
                "title": title,
                "documentType": doc_type,
                "documentDate": doc_date,
                "eventDate": event["eventDate"],
                "eventType": event["eventType"],
                "snippet": event["snippet"],
                "score": weight,
                "chunkNo": int(chunk_no or 0),
                "selectionReason": "Metinde olay ve açık tarih yakınlığı",
            }
    if not candidates:
        return _empty(
            "important_dates",
            "Doğrulanmış ve metni okunabilen evraklarda, aynı bağlamda açık "
            "bir hukuki olay ve tarih eşleşmesi bulunamadı. Bu sonuç, "
            "dosyada önemli tarih olmadığı anlamına gelmez.",
            "AI_IMPORTANT_DATES_NO_EVENT_EVIDENCE",
        )
    ordered = sorted(
        candidates.values(),
        key=lambda r: (-r["score"], r["eventDate"], r["sourceRef"]),
    )
    selected = []
    seen_sources = set()
    for row in ordered:
        if row["sourceRef"] not in seen_sources:
            selected.append(row)
            seen_sources.add(row["sourceRef"])
        if len(selected) >= limit:
            break
    selected.sort(key=lambda r: (r["eventDate"],r["sourceRef"]))
    return {
        "ok": True, "kind": "UYAP_AI_WORKFLOW_CONTEXT", "schemaVersion": 1,
        "workflowId": "important_dates", "results": selected,
        "notice": f"Yalnız açık olay-tarih kanıtı bulunan {len(selected)} seçili kaynağın analizi.",
        "networkUsed": False, "rawDocumentsUploaded": False,
        "distinctSources": len(selected), "coverage": "selected_events_only",
    }


def _empty(workflow_id, notice, code):
    return {
        "ok": True, "kind": "UYAP_AI_WORKFLOW_CONTEXT", "schemaVersion": 1,
        "workflowId": workflow_id, "results": [], "notice": notice,
        "noticeCode": code, "networkUsed": False, "rawDocumentsUploaded": False,
    }


def _baseline_diff(db, scope_id):
    """First run establishes a baseline; later runs report only hash-level changes.

    Baseline persists across index rebuilds. No implicit acknowledgement after
    an AI call, so a failed model call cannot silently lose a detected change.
    """
    db.execute("""CREATE TABLE IF NOT EXISTS ai_workflow_baseline(
        document_id_hash TEXT PRIMARY KEY,
        file_sha256 TEXT NOT NULL,
        saved_at TEXT NOT NULL
    )""")
    old = dict(db.execute(
        "SELECT document_id_hash, file_sha256 FROM ai_workflow_baseline"
    ).fetchall())
    current_rows = db.execute(
        "SELECT source_ref, document_id_hash, file_sha256 FROM ai_documents WHERE session_id=?",
        (scope_id,),
    ).fetchall()
    if not current_rows:
        return set(), "Doğrulanmış ve okunabilir AI kaynağı bulunamadı.", "AI_NO_INDEXED_DOCUMENTS"
    if not old:
        now = datetime.now(timezone.utc).isoformat()
        db.executemany(
            "INSERT INTO ai_workflow_baseline(document_id_hash,file_sha256,saved_at) VALUES(?,?,?)",
            [(doc_hash, sha, now) for _, doc_hash, sha in current_rows],
        )
        db.commit()
        return set(), (
            "İlk karşılaştırma başlangıcı kaydedildi. Yeni evrak veya değişiklik tespit etmek "
            "için arşivi ve AI indeksini güncelledikten sonra bu işlemi tekrar çalıştırın."
        ), "AI_UPDATES_BASELINE_CREATED"
    changed = {
        ref for ref, doc_hash, sha in current_rows
        if doc_hash not in old or old[doc_hash] != sha
    }
    removed = len(set(old) - {doc_hash for _, doc_hash, _ in current_rows})
    if not changed:
        note = "Başlangıç kaydına göre doğrulanmış AI indeksinde yeni/değişmiş evrak bulunamadı."
        if removed:
            note += (
                f" Önceki indekste bulunan {removed} kaynak artık bu doğrulanmış indekste "
                "görünmüyor; silindiği veya hukuki sonuç doğurduğu varsayılmamalıdır."
            )
        return set(), note, "AI_UPDATES_NO_NEW_DOCUMENTS"
    return changed, (
        f"İlk karşılaştırma kaydına göre {len(changed)} yeni/değişmiş doğrulanmış evrak bulundu."
        + (f" {removed} eski kayıt artık indekste yok; bunların içeriği AI'ya sunulmayacak." if removed else "")
    ), None


def workflow_context(downloads_root, scope_id, workflow_id, query="", limit=6):
    if workflow_id not in ALL_WORKFLOW_IDS:
        raise ValueError("AI_WORKFLOW_UNSUPPORTED")
    if workflow_id == "case_summary":
        return summary_context(downloads_root, scope_id, limit)
    if workflow_id == "ask":
        return search_index(downloads_root, scope_id, query, limit)

    root = Path(downloads_root).resolve(strict=True)
    scope_id = str(scope_id or "").strip()
    if not scope_id.startswith("case:"):
        raise ValueError("AI_WORKFLOW_ARCHIVE_CASE_REQUIRED")
    limit = max(1, min(int(limit or 6), 20))
    db, _ = _index_db(root, scope_id)
    try:
        if workflow_id == "important_dates":
            return _important_dates_context(db, scope_id, limit)
        allowed_refs = None
        notice = None
        if workflow_id == "new_updates":
            allowed_refs, notice, notice_code = _baseline_diff(db, scope_id)
            if notice_code:
                return _empty(workflow_id, notice, notice_code)

        rows = db.execute(
            """SELECT c.source_ref, c.text, d.title, d.document_type,
                      d.document_date, c.chunk_no
               FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref
               WHERE d.session_id=?
               ORDER BY d.document_date DESC, c.source_ref, c.chunk_no
               LIMIT 20000""",
            (scope_id,),
        )
        hints = HINTS[workflow_id]
        best = {}
        for ref, text, title, doc_type, date, chunk_no in rows:
            if allowed_refs is not None and ref not in allowed_refs:
                continue
            snippet = _clean(text)
            if not snippet:
                continue
            haystack = (str(title or "") + " " + snippet[:3200]).casefold()
            matches = sum(1 for keyword in hints if keyword in haystack)
            has_date = bool(DATE_IN_TEXT.search(snippet))
            valid_doc_date = _summary_date_key(date) != (0, 0, 0)
            if workflow_id == "important_dates" and (not has_date or matches == 0):
                continue
            if workflow_id == "parties" and matches == 0:
                continue
            if workflow_id == "chronology" and not (has_date or valid_doc_date):
                continue
            if workflow_id == "classification":
                # Every document is eligible, even when its filename is opaque.
                score = matches * 4 + min(5, len(snippet) // 400)
            else:
                score = matches * 10 + (6 if has_date else 0) + (3 if valid_doc_date else 0)
            if workflow_id == "new_updates":
                score += 50
            candidate = {
                "sourceRef": ref, "title": title, "documentType": doc_type,
                "documentDate": date, "snippet": snippet[:1800],
                "score": score, "chunkNo": int(chunk_no or 0),
                "selectionReason": WORKFLOW_LABELS[workflow_id] + " için kaynak eşleşmesi",
            }
            if ref not in best or candidate["score"] > best[ref]["score"]:
                best[ref] = candidate
        selected = sorted(
            best.values(),
            key=lambda row: (
                -row["score"],
                tuple(-part for part in _summary_date_key(row["documentDate"])),
                row["sourceRef"],
            ),
        )[:limit]
        if workflow_id == "chronology":
            selected.sort(key=lambda row: (_summary_date_key(row["documentDate"]), row["sourceRef"]))
        if not selected:
            return _empty(
                workflow_id,
                "Bu iş akışı için doğrulanmış evrak metinlerinde yeterli kaynak bulunamadı.",
                "AI_WORKFLOW_NO_MATCHING_SOURCES",
            )
        return {
            "ok": True, "kind": "UYAP_AI_WORKFLOW_CONTEXT", "schemaVersion": 1,
            "workflowId": workflow_id, "results": selected,
            "notice": notice, "networkUsed": False, "rawDocumentsUploaded": False,
            "distinctSources": len(selected), "coverage": "selected_sources_only",
        }
    finally:
        db.close()
