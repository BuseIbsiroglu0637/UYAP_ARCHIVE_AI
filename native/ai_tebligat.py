"""Rule-based "Tebligatlar" for one archive case. No model.

UYAP files a tebligat twice. The court's envelope ("Kapalı / Açık Tebligat")
names the addressee, what is in it ("BU ZARFTA … VARDIR") and any ihtar
("iki hafta içinde cevap dilekçesi…"). For an e-tebligat the UETS result
follows ("Kapalı / Açık E-Tebliğ Mazbatası"): when the envelope reached the
account, when it was opened and the day it counts as served. A paper
tebligat comes back as a scanned "Tebliğ Mazbatası" that has no text.

An e-tebligat counts as served at the end of the fifth day after it reached
the account (Tebligat Kanunu 7/a; opening it earlier does not move it,
YHGK 24.11.2020, 2020/547 E. 2020/924 K.). The mazbata states that day.
Time limits are counted from it (HMK 92): the one the envelope's ihtar
states, a remedy the served judgment grants, and those a served decision
record gives this addressee's side. Weekends, official holidays and adli
tatil are only flagged; nothing is extended automatically.
"""
from __future__ import annotations

import re
from datetime import date as Date, datetime, timedelta

from ai_case_facts import mask_identifiers, tr_fold, tr_lower, tr_title
from ai_court_orders import _CLOSING, addressee, classify_action
from ai_important_dates import (
    RECORD_KINDS, _add_duration, _as_date, _fmt, _short, _today, _tr_upper, classify_title, clean_title, load_case,
    record_info, time_limits,
)

TEBLIGAT_SCHEMA = "tebligat_v1"
RETRIEVAL_MODE = "tebligat_rules_v1"
E_TEBLIGAT_DAYS = 5
# Deadlines that ended longer ago than this are left out of "Süresi işleyenler".
DEADLINE_PAST_DAYS = 30
MAX_DEADLINES = 12
MAX_GROUPS = 8
MAX_REFS = 3
# An envelope and its UETS result are matched when the result reached the
# account within this many days of the envelope's date.
MATCH_DAYS = 3

GOOD, WARN, BAD, INFO, MUTED = "good", "warn", "bad", "info", "muted"

NOTICE = (
    "Tebligatlar kaynaktan kurallarla çıkarıldı; AI modeli kullanılmadı. E-tebligatta tebliğ günü, mazbatadaki "
    "\"okundu sayıldı\" günüdür (hesaba konulduğu günü izleyen 5. gün; erken açılması değiştirmez). Son günler "
    "tebliğden sayıldı; hafta sonu, resmi tatil ve adli tatil yalnız işaretlenir. Süreyi mazbata ve karar "
    "metninden kontrol edin."
)

# --------------------------------------------------------------------------
# Calendar: official holidays are flagged, never applied silently.
# --------------------------------------------------------------------------
_FIXED_HOLIDAYS = {(1, 1): "Yılbaşı", (4, 23): "23 Nisan", (5, 1): "1 Mayıs", (5, 19): "19 Mayıs",
                   (7, 15): "15 Temmuz", (8, 30): "30 Ağustos", (10, 29): "29 Ekim"}
# Religious holidays move every year (Diyanet calendar). Years not listed here
# get only the fixed holidays and weekends flagged.
_RELIGIOUS_HOLIDAYS = {
    2026: (((3, 20), (3, 22), "Ramazan Bayramı"), ((5, 27), (5, 30), "Kurban Bayramı")),
    2027: (((3, 9), (3, 11), "Ramazan Bayramı"), ((5, 16), (5, 19), "Kurban Bayramı")),
}
_HALF_DAYS = {Date(2026, 3, 19): "Ramazan Bayramı arifesi", Date(2026, 5, 26): "Kurban Bayramı arifesi",
              Date(2026, 10, 28): "Cumhuriyet Bayramı arifesi", Date(2027, 3, 8): "Ramazan Bayramı arifesi",
              Date(2027, 5, 15): "Kurban Bayramı arifesi", Date(2027, 10, 28): "Cumhuriyet Bayramı arifesi"}
_WEEKDAYS = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


def holiday_name(day):
    name = _FIXED_HOLIDAYS.get((day.month, day.day))
    for start, end, label in _RELIGIOUS_HOLIDAYS.get(day.year, ()):
        if Date(day.year, *start) <= day <= Date(day.year, *end):
            return label
    return name


def _workday(day):
    return day.weekday() < 5 and not holiday_name(day)


def calendar_flags(day):
    """Lines that flag a last day falling on a non-working day or in adli tatil."""
    flags = []
    off = holiday_name(day) or (_WEEKDAYS[day.weekday()] if day.weekday() >= 5 else None)
    if off:
        nxt = day + timedelta(days=1)
        while not _workday(nxt):
            nxt += timedelta(days=1)
        flags.append(f"Son gün {off} gününe denk geliyor; süre ilk iş günü {_fmt(nxt)} "
                     f"({_WEEKDAYS[nxt.weekday()]}) bitebilir — kontrol edin.")
    elif day in _HALF_DAYS:
        flags.append(f"Son gün {_HALF_DAYS[day]} (yarım gün) — mesai bitişini kontrol edin.")
    if Date(day.year, 7, 20) <= day <= Date(day.year, 8, 31):
        flags.append("Son gün adli tatilde (20 Temmuz–31 Ağustos); işin niteliğine göre süre uzayabilir — "
                     "kontrol edin.")
    return flags


# --------------------------------------------------------------------------
# Reading one tebligat document
# --------------------------------------------------------------------------
def notice_kind(title):
    """"envelope", "result" (UETS mazbata), "scan" (paper mazbata) or None."""
    folded = tr_lower(title)
    if "mazbata" in folded:
        return "result" if re.search(r"e[-\s]?tebli", folded) else "scan"
    if "tebligat" in folded or "tebliğ" in folded or "tebellüğ" in folded:
        return "envelope"
    return None


def _d(value):
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


_ROLE = (r"(?:karşı\s+)?(?:davacı|davalı|sanık|şüpheli|müşteki|katılan|mağdur|şikayetçi|şikâyetçi|alacaklı"
         r"|borçlu|müdahil|ihbar\s+olunan|bilirkişi|tanık|suça\s+sürüklenen\s+çocuk)")
_ADDRESSEE = re.compile(
    rf"^(?P<role>{_ROLE})\s+(?P<party>[^():\n]+?)"
    r"(?:\s+(?P<ctype>vekili|müdafii|müdafi)\s+(?P<counsel>av\.?\s*[^():\n]+?))?\s*(?:\(.*)?$")
_HOLDER = re.compile(r"(?i)uets\s+hesap\s+sahibi\s*:\s*([^\n]+)")
_NAME = re.compile(r"[A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ.' -]*")


def _upper_name(value):
    value = re.sub(r"\s+", " ", str(value or "")).strip(" ,.-")
    words = value.split()
    if not 1 <= len(words) <= 7 or not _NAME.fullmatch(value):
        return None
    return tr_title(value)


def _counsel_name(value):
    value = re.sub(r"(?i)^av\.?\s*", "", re.sub(r"\s+", " ", str(value or "")).strip(" ,.-"))
    name = _upper_name(value)
    return f"Av. {name}" if name else None


def parse_addressee(lines):
    """{role, party, counselType, counsel} from the envelope's addressee line, or None."""
    for line in lines:
        raw = line.strip()
        low = tr_lower(raw)
        if len(low) != len(raw):
            continue
        match = _ADDRESSEE.match(low)
        if not match:
            continue
        party = _upper_name(raw[match.start("party"):match.end("party")])
        if not party:
            continue
        counsel = _counsel_name(raw[match.start("counsel"):match.end("counsel")]) if match.group("counsel") else None
        role = re.sub(r"\s+", " ", match.group("role"))
        return {"role": _tr_upper(role[:1]) + role[1:], "party": party,
                "counselType": ("müdafii" if match.group("ctype").startswith("müdafi") else "vekili")
                if counsel else None,
                "counsel": counsel}
    return None


def muhatap_text(person, holder=None):
    if person and person.get("counsel"):
        return f"{person['role']} {person['counselType']} {person['counsel']}"
    if person:
        return f"{person['role']} {person['party']}"
    return holder or "Muhatap okunamadı"


def muhatap_key(person, holder=None):
    if person:
        return tr_fold(f"{person['role']}|{person.get('counsel') or person['party']}")
    return tr_fold(holder or "")


_ZARF = re.compile(r"bu\s+zarfta\s+(.*?)\s+vardır")


def parse_contents(text):
    """What the envelope holds: [{"title", "day"}], attachments dropped."""
    flat = re.sub(r"\s+", " ", str(text or ""))
    low = tr_lower(flat)
    if len(low) != len(flat):
        return []
    match = _ZARF.search(low)
    if not match:
        return []
    body = re.sub(r"\[[^\]]*\]", "", flat[match.start(1):match.end(1)])
    items, seen = [], set()
    for piece in body.split(","):
        piece = piece.strip(" -")
        if not piece or "_" in piece or re.match(r"(?i)ek\s*-", piece):
            continue
        dated = re.search(r"(\d{2}/\d{2}/\d{4})\s*$", piece)
        title = re.sub(r"[\s-]*\d{2}/\d{2}/\d{4}\s*$", "", piece)
        title = re.sub(r"(?:\s*-\s*)+$", "", title).strip(" -")
        if not title or len(title) > 80:
            continue
        item = {"title": tr_title(clean_title(title)), "day": _d(dated.group(1)) if dated else None}
        key = (tr_fold(item["title"]), item["day"])
        if key not in seen:
            seen.add(key)
            items.append(item)
    return items


_EVENT = re.compile(r"(\d{2}/\d{2}/\d{4})(?:\s+(\d{2}:\d{2}))?\s+(.+?)"
                    r"(?=\s+\d{2}/\d{2}/\d{4}|\s+e-tebliğ\s+mazbatası|\s+karar\s+no|\s+yukarıda\s+belirtilen|\s*$)")


def parse_events(text):
    """UETS rows: [{"kind", "day", "time", "text"}]."""
    flat = re.sub(r"\s+", " ", str(text or ""))
    low = tr_lower(flat)
    if len(low) != len(flat):
        return []
    start = low.find("tarih açıklama")
    if start < 0:
        start = low.find("muhatap hesabına")
        start = max(0, low.rfind(" ", 0, max(0, start - 18))) if start >= 0 else -1
    if start < 0:
        return []
    events = []
    for match in _EVENT.finditer(low, start):
        description = match.group(3)
        if "okundu say" in description:
            kind = "deemed"
        elif "konuldu" in description:
            kind = "placed"
        elif "açıldı" in description:
            kind = "opened"
        elif "teslim al" in description:
            kind = "accepted"
        else:
            kind = "other"
        events.append({"kind": kind, "day": _d(match.group(1)), "time": match.group(2),
                       "text": _short(flat[match.start(3):match.end(3)], 120)})
    return events


_IHTAR = re.compile(r"ihtar\s+olunur")


def ihtar_limits(text):
    """Time limits an envelope's ihtar states: [{"amount", "unit", "kesin", "label", "basis"}]."""
    flat = re.sub(r"\s+", " ", str(text or ""))
    low = tr_lower(flat)
    if len(low) != len(flat):
        return []
    found, seen, previous = [], set(), 0
    for match in _IHTAR.finditer(low):
        start = max(previous, match.start() - 420)
        window, raw = low[start:match.end()], flat[start:match.end()]
        previous = match.end()
        for limit in time_limits(raw, None):
            if "ikinci cevap" in window:
                label = "İkinci cevap dilekçesi süresi"
            elif "cevaba cevap" in window:
                label = "Cevaba cevap süresi"
            elif "cevap" in window:
                label = "Cevap süresi"
            elif "istinaf" in window:
                label = "İstinaf süresi"
            elif "temyiz" in window:
                label = "Temyiz süresi"
            elif "itiraz" in window:
                label = "İtiraz süresi"
            elif "delil" in window:
                label = "Delil süresi"
            else:
                label = "İhtardaki süre"
            article = re.search(r"(\d{2,3}/\d)\.?\s*madde", window)
            basis = "tebligattaki ihtar"
            if article and ("h.m.k" in window or "hmk" in window or "6100" in window):
                basis += f" (HMK {article.group(1)})"
            key = (limit["amount"], limit["unit"], label)
            if key not in seen:
                seen.add(key)
                found.append({"amount": limit["amount"], "unit": limit["unit"], "kesin": limit["kesin"],
                              "label": label, "basis": basis})
    return found


def _hearing(text, sent):
    """("13.10.2026 10:35", "Ön İnceleme") for a davetiye, else None."""
    flat = re.sub(r"\s+", " ", str(text or ""))
    position = tr_lower(flat).find("duruşma günü")
    if position < 0:
        return None
    window = flat[max(0, position - 80):position + 120]
    for match in re.finditer(r"(\d{2}/\d{2}/\d{4})(?:\s*\(([^)]{2,30})\))?(?:\s+(\d{2}:\d{2}))?", window):
        day = _d(match.group(1))
        if day and (sent is None or day >= sent):
            return {"day": day, "time": match.group(3), "type": tr_title(match.group(2)) if match.group(2) else None}
    return None


def parse_notice(text, title, day):
    """One tebligat document as structured fields; no address, ID or free text survives."""
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    holder = _HOLDER.search(str(text or ""))
    kind = notice_kind(title)
    folded_title = tr_lower(title)
    events = parse_events(text) if kind == "result" else []
    return {
        "kind": kind,
        "open": "açık" in folded_title,
        "electronic": kind == "result" or bool(holder) or "e-tebligat" in tr_lower(text),
        "person": parse_addressee(lines),
        "holder": (_upper_name(holder.group(1)) or tr_title(_short(holder.group(1), 60))) if holder else None,
        "contents": parse_contents(text),
        "events": events,
        "limits": ihtar_limits(text),
        "hearing": _hearing(text, day),
        "day": day,
    }


# --------------------------------------------------------------------------
# Envelopes matched to UETS results
# --------------------------------------------------------------------------
def _content_key(contents):
    return tuple(sorted({tr_fold(item["title"]) for item in contents}))


def _served(events):
    """(served day, computed?) from the UETS rows."""
    deemed = sorted(e["day"] for e in events if e["kind"] == "deemed" and e["day"])
    if deemed:
        return deemed[0], False
    placed = sorted(e["day"] for e in events if e["kind"] == "placed" and e["day"])
    if placed:
        return placed[0] + timedelta(days=E_TEBLIGAT_DAYS), True
    return None, False


def _first_event_day(events):
    days = sorted(e["day"] for e in events if e["day"] and e["kind"] in ("accepted", "placed"))
    return days[0] if days else None


def _merge_limits(*groups):
    merged, seen = [], set()
    for group in groups:
        for limit in group or []:
            key = (limit["amount"], limit["unit"], limit["label"])
            if key not in seen:
                seen.add(key)
                merged.append(limit)
    return merged


def collect_notices(catalog, text_documents):
    """(notices, unreadable) for a case.

    A notice is one addressee's tebligat: {person, holder, muhatap, key, contents, sent, served, computed,
    electronic, open, events, limits, hearing, refs, state}; state is "served", "awaiting" (e-tebligat with no
    UETS result in the archive) or "paper" (PTT; its scanned mazbata has no text).
    """
    envelopes, results, unreadable = [], [], []
    for row in catalog:
        kind = notice_kind(row.get("title"))
        if not kind:
            continue
        day = _as_date(row.get("date"))
        source = text_documents.get(row["ref"])
        text = str((source or {}).get("text") or "")
        if kind == "scan" or not text.strip():
            unreadable.append({"ref": row["ref"], "kind": kind, "day": day, "title": clean_title(row.get("title"))})
            continue
        parsed = parse_notice(text, row.get("title"), day)
        parsed["ref"] = row["ref"]
        (results if kind == "result" else envelopes).append(parsed)

    def base(parsed, envelope=None):
        person = parsed["person"] or (envelope or {}).get("person")
        holder = parsed["holder"] or (envelope or {}).get("holder")
        return {"person": person, "holder": holder, "muhatap": muhatap_text(person, holder),
                "key": muhatap_key(person, holder), "electronic": parsed["electronic"], "open": parsed["open"]}

    def fits(envelope, key, content, reached):
        if muhatap_key(envelope["person"], envelope["holder"]) != key or not envelope["day"]:
            return False
        if content and _content_key(envelope["contents"]) != content:
            return False
        if not content and envelope["contents"] and not envelope["hearing"]:
            return False
        return not reached or envelope["day"] <= reached <= envelope["day"] + timedelta(days=MATCH_DAYS)

    notices, used = [], set()
    for result in sorted(results, key=lambda r: (r["day"] or Date.min, r["ref"])):
        reached = _first_event_day(result["events"])
        key = muhatap_key(result["person"], result["holder"])
        content = _content_key(result["contents"])
        candidates = [e for e in envelopes if fits(e, key, content, reached)]
        # Duplicate envelopes (same addressee, same papers) pair off one to one.
        match = next((e for e in candidates if e["ref"] not in used), candidates[0] if candidates else None)
        if match:
            used.add(match["ref"])
        served, computed = _served(result["events"])
        notices.append({
            **base(result, match),
            "contents": result["contents"] or (match or {}).get("contents") or [],
            "sent": (match or {}).get("day") or reached or result["day"],
            "served": served, "computed": computed, "events": result["events"],
            "limits": _merge_limits(result["limits"], (match or {}).get("limits")),
            "hearing": (match or {}).get("hearing") or result["hearing"],
            "refs": [result["ref"]] + ([match["ref"]] if match else []),
            "state": "served" if served else "awaiting",
        })
    matched = list(notices)
    for envelope in envelopes:
        if envelope["ref"] in used:
            continue
        key, content = muhatap_key(envelope["person"], envelope["holder"]), _content_key(envelope["contents"])
        if envelope["day"] and any(
                n["key"] == key and _content_key(n["contents"]) == content and n["sent"]
                and abs((n["sent"] - envelope["day"]).days) <= MATCH_DAYS for n in matched):
            continue  # a second filing of an envelope whose result is already in
        notices.append({
            **base(envelope), "contents": envelope["contents"], "sent": envelope["day"], "served": None,
            "computed": False, "events": [], "limits": envelope["limits"], "hearing": envelope["hearing"],
            "refs": [envelope["ref"]], "state": "awaiting" if envelope["electronic"] else "paper",
        })

    # UYAP often files the same mazbata twice; one row per addressee and envelope.
    merged = {}
    for notice in notices:
        identity = (notice["key"], _content_key(notice["contents"]), notice["served"] or notice["sent"], notice["state"])
        if identity in merged:
            kept = merged[identity]
            kept["refs"] = list(dict.fromkeys(kept["refs"] + notice["refs"]))
            kept["limits"] = _merge_limits(kept["limits"], notice["limits"])
        else:
            merged[identity] = notice
    return list(merged.values()), unreadable


# --------------------------------------------------------------------------
# Time limits that run from a tebliğ
# --------------------------------------------------------------------------
_VERDICT = re.compile(r"hüküm\s*:|gereği\s+düşünüldü|hüküm\s+fıkrası")
_REMEDY_LABELS = {"istinaf": "İstinaf süresi", "temyiz": "Temyiz süresi", "itiraz": "İtiraz süresi"}


def who_matches(who, person):
    """Does a decision's addressee ("Davalı vekili", "Taraflar", None) cover this tebligat's side?"""
    if who in (None, "Taraflar"):
        return True
    if not person:
        return False
    side = re.sub(r"\s+(?:vekili|müdafii)$", "", tr_fold(who))
    return side == tr_fold(person["role"])


def _entries(catalog):
    entries = []
    for row in catalog:
        kind, label, _ = classify_title(row.get("title"))
        entries.append({**row, "kind": kind, "label": label, "day": _as_date(row.get("date")),
                        "clean": clean_title(row.get("title"))})
    return entries


def _decision_entry(content, entries):
    """The catalog record an envelope item names ("Tensip Zaptı - 11/09/2026")."""
    if not content.get("day"):
        return None
    same_day = [e for e in entries if e["day"] == content["day"] and e["kind"] in RECORD_KINDS | {"decision"}]
    folded = tr_fold(content["title"])
    exact = [e for e in same_day if tr_fold(e["clean"]) == folded]
    if exact:
        return exact[0]
    kind = classify_title(content["title"])[0]
    return next((e for e in same_day if e["kind"] == kind), None)


def _verdict_part(text):
    low = tr_lower(text)
    if len(low) != len(text):
        return text[-2500:]
    positions = [match.start() for match in _VERDICT.finditer(low)]
    return text[positions[-1]:] if positions else text[-2500:]


def decision_limits(entry, text, person):
    """Limits a served decision gives this side: [{amount, unit, kesin, label, basis, basisRefs}]."""
    found = []
    if entry["kind"] in ("decision", "finality"):
        for limit in time_limits(_verdict_part(text), None):
            if limit["remedy"] in _REMEDY_LABELS:
                found.append({"amount": limit["amount"], "unit": limit["unit"], "kesin": limit["kesin"],
                              "label": _REMEDY_LABELS[limit["remedy"]],
                              "basis": f"{entry['clean']} ({_fmt(entry['day'])}) hüküm fıkrası",
                              "basisRefs": [entry["ref"]]})
        return found
    info = record_info(text, entry.get("title"))
    shorts = info["decisions"]
    for number, decision in enumerate(info.get("decisionsFull") or shorts, start=1):
        decision = _CLOSING.sub("", decision).strip()
        short = _CLOSING.sub("", shorts[number - 1]).strip() if number - 1 < len(shorts) else decision
        limits = time_limits(decision, None)
        if not limits:
            continue
        if classify_action(short) != "party_task" and not any(l["remedy"] for l in limits):
            continue
        who = addressee(decision, "party_task")
        remedy_only = all(l["remedy"] for l in limits)
        if not remedy_only and not who_matches(who, person):
            continue
        clause, _ = mask_identifiers(_short(tr_title(short), 110))
        for limit in limits:
            if not limit["remedy"] and not who_matches(who, person):
                continue
            found.append({"amount": limit["amount"], "unit": limit["unit"], "kesin": limit["kesin"],
                          "label": _REMEDY_LABELS.get(limit["remedy"]) or _limit_label(decision, entry["kind"]),
                          "basis": f"{entry['clean']} {_fmt(entry['day'])}, {number}. madde: {clause}",
                          "basisRefs": [entry["ref"]], "who": who})
    return found


_LIMIT_LABELS = (("delil", "Delil / belge süresi"), ("belge", "Delil / belge süresi"),
                 ("avans", "Avans / masraf süresi"), ("masraf", "Avans / masraf süresi"),
                 ("harç", "Harç süresi"), ("adres", "Adres bildirme süresi"),
                 ("rapor", "Rapora itiraz / beyan süresi"), ("cevap", "Cevap süresi"), ("beyan", "Beyan süresi"))
_KIND_LABELS = {"tensip": "Tensip zaptındaki süre", "interim": "Ara karardaki süre",
                "hearing": "Duruşma ara kararındaki süre", "minutes": "Tutanaktaki süre"}


def _limit_label(decision, kind):
    lowered = tr_lower(decision)
    return next((label for word, label in _LIMIT_LABELS if word in lowered), _KIND_LABELS.get(kind, "Süre"))


def decision_is_final(entry, text):
    """A served judgment that states it is final ("kesin olmak üzere") has no remedy period."""
    if entry["kind"] not in ("decision", "finality"):
        return False
    part = tr_lower(_verdict_part(text))
    return "kesin olmak üzere" in part and not re.search(r"(istinaf|temyiz)\s+(?:kanun\s+)?yolu\s+açık", part)


def notice_deadlines(notice, entries, text_documents):
    """Last days for one served notice, each counted from its tebliğ day.

    A served judgment that is final is noted on the notice ("notes") instead.
    """
    notice.setdefault("notes", [])
    if not notice["served"]:
        return []
    limits = [{**limit, "basisRefs": []} for limit in notice["limits"]]
    for content in notice["contents"]:
        entry = _decision_entry(content, entries)
        source = text_documents.get(entry["ref"]) if entry else None
        if source and source.get("text"):
            limits += decision_limits(entry, source["text"], notice["person"])
            if decision_is_final(entry, source["text"]):
                notice["notes"].append((f"{entry['clean']} ({_fmt(entry['day'])}) kesin; kanun yolu süresi yok.",
                                        entry["ref"]))
    deadlines, seen = [], set()
    for limit in limits:
        last = _add_duration(notice["served"], limit["amount"], limit["unit"])
        key = (limit["label"], last)
        if not last or key in seen:
            continue
        seen.add(key)
        deadlines.append({**limit, "last": last, "served": notice["served"], "computed": notice["computed"],
                          "muhatap": notice["muhatap"], "refs": notice["refs"]})
    return deadlines


def case_deadlines(catalog, text_documents):
    """Every tebliğ-based last day of a case (for Önemli Tarihler)."""
    entries = _entries(catalog)
    notices, _ = collect_notices(catalog, text_documents)
    return [deadline for notice in notices for deadline in notice_deadlines(notice, entries, text_documents)]


def served_for_record(notices, day, title, kind):
    """Served notices whose envelope holds the decision record of this day and title."""
    folded = tr_fold(clean_title(title))
    return [n for n in notices if n["served"] and any(
        c.get("day") == day and (tr_fold(c["title"]) == folded or classify_title(c["title"])[0] == kind)
        for c in n["contents"])]


# --------------------------------------------------------------------------
# The view
# --------------------------------------------------------------------------
def _line(text, refs=(), tone=MUTED):
    text, _ = mask_identifiers(text)
    refs = list(dict.fromkeys(refs))
    return {"text": text, "refs": refs[:MAX_REFS], "moreRefs": max(0, len(refs) - MAX_REFS), "tone": tone}


def _item(date_text, text, who=None, status=None, lines=(), refs=(), kind="notice"):
    text, _ = mask_identifiers(text)
    refs = list(dict.fromkeys(refs))
    return {"dateText": date_text, "text": text, "who": who, "status": status, "lines": list(lines),
            "refs": refs[:MAX_REFS], "moreRefs": max(0, len(refs) - MAX_REFS), "kind": kind}


def _duration(limit):
    return f"{limit['amount']} {limit['unit']}{' kesin' if limit.get('kesin') else ''}"


def contents_label(notice):
    titles = list(dict.fromkeys(item["title"] for item in notice["contents"]))
    if titles:
        return ", ".join(titles[:3]) + (f" +{len(titles) - 3}" if len(titles) > 3 else "")
    if notice["hearing"]:
        return "Duruşma davetiyesi"
    return "Açık tebligat" if notice["open"] else "Tebligat"


def _event_line(notice):
    parts = []
    for kind, label in (("placed", "hesaba konuldu"), ("opened", "açıldı")):
        event = next((e for e in notice["events"] if e["kind"] == kind and e["day"]), None)
        if event:
            parts.append(f"{label} {_fmt(event['day'])}{' ' + event['time'] if event.get('time') else ''}")
    if notice["served"]:
        parts.append(f"tebliğ {'sayıldı' if not notice['computed'] else 'hesaplandı (5. gün)'} {_fmt(notice['served'])}")
    others = [e["text"] for e in notice["events"] if e["kind"] == "other"]
    text = "E-tebligat: " + " · ".join(parts) if parts else "E-tebligat"
    if others:
        text += " · " + others[-1]
    return text


def _status_for(deadline, today):
    remaining = (deadline["last"] - today).days
    if remaining < 0:
        return {"label": "Süre doldu", "tone": MUTED}
    if remaining == 0:
        return {"label": "Bugün son gün", "tone": BAD}
    return {"label": f"{remaining} gün kaldı", "tone": WARN if remaining <= 7 else INFO}


def _deadline_item(deadline, today):
    lines = [_line(f"Tebliğ {_fmt(deadline['served'])}{' (hesaplandı)' if deadline['computed'] else ''}",
                   deadline["refs"], INFO),
             _line(f"Dayanak: {deadline['basis']}", deadline.get("basisRefs") or [], MUTED)]
    lines += [_line(flag, (), WARN) for flag in calendar_flags(deadline["last"])]
    return _item(_fmt(deadline["last"]), f"{deadline['label']}: {_duration(deadline)}", who=deadline["muhatap"],
                 status=_status_for(deadline, today), lines=lines, kind="deadline")


def _notice_item(notice, deadlines):
    lines = []
    if notice["state"] == "served":
        status = ({"label": "Tebliğ edildi", "tone": GOOD} if not notice["computed"]
                  else {"label": "Tebliğ (hesaplandı)", "tone": INFO})
        lines.append(_line(_event_line(notice), notice["refs"][:1], GOOD))
        date_text = _fmt(notice["served"])
    elif notice["state"] == "awaiting":
        status = {"label": "Mazbata yok", "tone": WARN}
        lines.append(_line(f"E-tebligat {_fmt(notice['sent'])} gönderildi; UETS mazbatası arşivde görünmüyor.",
                           notice["refs"], WARN))
        date_text = _fmt(notice["sent"])
    else:
        status = {"label": "Fiziki tebligat", "tone": MUTED}
        lines.append(_line(f"PTT ile {_fmt(notice['sent'])} gönderildi; tebliğ mazbatası okunamadı veya arşivde "
                           "yok, tebliğ günü bilinmiyor.", notice["refs"], MUTED))
        date_text = _fmt(notice["sent"])
    hearing = notice["hearing"]
    if hearing:
        when = _fmt(hearing["day"]) + (f" saat {hearing['time']}" if hearing.get("time") else "")
        lines.append(_line(f"Duruşma: {when}{' (' + hearing['type'] + ')' if hearing.get('type') else ''}", (), INFO))
    for deadline in deadlines:
        lines.append(_line(f"{deadline['label']} {_duration(deadline)} → son gün {_fmt(deadline['last'])}",
                           deadline.get("basisRefs") or [], WARN))
    for note, ref in notice.get("notes", []):
        lines.append(_line(note, [ref], INFO))
    if notice["state"] != "served":
        for limit in notice["limits"]:
            lines.append(_line(f"İhtar: {limit['label'].lower()} {_duration(limit)} — tebliğ günü bilinmeden son gün "
                               "hesaplanamadı.", (), MUTED))
    return _item(date_text, notice["muhatap"], status=status, lines=lines, refs=notice["refs"], kind="notice")


def build_tebligat(catalog, text_documents, today=None, catalog_available=True):
    today = _today(today)
    entries = _entries(catalog)
    notices, unreadable = collect_notices(catalog, text_documents)
    deadlines_by_notice = {id(n): notice_deadlines(n, entries, text_documents) for n in notices}

    deadlines = [d for n in notices for d in deadlines_by_notice[id(n)]]
    upcoming = sorted((d for d in deadlines if d["last"] >= today), key=lambda d: (d["last"], d["muhatap"]))
    recent = sorted((d for d in deadlines if today - timedelta(days=DEADLINE_PAST_DAYS) <= d["last"] < today),
                    key=lambda d: d["last"], reverse=True)
    top = {"key": "deadlines", "title": "Süresi işleyenler", "highlight": True, "refs": [],
           "items": [_deadline_item(d, today) for d in (upcoming + recent)[:MAX_DEADLINES]],
           "empty": "Tebliğden sonra işleyen süre bulunamadı."}

    groups = {}
    for notice in notices:
        days = [c["day"] for c in notice["contents"] if c.get("day")]
        day = min(days) if days else notice["sent"]
        groups.setdefault((day, contents_label(notice)), []).append(notice)
    ordered = sorted(groups.items(), key=lambda item: (item[0][0] or Date.min, item[0][1]), reverse=True)
    sections = [top]
    for index, ((day, label), members) in enumerate(ordered[:MAX_GROUPS]):
        members.sort(key=lambda n: (n["served"] or n["sent"] or Date.min, n["muhatap"]))
        sections.append({"key": f"group-{index}", "title": f"{_fmt(day)} · {label}" if day else label,
                         "highlight": False, "refs": [],
                         "items": [_notice_item(n, deadlines_by_notice[id(n)]) for n in members],
                         "empty": "Muhatap okunamadı."})

    coverage = []
    served_count = sum(1 for n in notices if n["state"] == "served")
    if notices:
        coverage.append(f"{len(notices)} tebligat okundu: {served_count} tebliğ edildi, "
                        f"{sum(1 for n in notices if n['state'] == 'awaiting')} e-tebligatın mazbatası yok, "
                        f"{sum(1 for n in notices if n['state'] == 'paper')} fiziki tebligat.")
    else:
        coverage.append("Metni okunabilen tebligat evrakı bulunamadı.")
    if len(ordered) > MAX_GROUPS:
        coverage.append(f"Daha eski {len(ordered) - MAX_GROUPS} tebligat grubu gösterilmedi.")
    scans = [u for u in unreadable if u["kind"] == "scan"]
    missing = [u for u in unreadable if u["kind"] != "scan"]
    if scans:
        coverage.append(f"{len(scans)} tebliğ mazbatası taranmış görüntü; fiziki tebligatların tebliğ günü "
                        "okunamadı (OCR gerekir).")
    if missing:
        coverage.append(f"{len(missing)} tebligat evrakının metni indekste yok; \"İndeksi güncelle\" ile "
                        "okunabilir.")
    coverage.append("Son günler tebliğden sayıldı: gün süreleri tebliği izleyen günden, hafta ve ay süreleri "
                    "tebliğ gününe karşılık gelen günde biter (HMK 92). Tatil uzatmaları otomatik uygulanmaz.")
    if not catalog_available:
        coverage.append("Evrak kataloğu yok; yalnız metni okunan evraklar kullanıldı. \"Tam dava indeksini "
                        "güncelle\" çalıştırın.")
    sections.append({"key": "coverage", "title": "Kapsam", "highlight": False, "refs": [],
                     "items": [_item("", line, kind="note") for line in coverage]})
    refs = list(dict.fromkeys(ref for s in sections for ref in s.get("refs", []) + [
        r for i in s["items"] for r in i["refs"] + [x for line in i.get("lines", []) for x in line["refs"]]]))
    return {"sections": sections, "refs": refs, "notices": notices}


def tebligat_answer(downloads_root, scope_id, today=None):
    from ai_court_orders import render_text

    scope_id = str(scope_id or "").strip()
    if not scope_id.startswith("case:"):
        return {"ok": False, "errorCode": "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED",
                "error": "Tebligatlar yalnız tam dava indeksi için hazırlanabilir."}
    try:
        rows, documents, _, catalog_available = load_case(downloads_root, scope_id)
    except (OSError, ValueError) as error:
        return {"ok": False, "errorCode": "AI_INDEX_UNAVAILABLE", "error": f"Dava indeksi okunamadı: {error}"}
    built = build_tebligat(rows, documents, today=today, catalog_available=catalog_available)
    titles = {row["ref"]: row for row in rows}
    return {
        "ok": True,
        "kind": "UYAP_AI_ANSWER",
        "schemaVersion": 1,
        "answer": render_text(built["sections"]),
        "workflowId": "tebligat",
        "workflowTitle": "Tebligatlar",
        "workflowNotice": NOTICE,
        "tebligatSchema": TEBLIGAT_SCHEMA,
        "tebligatSections": built["sections"],
        "retrievalMode": RETRIEVAL_MODE,
        "sourceRefs": built["refs"],
        "sources": [{"sourceRef": ref, "title": (titles.get(ref) or {}).get("title"), "documentType": None,
                     "documentDate": (titles.get(ref) or {}).get("date"), "selectionReason": "Kural tabanlı tebligat",
                     "summaryRole": "rule_based_notice"} for ref in built["refs"]],
        "provider": "Yerel kurallar",
        "model": "",
        "providerCalled": False,
        "modelAnswerAccepted": None,
        "rawDocumentsUploaded": False,
        "networkUsed": False,
        "store": False,
    }
