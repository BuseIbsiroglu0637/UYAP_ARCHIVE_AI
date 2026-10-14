"""Rule-based "Ara Kararlar ve Yapılacaklar" for one archive case. No model.

For the latest decision records (duruşma zaptı, ara karar, tensip) every
numbered decision is shown with who has to act, what is asked, the granted
time limit (earliest possible end only) and the documents that entered the
file after the decision and look related to it.

"Done" is never claimed. A later "Genel Müzekkere" or "Beyan Dilekçesi" is
shown as a related record, or the view says none is visible; the lawyer
judges whether the order was met.
"""
from __future__ import annotations

import re

from ai_case_facts import find_dates, mask_identifiers, tr_lower, tr_title
from ai_important_dates import (
    RECORD_KINDS, _add_duration, _as_date, _fmt, _short, _today, _tr_upper, classify_title, clean_title, load_case,
    record_info, time_limits,
)

ORDERS_SCHEMA = "court_orders_v1"
RETRIEVAL_MODE = "court_orders_rules_v1"
MAX_RECORDS = 3
OPEN_LOOKBACK_DAYS = 120
MAX_EVIDENCE_GROUPS = 2
MAX_REFS = 3

NOTICE = (
    "Ara kararlar kaynaktan kurallarla çıkarıldı; AI modeli kullanılmadı. \"İlgili evrak\", kararın ardından dosyaya "
    "giren ve başlığına göre ilişkili görünen kayıttır; işin yapıldığını göstermez. Kararın e-tebliğ mazbatası "
    "okunabildiğinde süre o tarafın tebliğinden sayılır; okunamadığında karar tarihinden sayılan en erken bitiş "
    "gösterilir. Tatil ve adli tatil uzatmalarını kontrol edin."
)

_ROLES = "davacı|davalı|sanık|müşteki|katılan|şüpheli|alacaklı|borçlu|müdahil|mağdur"
_ACTOR_COUNSEL = re.compile(rf"\b({_ROLES})\s+(?:asil\s+)?(vekil|müdafi)\w*")
_ACTOR_PARTY = re.compile(rf"\b({_ROLES})(?:lar|ler)?(?:a|e|ya|ye|ın|in|nın|nin|ların|lerin|lara|lere)\b")
_ACTOR_BOTH = re.compile(r"\btaraf(?:lar)?(?:a|ın|ların|lara)\b")
_ACTOR_OTHER = ((re.compile(r"\bbilirkişi\w*"), "Bilirkişi"), (re.compile(r"\btanık\w*"), "Tanık"))

# (action, pattern) in priority order; the earliest match in the decision
# text decides, because a Turkish decision states its main verb first
# ("…talebinin REDDİNE, kararın davacıya tebliğine").
_ACTIONS = (
    ("party_task", re.compile(r"bildirme(?:si|sine|leri|lerine)|sunma(?:sı|sına|ları|larına|k\s+üzere)|sunmak"
                              r"|ibraz|yatırma(?:sı|sına|ları)|beyanda\s+bulun|açıklamayı\s+yapmak"
                              r"|delil\w*\s+(?:listesi|bildir)|süre\s+verilmesine|kesin\s+süre")),
    ("request", re.compile(r"müzekkere\s+yazıl|yazı\s+yazıl|istenmesine|celbine|sorulmasına|getirtilmesine"
                           r"|araştırılmasına|talimat\s+yazıl")),
    ("expert", re.compile(r"bilirkişi")),
    ("site", re.compile(r"keşif")),
    ("hearing", re.compile(r"duruşma\w*\s+(?:\S+\s+){0,6}?(?:bırakıl|ertelen|yapılmas)")),
    ("notice", re.compile(r"tebliğine|davetiye|tebligat\s+çıkarıl|ihtarlı|ihtarına")),
    ("ruling", re.compile(r"kabulüne|reddine|reddedilmesine|birleştirilmesine|tefrikine|ayrılmasına"
                          r"|durdurulmasına|kaldırılmasına|karar\s+verilmesine")),
)
_OUTGOING = ("müzekkere", "yazı", "talimat", "tekit")
_INCOMING = ("cevap", "gelen", "dosyaya eklenecek", "kurum ve kuruluş")
_PAYMENT_WORDS = ("avans", "masraf", "harç", "yatır")
_PAYMENT_TITLES = ("makbuz", "dekont", "tahsilat", "harç", "avans")
_SUBJECT_WORDS = ("dosya", "talimat", "banka", "tapu", "nüfus", "adres", "sicil", "sgk", "vergi", "rapor",
                  "bilirkişi", "keşif", "tanık", "delil", "davetiye", "bakanlık", "emniyet", "araç")

GOOD, WARN, BAD, INFO, MUTED = "good", "warn", "bad", "info", "muted"


def _cap(word):
    return _tr_upper(word[:1]) + word[1:]


# "…müzekkerelere cevap verildiği, … tamamlandığı anlaşılmakla" records what
# the court found, it orders nothing.
_FINDING = re.compile(r"(?:dığı|diği|duğu|düğü|tığı|tiği|tuğu|tüğü|anlaşılmakla|anlaşılmış|görülmekle)[\s,.;…]*$")
# The record's closing sentence ("Dosya üzerinde yapılan inceleme sonunda
# karar verildi. 11/08/2026") is not part of the last decision. Long
# decisions arrive shortened ("…, Dosya üzerinde yapılan inceleme sonucu…").
_CLOSING = re.compile(
    r"(?i)(?:,\s*dosya\s+üzer[iİ]nde\s+yapılan\s+[iİ]nceleme\b[^,;]*"
    r"|[\s,]*(?:dosya\s+üzer[iİ]nde(?:\s+yapılan)?\s+[iİ]ncele(?:me|nerek)"
    r"(?:\s+(?:sonunda|sonucunda|sonucu|neticesinde))?[\s,]*)?"
    r"(?:(?:oy\s*b[iİ]rl[iİ]ğ[iİ](?:yle|\s+[iİ]le)|oy\s*çokluğu(?:yla|\s+[iİ]le))\s*)?"
    r"karar\s+ver[iİ]ld[iİ]\.?\s*[\d/]*\.?…?)\s*$")


def addressee(text, action):
    """Who has to act, as the record states it."""
    if action in ("request", "notice"):
        return "Mahkeme kalemi"
    if action in ("ruling", "finding", "other"):
        return None
    lowered = tr_lower(text)
    found = []
    for match in _ACTOR_COUNSEL.finditer(lowered):
        found.append((match.start(), f"{_cap(match.group(1))} {'vekili' if match.group(2) == 'vekil' else 'müdafii'}"))
    for match in _ACTOR_PARTY.finditer(lowered):
        found.append((match.start() + 0.5, _cap(match.group(1))))
    for match in _ACTOR_BOTH.finditer(lowered):
        found.append((match.start() + 0.5, "Taraflar"))
    for pattern, label in _ACTOR_OTHER:
        for match in pattern.finditer(lowered):
            found.append((match.start() + 0.5, label))
    if action == "hearing":
        return "Taraflar"
    return min(found)[1] if found else None


def classify_action(text):
    lowered = tr_lower(_CLOSING.sub("", str(text or "")))
    if _FINDING.search(lowered[-80:]) and not re.search(r"(?:ına|ine|una|üne|na|ne)[\s,.;]*$", lowered):
        return "finding"
    best = None
    for priority, (action, pattern) in enumerate(_ACTIONS):
        match = pattern.search(lowered)
        if match and (best is None or (match.start(), priority) < best[0]):
            best = ((match.start(), priority), action)
    return best[1] if best else "other"


def _group(documents):
    """Same-titled evidence collapses: "Genel Müzekkere (260) · 13.10.2026"."""
    groups = {}
    for document in documents:
        groups.setdefault(document["clean"], []).append(document)
    lines = []
    for title, members in sorted(groups.items(), key=lambda item: (item[1][0]["day"], item[0]))[:MAX_EVIDENCE_GROUPS]:
        days = sorted({m["day"] for m in members})
        when = _fmt(days[0]) if len(days) == 1 else f"{_fmt(days[0])} – {_fmt(days[-1])}"
        refs = [m["ref"] for m in members]
        lines.append({"text": f"{title}{f' ({len(members)})' if len(members) > 1 else ''} · {when}",
                      "refs": refs[:MAX_REFS], "moreRefs": max(0, len(refs) - MAX_REFS)})
    hidden = max(0, len(groups) - MAX_EVIDENCE_GROUPS)
    return lines, hidden


def _evidence(action, text, decided, catalog, own_ref, today, until=None):
    """(status, lines) for one decision item.

    Related records are looked for from the decision day up to the case's next
    decision record: later decisions produce their own letters and tebligat.
    """
    after = [d for d in catalog if d["day"] and decided and d["day"] >= decided and d["ref"] != own_ref
             and (until is None or d["day"] < until)]
    held_pool = [d for d in catalog if d["day"] and d["ref"] != own_ref]
    lowered = tr_lower(text)
    lines = []

    def add(prefix, documents, tone):
        # Prefer records whose title shares a subject word with the decision
        # ("…dosyasının istenmesine" → "Dosya İsteme Yazısı").
        preferred = [d for d in documents if any(w in lowered and w in d["folded"] for w in _SUBJECT_WORDS)]
        grouped, hidden = _group(preferred or documents)
        for line in grouped:
            lines.append({**line, "text": f"{prefix}: {line['text']}", "tone": tone})
        if hidden:
            lines.append({"text": f"+{hidden} başka ilgili evrak türü", "refs": [], "moreRefs": 0, "tone": tone})

    if action == "ruling":
        return {"label": "Karar", "tone": MUTED}, lines
    if action == "finding":
        return {"label": "Tespit", "tone": MUTED}, lines
    if action == "hearing":
        days = sorted(_as_date(d) for d in find_dates(text))
        day = next((d for d in days if d and d >= decided), None) if decided else None
        if not day:
            return {"label": "Duruşma", "tone": INFO}, lines
        if day >= today:
            remaining = (day - today).days
            return {"label": "bugün" if remaining == 0 else f"{remaining} gün sonra", "tone": INFO}, lines
        held = [d for d in held_pool if d["day"] == day and d["kind"] in ("hearing", "minutes")]
        if held:
            add("Duruşma kaydı", held, GOOD)
            return {"label": "Duruşma kaydı var", "tone": GOOD}, lines
        return {"label": "Zaptı arşivde yok", "tone": WARN}, lines
    if action == "request":
        # A "Talimat Duruşma Zaptı" is a record, not a letter.
        letters = [d for d in after if d["kind"] not in RECORD_KINDS]
        outgoing = [d for d in letters if any(w in d["folded"] for w in _OUTGOING)
                    and not any(w in d["folded"] for w in _INCOMING)]
        incoming = [d for d in letters if any(w in d["folded"] for w in _INCOMING)]
        add("Yazı gönderilmiş", outgoing, GOOD)
        add("Gelen evrak / cevap", incoming, GOOD)
        if incoming:
            return {"label": "Cevap gelmiş", "tone": GOOD}, lines
        if outgoing:
            return {"label": "Cevap bekleniyor", "tone": WARN}, lines
        return {"label": "Yazı görünmüyor", "tone": WARN}, lines
    if action == "expert":
        reports = [d for d in after if d["kind"] == "expert"]
        add("Bilirkişi evrakı", reports, GOOD)
        return ({"label": "Bilirkişi evrakı var", "tone": GOOD} if reports else
                {"label": "Rapor görünmüyor", "tone": WARN}), lines
    if action == "site":
        visits = [d for d in after if "keşif" in d["folded"]]
        add("Keşif kaydı", visits, GOOD)
        return ({"label": "Keşif kaydı var", "tone": GOOD} if visits else
                {"label": "Keşif kaydı görünmüyor", "tone": WARN}), lines
    if action == "notice":
        notices = [d for d in after if d["kind"] == "notice"]
        add("Tebligat kaydı", notices, GOOD)
        return ({"label": "Tebligat kaydı var", "tone": GOOD} if notices else
                {"label": "Tebligat görünmüyor", "tone": MUTED}), lines
    if action == "party_task":
        petitions = [d for d in after if d["kind"] in ("petition", "reply", "opening")]
        add("Dilekçe gelmiş", petitions, GOOD)
        if any(word in lowered for word in _PAYMENT_WORDS):
            receipts = [d for d in after if any(w in d["folded"] for w in _PAYMENT_TITLES)]
            add("Ödeme kaydı", receipts, GOOD)
            petitions = petitions + receipts
        if petitions:
            return {"label": "İlgili evrak var", "tone": GOOD}, lines
        limit = next(iter(time_limits(text, decided)), None) if decided else None
        if limit and limit["earliestEnd"]:
            if limit["earliestEnd"] >= today:
                return {"label": "Süre işliyor", "tone": WARN}, lines
            return {"label": "Kontrol edin", "tone": BAD}, lines
        return {"label": "Bekliyor", "tone": WARN}, lines
    return None, lines


def build_court_orders(catalog, text_documents, today=None, catalog_available=True, served=None):
    """`served`: the case's tebligat notices (ai_tebligat.collect_notices); a time limit is then also counted
    from the tebliğ day of each matching side."""
    from ai_tebligat import served_for_record, who_matches  # ai_tebligat imports this module

    today = _today(today)
    entries = []
    for document in catalog:
        kind, label, _ = classify_title(document.get("title"))
        day = _as_date(document.get("date"))
        entries.append({**document, "kind": kind, "label": label, "day": day,
                        "folded": tr_lower(document.get("title")), "clean": clean_title(document.get("title"))})

    records, unreadable = [], []
    for entry in entries:
        if entry["kind"] not in RECORD_KINDS | {"decision"}:
            continue
        source = text_documents.get(entry["ref"])
        if not source:
            if entry["day"]:
                unreadable.append(entry)
            continue
        info = record_info(source.get("text"), entry.get("title"))
        if not info["decisions"] and not info["nextHearing"]:
            continue
        day = info["recordDate"] or entry["day"]
        label = entry["label"]
        if entry["kind"] == "hearing" and info["isTalimat"]:
            label = "Talimat duruşması" + (f" ({info['court']})" if info.get("court") else "")
        elif entry["kind"] == "decision" and info["upperCourt"]:
            label = info["upperCourt"]
        if info.get("celse"):
            label += f" — {info['celse']}. celse"
        records.append({"ref": entry["ref"], "day": day, "label": label, "info": info, "kind": entry["kind"],
                        "title": entry.get("title")})
    newest_first = lambda r: (r["day"] or _as_date((1900, 1, 1)), r["ref"])
    records.sort(key=newest_first, reverse=True)
    # This court's own records first; a talimat court's record only fills up.
    own = [r for r in records if not r["info"]["isTalimat"]]
    shown = sorted((own + [r for r in records if r["info"]["isTalimat"]])[:MAX_RECORDS], key=newest_first, reverse=True)
    latest_read = shown[0]["day"] if shown else None
    own_days = sorted({r["day"] for r in own if r["day"]})

    sections, open_items = [], []
    for record in shown:
        info, decided = record["info"], record["day"]
        until = next((day for day in own_days if decided and day > decided), None)
        items = []
        decisions = [_CLOSING.sub("", d).strip() for d in info["decisions"]]
        # Long forms, item for item: limits are read from these.
        fulls = [_CLOSING.sub("", d).strip() for d in info.get("decisionsFull") or info["decisions"]]
        hearing = info.get("nextHearing")
        if hearing and not any(classify_action(d) == "hearing" for d in decisions):
            when = _fmt(_as_date(hearing["date"])) + (f" saat {hearing['time']}" if hearing.get("time") else "")
            decisions.append(f"{info.get('nextHearingType') or 'Duruşma'}: {when} (duruşmanın bu güne bırakılmasına)")
        record_served = served_for_record(served, decided, record["title"], record["kind"]) if served else []
        for number, decision in enumerate(decisions, start=1):
            full = fulls[number - 1] if number - 1 < len(fulls) else decision
            action = classify_action(decision)
            status, lines = _evidence(action, full, decided, entries, record["ref"], today, until)
            limit = next(iter(time_limits(full, decided)), None) if decided and action != "hearing" else None
            last_days = []
            if limit and limit["earliestEnd"]:
                what = {"istinaf": "İstinaf süresi", "temyiz": "Temyiz süresi", "itiraz": "İtiraz süresi"}.get(
                    limit["remedy"], "Süre")
                # Counted from each matching side's tebliğ when its mazbata is readable.
                side = addressee(full, "party_task")
                tebligat_lines = []
                for notice in record_served:
                    if not limit["remedy"] and not who_matches(side, notice["person"]):
                        continue
                    last = _add_duration(notice["served"], limit["amount"], limit["unit"])
                    if not last:
                        continue
                    last_days.append(last)
                    tebligat_lines.append({
                        "text": f"Tebliğ {_fmt(notice['served'])} — {notice['muhatap']} → son gün {_fmt(last)}",
                        "refs": notice["refs"][:MAX_REFS], "moreRefs": max(0, len(notice["refs"]) - MAX_REFS),
                        "tone": WARN if last >= today else MUTED})
                basis = (f" — en erken bitiş {_fmt(limit['earliestEnd'])}" if not last_days
                         else " — tebliğden sayıldı")
                lines[:0] = [{"text": f"{what}: {limit['amount']} {limit['unit']}{' kesin' if limit['kesin'] else ''}"
                                      f"{basis}", "refs": [], "moreRefs": 0,
                              "tone": WARN if max(last_days or [limit["earliestEnd"]]) >= today else MUTED}
                             ] + tebligat_lines
                if last_days and status and status["label"] in ("Süre işliyor", "Kontrol edin"):
                    status = ({"label": "Süre işliyor", "tone": WARN} if max(last_days) >= today
                              else {"label": "Kontrol edin", "tone": BAD})
            if action != "party_task":
                limit = None
            text, _ = mask_identifiers(_short(tr_title(decision), 240))
            who = addressee(decision, action)
            for line in lines:
                line["text"], _ = mask_identifiers(line["text"])
            items.append({"dateText": f"{number}.", "text": text, "who": who, "status": status, "lines": lines,
                          "refs": [], "moreRefs": 0, "kind": action})
            recent = decided and (today - decided).days <= OPEN_LOOKBACK_DAYS
            if (status and status["tone"] in (WARN, BAD) and action not in ("ruling", "hearing", "notice")
                    and not info["isTalimat"] and (recent or record is shown[0])):
                due = limit["earliestEnd"] if limit and limit["earliestEnd"] else None
                if last_days:
                    due = min((d for d in last_days if d >= today), default=max(last_days))
                summary, _ = mask_identifiers(_short(tr_title(decision), 130))
                open_items.append({"dateText": _fmt(due or decided), "text": f"{who or 'Mahkeme kalemi'} — {summary}",
                                   "who": None, "status": status, "lines": [], "refs": [record["ref"]], "moreRefs": 0,
                                   "kind": "open", "sort": (due or decided).isoformat() if (due or decided) else ""})
        sections.append({"key": f"record-{record['ref']}", "title": f"{_fmt(decided)} · {record['label']}",
                         "refs": [record["ref"]], "highlight": False, "items": items,
                         "empty": "Numaralı karar maddesi okunamadı."})

    warnings = []
    for entry in sorted(unreadable, key=lambda e: e["day"], reverse=True):
        if latest_read is None or entry["day"] > latest_read:
            warnings.append({"dateText": _fmt(entry["day"]), "text": f"{entry['clean']} — metni okunamadı (taranmış "
                             "olabilir); içindeki kararlar bu listede yok", "who": None,
                             "status": {"label": "Okunamadı", "tone": BAD}, "lines": [], "refs": [entry["ref"]],
                             "moreRefs": 0, "kind": "unreadable"})
    open_items.sort(key=lambda item: item.pop("sort"))
    top = {"key": "open", "title": "Açık işler", "highlight": True, "refs": [], "items": warnings[:3] + open_items,
           "empty": "Son kararlarda bekleyen iş görünmüyor."}

    coverage = []
    if records:
        coverage.append(f"Metni okunan {len(records)} karar kaydından en yeni {len(shown)} tanesi gösterildi.")
    else:
        coverage.append("Metni okunabilen ara karar / duruşma zaptı bulunamadı.")
    if unreadable:
        coverage.append(f"{len(unreadable)} karar / tutanak kaydının metni okunamadı (taranmış olabilir; OCR gerekir).")
    if not catalog_available:
        coverage.append("İlgili evrak yalnız metni okunan kayıtlar arasında arandı. Tüm evrakları kullanmak için "
                        "\"Tam dava indeksini güncelle\" çalıştırın.")
    sections = [top] + sections + [{
        "key": "coverage", "title": "Kapsam", "highlight": False, "refs": [],
        "items": [{"dateText": "", "text": line, "who": None, "status": None, "lines": [], "refs": [], "moreRefs": 0,
                   "kind": "note"} for line in coverage]}]
    return {"sections": sections, "answer": render_text(sections),
            "refs": list(dict.fromkeys(ref for s in sections for ref in s.get("refs", []) + [
                r for i in s["items"] for r in i["refs"] + [x for line in i.get("lines", []) for x in line["refs"]]]))}


def render_text(sections):
    blocks = []
    for section in sections:
        head = _tr_upper(section["title"]) + "".join(f" [{ref}]" for ref in section.get("refs", []))
        lines = []
        for item in section["items"]:
            status = f" ({item['status']['label']})" if item.get("status") else ""
            who = f"{item['who']} — " if item.get("who") else ""
            refs = "".join(f" [{ref}]" for ref in item["refs"]) + (f" +{item['moreRefs']}" if item.get("moreRefs") else "")
            prefix = f"{item['dateText']} " if item.get("dateText") else ""
            lines.append(f"- {prefix}{who}{item['text']}{status}{refs}")
            for line in item.get("lines", []):
                more = f" +{line['moreRefs']}" if line.get("moreRefs") else ""
                lines.append(f"    · {line['text']}" + "".join(f" [{ref}]" for ref in line["refs"]) + more)
        if not lines and section.get("empty"):
            lines.append(f"- {section['empty']}")
        blocks.append(head + "\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def court_orders_answer(downloads_root, scope_id, today=None):
    scope_id = str(scope_id or "").strip()
    if not scope_id.startswith("case:"):
        return {"ok": False, "errorCode": "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED",
                "error": "Ara kararlar yalnız tam dava indeksi için hazırlanabilir."}
    try:
        rows, documents, _, catalog_available = load_case(downloads_root, scope_id)
    except (OSError, ValueError) as error:
        return {"ok": False, "errorCode": "AI_INDEX_UNAVAILABLE", "error": f"Dava indeksi okunamadı: {error}"}
    from ai_tebligat import collect_notices

    try:
        served, _ = collect_notices(rows, documents)
    except Exception:  # the orders stand without tebliğ days
        served = []
    built = build_court_orders(rows, documents, today=today, catalog_available=catalog_available, served=served)
    titles = {row["ref"]: row for row in rows}
    return {
        "ok": True,
        "kind": "UYAP_AI_ANSWER",
        "schemaVersion": 1,
        "answer": built["answer"],
        "workflowId": "court_orders",
        "workflowTitle": "Ara Kararlar ve Yapılacaklar",
        "workflowNotice": NOTICE,
        "ordersSchema": ORDERS_SCHEMA,
        "orderSections": built["sections"],
        "retrievalMode": RETRIEVAL_MODE,
        "sourceRefs": built["refs"],
        "sources": [{"sourceRef": ref, "title": (titles.get(ref) or {}).get("title"), "documentType": None,
                     "documentDate": (titles.get(ref) or {}).get("date"), "selectionReason": "Kural tabanlı ara karar",
                     "summaryRole": "rule_based_order"} for ref in built["refs"]],
        "provider": "Yerel kurallar",
        "model": "",
        "providerCalled": False,
        "modelAnswerAccepted": None,
        "rawDocumentsUploaded": False,
        "networkUsed": False,
        "store": False,
    }
