"""Rule-based "Önemli Tarihler" for one archive case. No model is involved.

The view answers what a lawyer asks first: what is ahead (next hearing,
running time limits) and what happened last; older turning points follow as
milestones. Every line carries its source tags [Dxxxxxx].

Inputs:
- the case catalog written at index build: every document's UYAP title and
  date, readable or not (a scanned tebligat still dates an event), and
- the full text of readable records: header date labels (ARA KARAR TARİHİ,
  KARAR TARİHİ, DURUŞMA TARİHİ, DAVA TARİHİ), numbered decisions, the
  "duruşmanın … günü saat … bırakılmasına" clause and granted time limits.

A time limit's end date is never presented as a legal deadline. Only its
earliest possible end is shown, counted from the decision date: service or
pronouncement cannot precede the decision. The real end depends on tebliğ,
holidays and judicial recess and must be checked against the record.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date as Date, timedelta
from pathlib import Path

from ai_case_facts import mask_identifiers, find_dates, parse_date, parse_hearing, tr_lower, tr_title
from ai_local_index import DEFAULT_OVERLAP_CHARS, _index_db

DATES_SCHEMA = "important_dates_v2"
RETRIEVAL_MODE = "important_dates_rules_v2"
RECENT_WINDOW_DAYS = 45
DEADLINE_LOOKBACK_DAYS = 120
MAX_RECENT_LINES = 14
MAX_MILESTONE_LINES = 12
MAX_REFS_PER_LINE = 4
# ai_meta key holding the document-id hashes this view has already shown.
SEEN_KEY = "dates_seen"

NOTICE = (
    "Tarihler kaynaktan kurallarla çıkarıldı; AI modeli kullanılmadı. Süreler hukuki hesap değildir: "
    "\"en erken bitiş\" yalnız karar tarihinden sayılır; tebliğ, tatil ve adli tatile göre zaptı kontrol edin."
)

# (kind, label, milestone, title patterns). The first match wins, so order
# matters: "İstinaf Başvuru Dilekçesi" is an appeal, not the opening petition;
# "Ara Karar Evrakı" is an interim decision, not the judgment; "Müzekkere
# Cevabı" is incoming mail. A None label means "use the cleaned title".
TITLE_EVENTS = (
    ("appeal_sent", "Dosya üst mahkemeye gönderildi", True,
     ("bam dosya gönder", "dosya gönderme kontrol", "yargıtaya gönder", "yargıtay'a gönder")),
    ("appeal", "İstinaf başvurusu", True, ("istinaf başvuru", "istinaf dilek")),
    ("appeal", "Temyiz başvurusu", True, ("temyiz başvuru", "temyiz dilek")),
    ("appeal", None, True, ("istinaf formu", "temyiz formu")),
    ("finality", "Kesinleşme", True, ("kesinleşme", "kesinleştirme")),
    ("interim", "Ara karar", True, ("ara karar",)),
    ("decision", None, True,
     ("gerekçeli karar", "kısa karar", "karar ilam", "nihai karar", "hüküm", "karar evrak")),
    ("tensip", None, True, ("tensip",)),
    ("hearing", None, True,
     ("duruşma zapt", "duruşma tutana", "ön inceleme", "tahkikat", "sözlü yargılama")),
    ("expert", None, True, ("bilirkişi",)),
    ("site", None, True, ("keşif",)),
    ("opening", None, True,
     ("dava dilek", "iddianame", "takip talebi", "şikayet dilek", "şikâyet dilek", "başvuru dilek")),
    ("minutes", None, False, ("tutanak", "zabıt", "zapt")),
    # Answer / reply / rejoinder / defence close the written phase: milestones.
    ("reply", None, True,
     ("cevap dilek", "cevaba cevap", "ikinci cevap", "savunma dilek", "bilirkişi raporuna itiraz")),
    ("petition", None, False, ("dilekçe", "dilekce", "savunma", "beyan", "mazeret")),
    ("notice", None, False, ("tebligat", "tebliğ", "mazbata", "muhtıra")),
    ("low", None, False,
     ("reddiyat", "makbuz", "tahsilat", "harç", "masraf", "alındı", "dekont", "vekaletname", "vekalet pulu",
      "tevzi formu", "nüfus kayıt", "fişi")),
    ("letter_in", "Gelen evrak / cevap yazısı", False,
     ("müzekkere cevab", "dosyaya eklenecek", "gelen cevap", "cevap yazı", "kurum ve kuruluş", "gelen evrak")),
    ("letter_out", "Müzekkere / yazı gönderildi", False, ("müzekkere", "talimat", "üst yazı", "tekit")),
)
RECORD_KINDS = {"interim", "decision", "tensip", "hearing", "minutes", "appeal_sent", "finality"}
# Routine traffic is grouped per day ("Müzekkere / yazı gönderildi (12)").
GROUPED_KINDS = {"letter_in", "letter_out", "notice", "petition", "other", "minutes"}

_RECORD_DATE = re.compile(
    r"(?im)^\s*(?:ARA\s+KARAR|KARAR|DURU[ŞS]MA|TENS[İI]P|CELSE)\s+TAR[İI]H[İI]\s*:\s*(\d{1,2}[./]\d{1,2}[./]\d{4})")
_DAVA_DATE = re.compile(r"(?im)^\s*DAVA\s+TAR[İI]H[İI]\s*:\s*(\d{1,2}[./]\d{1,2}[./]\d{4})")
_CLOSING_DATE = re.compile(r"(?i)karar\s+verildi\.?\s*(\d{1,2}/\d{1,2}/\d{4})")
_UPPER_COURT = (("bölge adliye mahkemesi", "Bölge Adliye Mahkemesi kararı"), ("yargıtay", "Yargıtay kararı"))
_FINAL = re.compile(r"kesin\s+olmak\s+üzere")
_REMEDY_OPEN = re.compile(r"(istinaf|temyiz)\s+yolu\s+açık")
_NUMBER_WORDS = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7, "sekiz": 8,
                 "dokuz": 9, "on": 10, "onbeş": 15, "on beş": 15, "yirmi": 20, "otuz": 30}
_LIMIT = re.compile(
    r"(?<![\w/])(\d{1,3}|bir|iki|üç|dört|beş|altı|yedi|sekiz|dokuz|on\s?beş|on|yirmi|otuz)\s*"
    r"(haftalık|günlük|aylık|hafta|gün|ay)\b(\s+kesin)?\s+(?:süre|içinde|içerisinde)")


# --------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------
def _as_date(value):
    if isinstance(value, tuple) and len(value) == 3:
        try:
            return Date(*value)
        except ValueError:
            return None
    parsed = parse_date(value)
    if not parsed:
        return None
    try:
        return Date(*parsed)
    except ValueError:
        return None


def _fmt(value):
    return value.strftime("%d.%m.%Y") if value else ""


def _today(today=None):
    if today:
        return _as_date(today)
    now = time.localtime()
    return Date(now.tm_year, now.tm_mon, now.tm_mday)


def clean_title(title):
    value = str(title or "")
    value = re.sub(r"\s*\d{1,2}[._/]\d{1,2}[._/]\d{4}", "", value)
    value = re.sub(r"\s*\(\d+\)", "", value)
    value = re.sub(r"(?i)\s*-\s*ek\s*\d+", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -_")
    return value or "Adsız evrak"


def classify_title(title):
    """(kind, label, milestone) for a UYAP document title."""
    folded = tr_lower(title)
    for kind, label, milestone, patterns in TITLE_EVENTS:
        if any(pattern in folded for pattern in patterns):
            return kind, label or clean_title(title), milestone
    return "other", clean_title(title), False


def _short(value, limit):
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ,;")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _add_duration(start, amount, unit):
    if unit.startswith("hafta"):
        return start + timedelta(days=7 * amount)
    if unit.startswith("gün"):
        return start + timedelta(days=amount)
    month = start.month - 1 + amount
    year, month = start.year + month // 12, month % 12 + 1
    for day in (start.day, 30, 29, 28):
        try:
            return Date(year, month, day)
        except ValueError:
            continue
    return None


def _join_chunks(chunks):
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


# --------------------------------------------------------------------------
# Records with text
# --------------------------------------------------------------------------
def time_limits(text, decided_on):
    """Time limits granted in decision text, each with its earliest possible end."""
    found = []
    raw = str(text or "")
    lowered = tr_lower(raw)
    aligned = len(lowered) == len(raw)
    for match in _LIMIT.finditer(lowered):
        word = re.sub(r"\s+", " ", match.group(1))
        amount = int(word) if word.isdigit() else _NUMBER_WORDS.get(word)
        if not amount:
            continue
        unit = match.group(2)
        # The clause that grants the limit, not the whole decision paragraph.
        before = lowered[: match.start()]
        start = max(before.rfind(","), before.rfind(";"), before.rfind("\n"), before.rfind(". ")) + 1
        end_candidates = [pos for pos in (lowered.find(",", match.end()), lowered.find(";", match.end()),
                                          lowered.find("\n", match.end())) if pos >= 0]
        end = min(end_candidates) if end_candidates else len(lowered)
        clause = (raw if aligned else lowered)[start:end]
        remedy = re.search(r"istinaf|temyiz|itiraz", lowered[match.start(): end + 120])
        found.append({
            "amount": amount,
            "unit": "hafta" if unit.startswith("hafta") else ("gün" if unit.startswith("gün") else "ay"),
            "kesin": bool(match.group(3)),
            "remedy": remedy.group(0) if remedy else None,
            "clause": _short(tr_title(clause), 170),
            "earliestEnd": _add_duration(decided_on, amount, unit) if decided_on else None,
        })
    return found


_HEARING_TYPES = (("ön inceleme", "Ön inceleme duruşması"), ("tahkikat", "Tahkikat duruşması"),
                  ("sözlü yargılama", "Sözlü yargılama duruşması"), ("keşif", "Keşif"))


def _hearing_type(raw, day):
    """"Ön inceleme duruşmasının 11/11/2026 günü …" names the kind of hearing."""
    if not day:
        return None
    lowered = tr_lower(raw)
    year, month, dd = day
    for variant in (f"{dd:02d}/{month:02d}/{year}", f"{dd}/{month}/{year}",
                    f"{dd:02d}.{month:02d}.{year}", f"{dd}.{month}.{year}"):
        position = lowered.find(variant)
        if position >= 0:
            window = lowered[max(0, position - 160):position]
            return next((label for needle, label in _HEARING_TYPES if needle in window), None)
    return None


def record_info(text, title):
    """Date, decisions, next hearing and time limits of a court record."""
    raw = str(text or "")
    head = tr_lower(raw[:1600])
    hearing = parse_hearing(raw, title)
    match = _RECORD_DATE.search(raw)
    record_date = _as_date(match.group(1)) if match else None
    if not record_date:
        closing = _CLOSING_DATE.findall(raw)
        record_date = _as_date(closing[-1]) if closing else None
    record_date = record_date or _as_date(hearing.get("date"))
    dava = _DAVA_DATE.search(raw)
    court_label = next((label for needle, label in _UPPER_COURT if needle in head), None)
    lowered = tr_lower(raw)
    remedy = _REMEDY_OPEN.search(lowered)
    decisions = hearing.get("decisions") or []
    return {
        "recordDate": record_date,
        "davaDate": _as_date(dava.group(1)) if dava else None,
        "celse": hearing.get("celse"),
        "isTalimat": bool(hearing.get("isTalimat")),
        "court": hearing.get("court"),
        "decisions": decisions,
        "nextHearing": hearing.get("nextHearing"),
        "nextHearingType": _hearing_type(raw, (hearing.get("nextHearing") or {}).get("date")),
        "upperCourt": court_label,
        "final": bool(_FINAL.search(lowered)),
        "remedyOpen": remedy.group(1) if remedy else None,
        "limits": time_limits("\n".join(decisions) or raw[-3000:], record_date),
    }


def _record_detail(kind, info):
    parts = []
    if kind == "hearing" and info.get("celse"):
        parts.append(f"{info['celse']}. celse")
    if kind in ("hearing", "minutes") and info.get("decisions"):
        parts.append(f"{len(info['decisions'])} ara karar")
    if kind == "interim" and info.get("decisions"):
        parts.append(_short(tr_title(info["decisions"][0]), 110))
    if kind == "decision":
        if info.get("final"):
            parts.append("kesin")
        elif info.get("remedyOpen"):
            parts.append(f"{info['remedyOpen']} yolu açık")
    return ", ".join(parts) or None


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def _merge(events):
    merged = {}
    for event in events:
        key = (event["date"], event["kind"], event["label"], event.get("detail"), event.get("time"))
        if key in merged:
            target = merged[key]
            target["count"] += event.get("count", 1)
            target["refs"].extend(ref for ref in event["refs"] if ref not in target["refs"])
        else:
            merged[key] = {**event, "count": event.get("count", 1), "refs": list(event["refs"])}
    return list(merged.values())


def _item(day, text, refs, kind, time_text=None):
    shown = list(dict.fromkeys(refs))[:MAX_REFS_PER_LINE]
    extra = len(dict.fromkeys(refs)) - len(shown)
    masked, _ = mask_identifiers(text)
    return {
        "date": day.isoformat() if day else None,
        "dateText": _fmt(day) + (f" saat {time_text}" if time_text else ""),
        "text": masked,
        "refs": shown,
        "moreRefs": max(0, extra),
        "kind": kind,
    }


def _event_text(event):
    text = event["label"]
    if event["count"] > 1 and event["kind"] in GROUPED_KINDS:
        text += f" ({event['count']})"
    if event.get("detail"):
        text += f" — {event['detail']}"
    return text


def build_important_dates(catalog, text_documents, today=None, total_documents=None, catalog_available=True,
                          new_refs=None, tracking=None):
    """Sections for one case.

    catalog: [{"ref", "title", "date", "indexed"}] for every case document.
    text_documents: {ref: {"title", "date", "text"}} for readable documents.
    new_refs: refs that arrived since the previous index state; lines resting
    on them get "isNew". tracking: "first" | "tracked" | "unavailable" | None.
    """
    today = _today(today)
    new_refs = set(new_refs or ())
    marked = set()

    def _line(day, text, refs, kind, time_text=None):
        built = _item(day, text, refs, kind, time_text)
        hit = new_refs.intersection(refs)
        if hit:
            built["isNew"] = True
            marked.update(hit)
        return built

    events, records, dava_dates = [], [], {}
    low = undated = 0
    for document in catalog:
        ref = document["ref"]
        kind, label, milestone = classify_title(document.get("title"))
        if kind == "low":
            low += 1
            continue
        day = _as_date(document.get("date"))
        detail = None
        source = text_documents.get(ref)
        if source and (kind in RECORD_KINDS or kind == "other"):
            info = record_info(source.get("text"), document.get("title"))
            if kind == "other" and not (info["recordDate"] and (info["decisions"] or info["nextHearing"])):
                info = None
            if info:
                day = info["recordDate"] or day
                if info["davaDate"]:
                    dava_dates.setdefault(info["davaDate"], []).append(ref)
                if kind == "decision" and info["upperCourt"]:
                    label = info["upperCourt"]
                if kind == "hearing" and info["isTalimat"]:
                    label = "Talimat duruşması" + (f" ({info['court']})" if info.get("court") else "")
                detail = _record_detail(kind, info)
                records.append({"ref": ref, "kind": kind, "date": day, "info": info})
        if not day:
            undated += 1
            continue
        events.append({"date": day, "kind": kind, "label": label, "detail": detail,
                       "milestone": milestone, "refs": [ref]})

    events = _merge(events)
    if dava_dates:
        # The "DAVA TARİHİ" header of the records dates the opening even when
        # the petition itself is unreadable; same-day petitions fold into it.
        day, refs = max(dava_dates.items(), key=lambda item: (len(item[1]), item[0]))
        same_day = [e for e in events if e["kind"] == "opening" and e["date"] == day]
        refs = [ref for e in same_day for ref in e["refs"]] + refs[:1]
        label = "Dava açıldı — " + tr_lower(same_day[0]["label"]) if same_day else \
            "Dava açıldı (UYAP künyesindeki dava tarihi)"
        events = [e for e in events if e not in same_day]
        events.append({"date": day, "kind": "opening", "label": label, "detail": None, "milestone": True,
                       "refs": list(dict.fromkeys(refs)), "count": max(1, len(same_day))})
    latest = max((e["date"] for e in events), default=None)

    # ---- Yaklaşan -------------------------------------------------------
    upcoming = []
    with_next = [r for r in records if r["info"].get("nextHearing") and r["date"]]
    own = [r for r in with_next if not r["info"]["isTalimat"]] or with_next
    next_record = max(own, key=lambda r: (r["date"], r["ref"])) if own else None
    next_day = None
    if next_record:
        hearing = next_record["info"]["nextHearing"]
        next_day = _as_date(hearing["date"])
        if next_day and next_day >= today:
            days = (next_day - today).days
            when = "bugün" if days == 0 else ("yarın" if days == 1 else f"{days} gün sonra")
            title = "Talimat duruşması" if next_record["info"]["isTalimat"] else (
                next_record["info"].get("nextHearingType") or "Duruşma")
            upcoming.append(_line(next_day, f"{title} — {when}", [next_record["ref"]], "hearing",
                                  hearing.get("time")))
        elif next_day:
            upcoming.append(_line(next_day, "Son kayıtlı duruşma günü geçti; o günün zaptı arşivde henüz yok",
                                  [next_record["ref"]], "stale", hearing.get("time")))
    for record in sorted(records, key=lambda r: (r["date"] or Date.min, r["ref"]), reverse=True):
        decided = record["date"]
        if not decided or (today - decided).days > DEADLINE_LOOKBACK_DAYS:
            continue
        for limit in record["info"]["limits"]:
            end = limit["earliestEnd"]
            if not end or end < today:
                continue
            what = {"istinaf": "İstinaf süresi", "temyiz": "Temyiz süresi", "itiraz": "İtiraz süresi"}.get(
                limit["remedy"], "Süre")
            upcoming.append(_line(end, f"{what} en erken biter — {limit['clause']} "
                                       f"({_fmt(decided)} kararından sayıldı)", [record["ref"]], "limit"))
        for found in sorted(find_dates(" ".join(record["info"]["decisions"]))):
            day = _as_date(found)
            if day and day > today and day != next_day:
                clause = next((d for d in record["info"]["decisions"] if found in find_dates(d)), "")
                upcoming.append(_line(day, "Kararda geçen ileri tarih — " + _short(tr_title(clause), 120),
                                      [record["ref"]], "future"))
    upcoming.sort(key=lambda item: item["date"] or "")

    # ---- Son işlemler / kilometre taşları -------------------------------
    cutoff = latest - timedelta(days=RECENT_WINDOW_DAYS) if latest else None
    recent = sorted((e for e in events if cutoff and e["date"] >= cutoff),
                    key=lambda e: (e["date"], e["milestone"], e["kind"]), reverse=True)
    recent_items = [_line(e["date"], _event_text(e), e["refs"], e["kind"]) for e in recent[:MAX_RECENT_LINES]]
    hidden_recent = max(0, len(recent) - MAX_RECENT_LINES)
    older = sorted((e for e in events if e["milestone"] and (not cutoff or e["date"] < cutoff)),
                   key=lambda e: (e["date"], e["kind"]))
    milestones = older
    if len(older) > MAX_MILESTONE_LINES:
        milestones = older[:3] + older[-(MAX_MILESTONE_LINES - 3):]
    milestone_items = [_line(e["date"], _event_text(e), e["refs"], e["kind"]) for e in milestones]
    if len(older) > MAX_MILESTONE_LINES:
        milestone_items.insert(3, {"date": None, "dateText": "…", "text": f"{len(older) - MAX_MILESTONE_LINES} "
                                   "ara kilometre taşı gösterilmedi", "refs": [], "moreRefs": 0, "kind": "gap"})

    # ---- Kapsam ---------------------------------------------------------
    indexed = sum(1 for document in catalog if document.get("indexed"))
    total = total_documents if isinstance(total_documents, int) else len(catalog)
    coverage_lines = []
    if catalog_available:
        coverage_lines.append(f"{total} evrakın tamamı başlık ve UYAP tarihiyle, {indexed} tanesi metniyle tarandı.")
    else:
        coverage_lines.append(
            f"Yalnız metni okunan {indexed} evrak tarandı. Metni okunamayan evrakları da (taranmış tebligat, "
            "tutanak) başlık ve tarihleriyle eklemek için \"Tam dava indeksini güncelle\" çalıştırın.")
    if low:
        coverage_lines.append(f"{low} mali / idari kayıt (makbuz, reddiyat, vekaletname…) listelenmedi.")
    if hidden_recent:
        coverage_lines.append(f"Son işlemlerde {hidden_recent} kayıt daha var; tamamı Evraklar sekmesinde.")
    if undated:
        coverage_lines.append(f"{undated} evrakın tarihi olmadığı için listeye alınamadı.")
    if tracking == "first":
        coverage_lines.append("YENİ işaretleri bundan sonra: indeks güncellendiğinde arşive yeni giren evraklara "
                              "dayanan satırlar işaretlenecek.")
    elif tracking == "tracked" and new_refs:
        line = f"Önceki indeks durumuna göre {len(new_refs)} yeni evrak var"
        unshown = len(new_refs - marked)
        if marked:
            line += "; dayandıkları satırlar YENİ ile işaretli"
        if unshown:
            line += f"; {unshown} tanesi bu listede yer almıyor (Evraklar sekmesinde)"
        coverage_lines.append(line + ".")
    elif tracking == "tracked":
        coverage_lines.append("Önceki indeks durumuna göre yeni evrak yok.")
    elif tracking == "unavailable" and catalog_available:
        coverage_lines.append("YENİ işaretleri için \"Tam dava indeksini güncelle\" bir kez çalıştırılmalı.")

    sections = [
        {"key": "upcoming", "title": "Yaklaşan", "highlight": True, "items": upcoming,
         "empty": "İleri tarihli duruşma veya işleyen süre kaydı bulunamadı."},
        {"key": "recent", "title": "Son işlemler", "highlight": False, "items": recent_items,
         "subtitle": f"{_fmt(cutoff)} – {_fmt(latest)}" if latest else None,
         "empty": "Tarihli işlem bulunamadı."},
        {"key": "milestones", "title": "Kilometre taşları", "highlight": False, "items": milestone_items,
         "empty": "Daha eski kilometre taşı yok."},
        {"key": "coverage", "title": "Kapsam", "highlight": False,
         "items": [{"date": None, "dateText": "", "text": line, "refs": [], "moreRefs": 0, "kind": "note"}
                   for line in coverage_lines]},
    ]
    return {
        "sections": sections,
        "answer": render_text(sections),
        "refs": list(dict.fromkeys(ref for s in sections for i in s["items"] for ref in i["refs"])),
        "coverage": {"totalDocuments": total, "indexedDocuments": indexed, "catalogAvailable": catalog_available,
                     "lowValueSkipped": low, "undated": undated, "latestRecord": latest.isoformat() if latest else None,
                     "newDocuments": len(new_refs), "newTracking": tracking},
    }


def _tr_upper(value):
    return str(value or "").translate(str.maketrans({"i": "İ", "ı": "I"})).upper()


def render_text(sections):
    """Plain text that also reads well where the UI shows it as-is."""
    blocks = []
    for section in sections:
        title = _tr_upper(section["title"])
        if section["key"] == "recent" and section.get("subtitle"):
            title += f" ({section['subtitle']})"
        lines = []
        for item in section["items"]:
            refs = "".join(f" [{ref}]" for ref in item["refs"]) + (f" +{item['moreRefs']}" if item.get("moreRefs") else "")
            prefix = f"{item['dateText']} · " if item.get("dateText") and item["dateText"] != "…" else (
                "… " if item.get("dateText") == "…" else "")
            lines.append(f"- {prefix}{'[YENİ] ' if item.get('isNew') else ''}{item['text']}{refs}")
        if not lines and section.get("empty"):
            lines.append(f"- {section['empty']}")
        blocks.append(title + "\n" + "\n".join(lines))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------
# Index access
# --------------------------------------------------------------------------
def _load(downloads_root, scope_id):
    root = Path(downloads_root).resolve(strict=True)
    db, _ = _index_db(root, scope_id)
    try:
        documents = {}
        for ref, title, document_date in db.execute(
            "SELECT source_ref, title, document_date FROM ai_documents WHERE session_id=?", (scope_id,)
        ).fetchall():
            documents[ref] = {"title": title, "date": document_date, "chunks": []}
        for ref, text in db.execute(
            """SELECT c.source_ref, c.text FROM ai_chunks c JOIN ai_documents d ON d.source_ref=c.source_ref
               WHERE d.session_id=? ORDER BY c.source_ref, c.chunk_no""", (scope_id,)
        ).fetchall():
            if ref in documents:
                documents[ref]["chunks"].append(text)
        for document in documents.values():
            document["text"] = _join_chunks(document.pop("chunks"))
        row = db.execute("SELECT value FROM ai_meta WHERE key='case_catalog'").fetchone()
        try:
            catalog = json.loads(row[0]) if row else None
        except (TypeError, ValueError):
            catalog = None
    finally:
        db.close()
    return documents, catalog


def load_case(downloads_root, scope_id):
    """(catalog rows, readable documents, total documents, catalog available) for a case scope."""
    documents, catalog = _load(downloads_root, scope_id)
    catalog_rows = (catalog or {}).get("documents") if isinstance(catalog, dict) else None
    if catalog_rows:
        rows = [{"ref": str(d.get("ref")), "title": d.get("title"), "date": d.get("date"),
                 "indexed": bool(d.get("indexed")), "idHash": d.get("idHash")} for d in catalog_rows if d.get("ref")]
        return rows, documents, catalog.get("totalDocuments"), True
    rows = [{"ref": ref, "title": d["title"], "date": d["date"], "indexed": True, "idHash": None}
            for ref, d in sorted(documents.items())]
    return rows, documents, None, False


def next_seen_state(rows, stored):
    """(new document-id hashes, tracking mode, state to store) for the "YENİ" marks.

    The stored state keeps the previous index state ("baseline") and the one
    shown last ("current"). Marks stay on re-runs and move only when an index
    update changes the document set, so a second look does not wipe them.
    """
    if not rows or any(not row.get("idHash") for row in rows):
        return set(), "unavailable", None
    current = sorted({str(row["idHash"]) for row in rows})
    try:
        baseline = set(stored["baseline"]) if isinstance(stored, dict) else None
        shown = set(stored["current"]) if isinstance(stored, dict) else None
    except (KeyError, TypeError):
        baseline = shown = None
    if baseline is None or shown is None:
        return set(), "first", {"schemaVersion": 1, "baseline": current, "current": current}
    if set(current) != shown:
        baseline = shown
    fresh = set(current) - baseline
    return fresh, "tracked", {"schemaVersion": 1, "baseline": sorted(baseline), "current": current}


def _read_seen(downloads_root, scope_id):
    db, _ = _index_db(Path(downloads_root).resolve(strict=True), scope_id)
    try:
        row = db.execute("SELECT value FROM ai_meta WHERE key=?", (SEEN_KEY,)).fetchone()
    finally:
        db.close()
    try:
        return json.loads(row[0]) if row else None
    except (TypeError, ValueError):
        return None


def _store_seen(downloads_root, scope_id, state):
    db, _ = _index_db(Path(downloads_root).resolve(strict=True), scope_id)
    try:
        db.execute("INSERT OR REPLACE INTO ai_meta(key,value,updated_at) VALUES(?,?,?)",
                   (SEEN_KEY, json.dumps(state), time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        db.commit()
    finally:
        db.close()


def important_dates_answer(downloads_root, scope_id, today=None):
    scope_id = str(scope_id or "").strip()
    if not scope_id.startswith("case:"):
        return {"ok": False, "errorCode": "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED",
                "error": "Önemli tarihler yalnız tam dava indeksi için hazırlanabilir."}
    try:
        rows, documents, total, catalog_available = load_case(downloads_root, scope_id)
    except (OSError, ValueError) as error:
        return {"ok": False, "errorCode": "AI_INDEX_UNAVAILABLE", "error": f"Dava indeksi okunamadı: {error}"}
    fresh, tracking, seen_state = set(), None, None
    if catalog_available:
        try:
            fresh, tracking, seen_state = next_seen_state(rows, _read_seen(downloads_root, scope_id))
        except (OSError, ValueError, sqlite3.Error):
            tracking = None
    new_refs = {row["ref"] for row in rows if row.get("idHash") in fresh}
    built = build_important_dates(rows, documents, today=today, total_documents=total,
                                  catalog_available=catalog_available, new_refs=new_refs, tracking=tracking)
    if seen_state:
        try:
            _store_seen(downloads_root, scope_id, seen_state)
        except (OSError, ValueError, sqlite3.Error):
            pass  # marks are a convenience; the dates stand without them
    titles = {row["ref"]: row for row in rows}
    sources = [{
        "sourceRef": ref,
        "title": (titles.get(ref) or {}).get("title"),
        "documentType": None,
        "documentDate": (titles.get(ref) or {}).get("date"),
        "selectionReason": "Kural tabanlı tarih",
        "summaryRole": "rule_based_date",
    } for ref in built["refs"]]
    return {
        "ok": True,
        "kind": "UYAP_AI_ANSWER",
        "schemaVersion": 1,
        "answer": built["answer"],
        "workflowId": "important_dates",
        "workflowTitle": "Önemli Tarihler",
        "workflowNotice": NOTICE,
        "datesSchema": DATES_SCHEMA,
        "dateSections": built["sections"],
        "dateCoverage": built["coverage"],
        "retrievalMode": RETRIEVAL_MODE,
        "sourceRefs": built["refs"],
        "sources": sources,
        "provider": "Yerel kurallar",
        "model": "",
        "providerCalled": False,
        "modelAnswerAccepted": None,
        "rawDocumentsUploaded": False,
        "networkUsed": False,
        "store": False,
    }
