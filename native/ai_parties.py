"""Rule-based "Taraflar" for one archive case. No model is involved.

Every UYAP record starts with a label block ("DAVACI : …", "VEKİLİ : Av. …",
"ESAS NO : 2026/115"). This view reads those blocks and answers:

- who the sides of THIS case are, with their counsel over time (current and
  earlier lawyers, with dates) and what else the record says about the side
  ("karşı davacı", "istinaf kanun yoluna başvuran");
- who sits on the bench and which experts / witnesses appear;
- where the same people appear in the other files whose documents were
  added to this case: one row per lawsuit or criminal case, and per person
  for enforcement files and investigations (often many).

A record is attributed to a file by the case number in its own header, so
the roles of an attached file never leak into this case's sides. A record
filed directly in this case whose header carries a mistyped number but the
same sides in the same roles still counts as this case.
Addresses, ID, phone and UETS numbers are never shown.
"""
from __future__ import annotations

import re
from collections import Counter

from ai_case_facts import (
    _HEADER_STOP, _LABEL_LINE, _label_key, is_attachment_title, mask_identifiers, name_from_value, parse_date,
    parse_header, tr_fold, tr_lower, tr_title,
)
from ai_important_dates import RECORD_KINDS, _as_date, _fmt, _tr_upper, classify_title, load_case_full

PARTIES_SCHEMA = "parties_v1"
RETRIEVAL_MODE = "parties_rules_v1"
MAX_REFS = 3
MAX_OTHER_ITEMS = 14
MAX_NUMBERS_SHOWN = 4
MAX_FILE_PARTIES = 5

NOTICE = (
    "Taraflar evrak künyelerinden kurallarla çıkarıldı; AI modeli kullanılmadı. Diğer dosyalardaki eşleşmeler ad-soyad "
    "aynılığına dayanır. Adres, kimlik, telefon ve UETS bilgileri gösterilmez."
)

ROLE_ORDER = ("Davacı", "Karşı davalı", "Davalı", "Karşı davacı", "Müşteki", "Şikayetçi", "Katılan", "Mağdur",
              "Sanık", "Şüpheli", "Suça sürüklenen çocuk", "Alacaklı", "Borçlu", "Tedbir isteyen", "Karşı taraf",
              "İhbar olunan", "Fer'i müdahil", "Müdahil", "Taraf")
# Which side a role stands on; a person on both sides of one file is flagged.
SIDE = {"Davacı": "A", "Karşı davalı": "A", "Müşteki": "A", "Şikayetçi": "A", "Katılan": "A", "Mağdur": "A",
        "Alacaklı": "A", "Davalı": "B", "Karşı davacı": "B", "Sanık": "B", "Şüpheli": "B",
        "Suça sürüklenen çocuk": "B", "Borçlu": "B"}
KIND_LABELS = {"dava": "Dava", "ceza": "Ceza davası", "icra": "İcra takibi", "soruşturma": "Soruşturma"}
GOOD, WARN, INFO, MUTED = "good", "warn", "info", "muted"

_CASE_NO = re.compile(r"(\d{4})\s*/\s*(\d{1,7})")
_CASE_LABELS = ("ESAS NO", "DOSYA NO", "ESAS", "SAYI", "DOSYA", "ESAS KARAR NO")
_INLINE_NO = re.compile(r"(?i)\b(?:esas|dosya)\s+no\s*:\s*(\d{4}\s*/\s*\d{1,7})")
_INLINE_INVESTIGATION = re.compile(r"(?i)\bsoruşturma\s+no\s*:\s*(\d{4}\s*/\s*\d{1,7})")
_REVIEWED = re.compile(r"(?s)İNCELENEN\s+KARARIN.{0,240}?NUMARASI\s*:\s*(\d{4}\s*/\s*\d{1,7})")
_OFFICE_WORDS = re.compile(r"(İCRA DAİRESİ|İCRA MÜDÜRLÜĞÜ|CUMHURİYET BAŞSAVCILIĞI|BAŞSAVCILIĞI|MAHKEMESİ)")
_CHAMBER = re.compile(r"^\s*(\d{1,2}\.\s*(?:HUKUK|CEZA)\s+DAİRESİ)\b")
_MONTH_WORDS = {"OCAK", "ŞUBAT", "MART", "NİSAN", "MAYIS", "HAZİRAN", "TEMMUZ", "AĞUSTOS", "EYLÜL", "EKİM", "KASIM",
                "ARALIK"}
_HEADER_DATE = re.compile(
    r"(?im)^\s*(?:ARA\s+KARAR|KARAR|DURU[ŞS]MA|TENS[İI]P|CELSE|TALEP|D[İI]LEK[ÇC]E)\s+TAR[İI]H[İI]\s*:\s*"
    r"(\d{1,2}[./]\d{1,2}[./]\d{4})|(\d{1,2}[./]\d{1,2}[./]\d{4})\s+TAR[İI]H[İI]NDE\s+UYAP")
_COMPANY = ((re.compile(r"\bAnonim Şirketi\b"), "A.Ş."), (re.compile(r"\bLimited Şirketi\b"), "Ltd. Şti."))


def _case_no(value):
    match = _CASE_NO.search(str(value or ""))
    return f"{match.group(1)}/{int(match.group(2))}" if match else None


def display_name(name):
    text = str(name or "")
    for pattern, short in _COMPANY:
        text = pattern.sub(short, text)
    return text


def _cap(text):
    text = str(text or "").strip()
    return _tr_upper(text[:1]) + text[1:]


def _clean_office_words(text):
    """Drop UYAP barcodes, document codes, "T.C." and dates from a letterhead line."""
    words = []
    for word in text.split():
        bare = word.strip(".,;:()")
        if not bare or bare in ("T.C", "TC") or bare in _MONTH_WORDS:
            continue
        if any(ch.isdigit() for ch in bare) and not re.fullmatch(r"\d{1,2}\.", word):
            continue
        words.append(word)
    return re.sub(r"(\d)\.(?=\S)", r"\1. ", " ".join(words))


def office_from_header(lines):
    """Court / office named at the top of a record: "Kastamonu 2. Aile Mahkemesi", "Ankara Bölge Adliye
    Mahkemesi 12. Hukuk Dairesi", "Kastamonu Cumhuriyet Başsavcılığı"."""
    top = [re.sub(r"\b\d{1,2}[./]\d{1,2}[./]\d{4}\b", " ", line.strip()) for line in lines[:14]]
    for index, line in enumerate(top):
        upper = _tr_upper(line)
        match = _OFFICE_WORDS.search(upper)
        if not match:
            continue
        head = _clean_office_words(upper[:match.end()])
        # "KASTAMONU" / "2. AİLE MAHKEMESİ" or "ANKARA" / "BÖLGE ADLİYE MAHKEMESİ" on separate lines.
        if len(head.split()) <= 3 and index:
            previous = _clean_office_words(_tr_upper(top[index - 1]))
            if previous and len(previous) <= 30 and len(previous.split()) <= 2 and ":" not in previous:
                head = previous + " " + head
        if "BÖLGE ADLİYE" in head and index + 1 < len(top):
            chamber = _CHAMBER.match(_tr_upper(top[index + 1]))
            if chamber:
                head += " " + chamber.group(1)
        return tr_title(head.strip()) or None
    return None


def header_meta(text, parties):
    """Which file a record belongs to, by its own header, plus bench / expert / witness names."""
    lines = str(text or "").splitlines()[:45]
    labels, witnesses, experts = {}, [], []
    for line in lines:
        if _HEADER_STOP.search(line) and labels:
            break
        match = _LABEL_LINE.match(line)
        if not match:
            continue
        key, value = _label_key(match.group(1)), match.group(3)
        labels.setdefault(key, value)
        if key in ("TANIK", "TANIKLAR"):
            name = name_from_value(value)
            if name:
                witnesses.append(name)
        elif key in ("BİLİRKİŞİ", "BİLİRKİŞİLER"):
            name = name_from_value(value)
            if name:
                experts.append(name)
    head = "\n".join(lines)
    reviewed = _REVIEWED.search(head)
    investigation = _case_no(labels.get("SORUŞTURMA NO") or labels.get("SORUŞTURMA"))
    if investigation is None:
        inline = _INLINE_INVESTIGATION.search(head)
        investigation = _case_no(inline.group(1)) if inline else None
    number = next((_case_no(labels[key]) for key in _CASE_LABELS if _case_no(labels.get(key))), None)
    if number is None:
        inline = _INLINE_NO.search(head)
        number = _case_no(inline.group(1)) if inline else None
    office = office_from_header(lines)
    roles = {party["role"] for party in parties}
    upper_office = _tr_upper(office or "")
    if "Şüpheli" in roles or investigation or "BAŞSAVCILIĞI" in upper_office:
        kind = "soruşturma"
    elif roles & {"Alacaklı", "Borçlu"} or "İCRA DAİRESİ" in upper_office or "İCRA MÜDÜRLÜĞÜ" in upper_office:
        kind = "icra"
    elif roles & {"Sanık", "Katılan", "Suça sürüklenen çocuk"} or ("Müşteki" in roles and not roles & {"Davacı", "Davalı"}):
        kind = "ceza"
    else:
        kind = "dava"
    if number is None and investigation is None and kind in ("icra", "soruşturma"):
        # Enforcement letters often write the file number without a label: "2025/39 TLMT.".
        number = next((_case_no(line) for line in lines[:14] if _case_no(line) and ":" not in line), None)
    date = _HEADER_DATE.search(head)
    return {
        "number": _case_no(reviewed.group(1)) if reviewed else (investigation if kind == "soruşturma" and investigation
                                                                else number or investigation),
        "reviewed": bool(reviewed),
        "talimat": bool(labels.get("TALİMAT NO")),
        "office": office,
        "kind": kind,
        "courtRecord": bool(labels.get("HAKİM")),
        "judge": name_from_value(labels.get("HAKİM")) if labels.get("HAKİM") else None,
        "witnesses": witnesses,
        "experts": experts,
        "headerDate": parse_date(date.group(1) or date.group(2)) if date else None,
    }


def _refs(refs, limit=MAX_REFS):
    unique = list(dict.fromkeys(refs))
    return unique[-limit:], max(0, len(unique) - limit)


def _line(text, refs=(), tone=MUTED):
    shown, more = _refs(refs)
    masked, _ = mask_identifiers(text)
    return {"text": masked, "refs": shown, "moreRefs": more, "tone": tone}


def _role_rank(role):
    return ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER)


def _roles_text(roles):
    ordered = [role for role, _ in sorted(roles.items(), key=lambda item: (-item[1], _role_rank(item[0])))]
    return " / ".join([ordered[0]] + [tr_lower(role) for role in ordered[1:]])


def _primary(person):
    return max(person["roles"], key=lambda role: (person["roles"][role], -_role_rank(role)))


def _add_counsel(person, counsel_list, entry):
    for counsel in counsel_list:
        slot = person["counsel"].setdefault(tr_fold(counsel["name"]), {
            "name": counsel["name"], "label": counsel["label"], "last": None, "refs": []})
        slot["refs"].append(entry["ref"])
        if entry["day"] and (slot["last"] is None or entry["day"] > slot["last"]):
            slot["last"] = entry["day"]


def _aggregate_sides(entries):
    """People of this case: name, weighted roles, counsel over time, qualifiers."""
    people, unnamed = {}, []
    for entry in entries:
        for party in entry["parties"]:
            if not party["name"]:
                if party["counsel"]:
                    unnamed.append((party, entry))
                continue
            key = tr_fold(party["name"])
            person = people.setdefault(key, {"name": party["name"], "roles": Counter(), "roleRefs": {}, "refs": [],
                                             "counsel": {}, "notes": {}})
            person["roles"][party["role"]] += entry["weight"]
            person["roleRefs"].setdefault(party["role"], []).append(entry["ref"])
            person["refs"].append(entry["ref"])
            if party.get("note"):
                person["notes"].setdefault(party["note"], []).append(entry["ref"])
            _add_counsel(person, party["counsel"], entry)
    for party, entry in unnamed:
        holders = [p for p in people.values() if _primary(p) == party["role"]]
        if len(holders) == 1:
            _add_counsel(holders[0], party["counsel"], entry)
    return people, unnamed


def _counsel_lines(person):
    lines = []
    counsel = sorted(person["counsel"].values(), key=lambda c: (c["last"] or _as_date((1900, 1, 1)), c["name"]),
                     reverse=True)
    if not counsel:
        return lines
    newest = counsel[0]["last"]
    current = [c for c in counsel if c["last"] == newest]
    defence = current[0]["label"] == "müdafii"
    title = ("Müdafii" if defence else "Vekili") if len(current) == 1 else ("Müdafileri" if defence else "Vekilleri")
    when = f" · son kayıt {_fmt(newest)}" if newest else ""
    lines.append(_line(f"{title}: {', '.join(c['name'] for c in current)}{when}",
                       [r for c in current for r in c["refs"]], GOOD))
    for earlier in (c for c in counsel if c not in current):
        when = f", son {_fmt(earlier['last'])}" if earlier["last"] else ""
        lines.append(_line(f"Daha önce: {earlier['name']} ({earlier['label']}{when})", earlier["refs"]))
    return lines


def _number_text(number, kind):
    if kind in ("dava", "ceza"):
        return f"{number} Esas"
    if kind == "soruşturma":
        return f"{number} soruşturma"
    return number


def read_entries(catalog, text_documents, own_no):
    entries, unreadable = [], 0
    for document in sorted(catalog, key=lambda d: (str(d.get("date") or ""), d["ref"])):
        source = text_documents.get(document["ref"])
        if not source:
            unreadable += 1
            continue
        parties, subject, _ = parse_header(source.get("text"))
        meta = header_meta(source.get("text"), parties)
        kind, _, _ = classify_title(document.get("title"))
        attachment = is_attachment_title(document.get("title"))
        # An attachment's UYAP date is when it was added here; the record's own date is in its header.
        day = _as_date(meta["headerDate"]) if attachment else (
            _as_date(document.get("date")) or _as_date(meta["headerDate"]))
        if meta["number"] and own_no and meta["number"] == own_no:
            scope = "own"
        elif meta["talimat"] and meta["kind"] != "icra" and not attachment:
            scope = "talimat"
        elif meta["number"]:
            scope = "other"
        else:
            scope = "unknown" if attachment else "own"
        entries.append({"ref": document["ref"], "title": document.get("title"), "day": day, "attachment": attachment,
                        "parties": parties, "subject": subject, "meta": meta, "scope": scope,
                        "weight": 3 if meta["courtRecord"] or kind in RECORD_KINDS | {"decision"} else 1})
    # A record filed in this case with a mistyped number keeps the same sides.
    first, _ = _aggregate_sides([e for e in entries if e["scope"] in ("own", "talimat")])
    sides = {key: SIDE.get(_primary(person)) for key, person in first.items()}
    own_sequence = own_no.split("/")[1] if own_no else None
    for entry in entries:
        if entry["scope"] != "other" or entry["meta"]["kind"] not in ("dava", "ceza"):
            continue
        # Attachments come from other files; only a year typo ("2025/115" for 2026/115) is forgiven there.
        if entry["attachment"] and entry["meta"]["number"].split("/")[1] != own_sequence:
            continue
        named = [(tr_fold(p["name"]), p["role"]) for p in entry["parties"] if p["name"]]
        if named and all(sides.get(key) and SIDE.get(role) == sides[key] for key, role in named):
            entry["scope"] = "own"
    return entries, unreadable


def _own_items(people, unnamed, person_order):
    items = []
    for key in person_order:
        person = people[key]
        role = _primary(person)
        lines = _counsel_lines(person)
        also = [other for other in person["roles"] if other != role]
        conflict = [other for other in also if SIDE.get(other) and SIDE.get(role) and SIDE[other] != SIDE[role]]
        extras = {}
        for other in also:
            if other not in conflict:
                extras.setdefault(tr_lower(other), []).extend(person["roleRefs"][other])
        for note, refs in person["notes"].items():
            extras.setdefault(note, []).extend(refs)
        for note, refs in sorted(extras.items(), key=lambda item: -len(set(item[1]))):
            lines.append(_line(_cap(note), refs, INFO))
        for other in conflict:
            lines.append(_line(f"Bazı evraklarda “{other}” olarak geçiyor", person["roleRefs"][other], WARN))
        status = None if person["counsel"] else {"label": "Vekil görünmüyor", "tone": MUTED}
        items.append({"dateText": role, "text": person["name"], "who": None, "status": status, "lines": lines,
                      "refs": list(dict.fromkeys([person["refs"][0], person["refs"][-1]])), "moreRefs": 0,
                      "kind": "party"})
    for party, entry in unnamed:
        if not any(_primary(p) == party["role"] for p in people.values()):
            items.append({"dateText": party["role"], "text": "Adı künyeden okunamadı", "who": None, "status": None,
                          "lines": [_line("Vekili: " + ", ".join(c["name"] for c in party["counsel"]),
                                          [entry["ref"]], GOOD)],
                          "refs": [entry["ref"]], "moreRefs": 0, "kind": "party"})
    return items


def _bench_items(entries, own_entries):
    items, judges = [], {}
    # The talimat court's judge is not this court's judge.
    for entry in (e for e in entries if e["scope"] == "own" and e["meta"]["judge"]):
        judge = judges.setdefault(tr_fold(entry["meta"]["judge"]),
                                  {"name": entry["meta"]["judge"], "last": entry["day"], "refs": []})
        judge["refs"].append(entry["ref"])
        if entry["day"] and (judge["last"] is None or entry["day"] > judge["last"]):
            judge["last"] = entry["day"]
    if judges:
        ranked = sorted(judges.values(), key=lambda j: j["last"] or _as_date((1900, 1, 1)), reverse=True)
        current = ranked[0]
        lines = [_line(f"Önceki: {j['name']}{' (son ' + _fmt(j['last']) + ')' if j['last'] else ''}", j["refs"])
                 for j in ranked[1:]]
        shown, more = _refs(current["refs"], 2)
        items.append({"dateText": "Hakim", "text": current["name"] + (
            f" · son kayıt {_fmt(current['last'])}" if current["last"] else ""), "who": None, "status": None,
            "lines": lines, "refs": shown, "moreRefs": more, "kind": "person"})
    for label, field in (("Bilirkişi", "experts"), ("Tanık", "witnesses")):
        found = {}
        for entry in own_entries:
            for name in entry["meta"][field]:
                found.setdefault(tr_fold(name), {"name": name, "refs": []})["refs"].append(entry["ref"])
        if found:
            names = list(found.values())
            shown, more = _refs([r for n in names for r in n["refs"]])
            items.append({"dateText": label, "text": ", ".join(n["name"] for n in names[:6]) + (
                f" (+{len(names) - 6})" if len(names) > 6 else ""), "who": None, "status": None, "lines": [],
                "refs": shown, "moreRefs": more, "kind": "person"})
    return items


def _other_items(entries, people, person_order):
    own_roles = {key: _primary(person) for key, person in people.items()}
    files, groups = {}, {}
    for entry in (e for e in entries if e["scope"] == "other"):
        named = {tr_fold(p["name"]): p for p in entry["parties"] if p["name"]}
        mine = own_roles.keys() & named.keys()
        if not mine:
            continue
        kind, number = entry["meta"]["kind"], entry["meta"]["number"]
        if kind in ("dava", "ceza"):
            # One row per lawsuit / criminal case, all its sides together.
            file = files.setdefault((kind, number), {"office": None, "people": {}, "refs": [], "subjects": Counter()})
            file["office"] = file["office"] or entry["meta"]["office"]
            file["refs"].append(entry["ref"])
            if entry["subject"] and kind == "ceza":
                file["subjects"][entry["subject"]["text"]] += 1
            for key, party in named.items():
                slot = file["people"].setdefault(key, {"name": display_name(party["name"]), "roles": Counter(),
                                                       "counsel": Counter()})
                slot["roles"][party["role"]] += 1
                for counsel in party["counsel"]:
                    slot["counsel"][counsel["name"]] += 1
            continue
        # Enforcement files and investigations: per person and role, often many files.
        for key in mine:
            party = named[key]
            group = groups.setdefault((key, party["role"], kind), {"numbers": {}, "counterparts": {}, "refs": [],
                                                                   "subjects": Counter(), "counsel": Counter()})
            slot = group["numbers"].setdefault(number, {"office": entry["meta"]["office"], "refs": []})
            slot["refs"].append(entry["ref"])
            group["refs"].append(entry["ref"])
            for other in entry["parties"]:
                if other is party or not other["name"]:
                    continue
                group["counterparts"].setdefault(other["role"], Counter())[display_name(other["name"])] += 1
            if entry["subject"] and kind == "soruşturma":
                group["subjects"][entry["subject"]["text"]] += 1
            for counsel in party["counsel"]:
                group["counsel"][counsel["name"]] += 1

    items = []
    for (kind, number), file in sorted(files.items(), key=lambda item: -len(item[1]["refs"])):
        lines = []
        ranked = sorted(file["people"].items(), key=lambda item: (
            item[0] not in own_roles, _role_rank(max(item[1]["roles"], key=item[1]["roles"].get))))
        for key, slot in ranked[:MAX_FILE_PARTIES]:
            text = f"{_roles_text(slot['roles'])}: {slot['name']}"
            if slot["counsel"]:
                names = [n for n, _ in slot["counsel"].most_common(3)]
                text += f" — {'vekilleri' if len(names) > 1 else 'vekili'} " + ", ".join(names)
            own_role = own_roles.get(key)
            if own_role and own_role not in slot["roles"]:
                text += f" (bu dosyada {tr_lower(own_role)})"
            lines.append(_line(text, tone=INFO if key in own_roles else MUTED))
        if len(ranked) > MAX_FILE_PARTIES:
            lines.append(_line(f"+{len(ranked) - MAX_FILE_PARTIES} kişi / kurum daha"))
        if file["subjects"]:
            lines.append(_line("Suç: " + ", ".join(s for s, _ in file["subjects"].most_common(3)), tone=INFO))
        title = (f"{number} Esas" if number else "Esas numarası okunamadı") + (
            f" · {file['office']}" if file["office"] else "")
        shown, more = _refs(file["refs"])
        items.append({"dateText": "Ceza" if kind == "ceza" else "Dava", "text": title, "who": None,
                      "status": {"label": f"{len(file['refs'])} evrak", "tone": MUTED}, "lines": lines,
                      "refs": shown, "moreRefs": more, "kind": "file"})
    order = {key: index for index, key in enumerate(person_order)}
    for (key, role, kind), group in sorted(groups.items(), key=lambda item: (
            order.get(item[0][0], 99), -len(item[1]["numbers"]), _role_rank(item[0][1]))):
        numbers = [n for n in group["numbers"] if n]
        count = len(group["numbers"])
        if count == 1 and numbers:
            office = group["numbers"][numbers[0]]["office"]
            text = f"{KIND_LABELS[kind]} · {_number_text(numbers[0], kind)}" + (f" · {office}" if office else "")
        else:
            text = f"{KIND_LABELS[kind]} · {count} dosya"
        lines = []
        if count > 1 and numbers:
            # "Kastamonu Cumhuriyet Başsavcılığı: 2025/8433, 2026/2407 · Küre Cumhuriyet Başsavcılığı: 2025/510"
            by_office = {}
            for number in numbers:
                by_office.setdefault(group["numbers"][number]["office"] or "", []).append(number)
            parts, shown_numbers = [], []
            for office, members in sorted(by_office.items(), key=lambda item: (-len(item[1]), item[0] == "")):
                room = MAX_NUMBERS_SHOWN + 2 - len(shown_numbers)
                if room <= 0:
                    break
                shown_numbers += members[:room]
                parts.append((f"{office}: " if office else "") + ", ".join(members[:room]))
            extra = len(numbers) - len(shown_numbers)
            lines.append(_line("Dosyalar — " + " · ".join(parts) + (f" ve {extra} dosya daha" if extra > 0 else ""),
                               [group["numbers"][n]["refs"][0] for n in shown_numbers]))
        for other_role, names in sorted(group["counterparts"].items(), key=lambda item: _role_rank(item[0])):
            listed = [f"{name} ({n})" if n > 1 and count > 1 else name for name, n in names.most_common(3)]
            extra = len(names) - len(listed)
            lines.append(_line(f"{other_role}: " + ", ".join(listed) + (f" +{extra}" if extra > 0 else "")))
        if group["subjects"]:
            lines.append(_line("Suç: " + ", ".join(s for s, _ in group["subjects"].most_common(3)), tone=INFO))
        if group["counsel"]:
            lines.append(_line(("Müdafii" if role in ("Sanık", "Şüpheli") else "Vekili") + ": "
                               + ", ".join(n for n, _ in group["counsel"].most_common(3))))
        shown, more = _refs(group["refs"])
        items.append({"dateText": role, "who": people[key]["name"], "text": text, "status": None,
                      "lines": lines, "refs": shown, "moreRefs": more, "kind": "other"})
    return items


def build_parties(catalog, text_documents, case_no=None, court=None, catalog_available=True):
    own_no = _case_no(case_no)
    entries, unreadable = read_entries(catalog, text_documents, own_no)
    own_entries = [e for e in entries if e["scope"] in ("own", "talimat")]
    people, unnamed = _aggregate_sides(own_entries)
    person_order = sorted(people, key=lambda key: (_role_rank(_primary(people[key])), people[key]["name"]))

    own_items = _own_items(people, unnamed, person_order)
    bench_items = _bench_items(entries, own_entries)
    other_items = _other_items(entries, people, person_order)
    hidden_other = max(0, len(other_items) - MAX_OTHER_ITEMS)
    other_items = other_items[:MAX_OTHER_ITEMS]

    own_count = sum(1 for e in own_entries if e["parties"])
    other_count = sum(1 for e in entries if e["scope"] == "other" and e["parties"])
    unknown = sum(1 for e in entries if e["scope"] == "unknown" and e["parties"])
    first_line = f"{own_count + other_count} evrakın künyesinden taraf okundu"
    first_line += (f": {own_count} bu dosyaya, {other_count} dosyaya eklenen başka dosya / kayıtlara ait."
                   if other_count else ".")
    coverage = [first_line]
    if unknown:
        coverage.append(f"{unknown} ek evrakın hangi dosyaya ait olduğu künyesinden anlaşılamadı; taraf listesine "
                        "katılmadı.")
    if unreadable:
        coverage.append(f"{unreadable} evrakın metni okunamadığı için (taranmış olabilir) künyesi taranamadı.")
    if hidden_other:
        coverage.append(f"Diğer dosyalarda {hidden_other} satır daha var.")
    if not catalog_available:
        coverage.append("Yalnız metni okunan evraklar tarandı; tüm evrak listesi için \"Tam dava indeksini güncelle\" "
                        "çalıştırın.")
    coverage_items = [{"dateText": "", "text": text, "who": None, "status": None, "lines": [], "refs": [],
                       "moreRefs": 0, "kind": "note"} for text in coverage]
    vekaletname = [d["ref"] for d in sorted(catalog, key=lambda d: (str(d.get("date") or ""), d["ref"]))
                   if "vekaletname" in tr_lower(d.get("title"))]
    if vekaletname:
        shown, more = _refs(vekaletname, 4)
        coverage_items.append({"dateText": "", "text": f"Vekaletname evrakı: {len(vekaletname)}", "who": None,
                               "status": None, "lines": [], "refs": shown, "moreRefs": more, "kind": "note"})

    subtitle = " · ".join(p for p in (court, own_no) if p) or None
    sections = [{"key": "own", "title": "Bu dosyanın tarafları", "subtitle": subtitle, "highlight": True, "refs": [],
                 "items": own_items, "empty": "Künyesinden taraf okunabilen evrak bulunamadı."}]
    if bench_items:
        sections.append({"key": "bench", "title": "Mahkeme ve diğer kişiler", "highlight": False, "refs": [],
                         "items": bench_items})
    if other_items:
        sections.append({"key": "other", "title": "Aynı kişiler diğer dosyalarda", "highlight": False, "refs": [],
                         "subtitle": "dosyaya eklenen evraklardan", "items": other_items})
    sections.append({"key": "coverage", "title": "Kapsam", "highlight": False, "refs": [], "items": coverage_items})
    return {"sections": sections, "answer": render_text(sections),
            "refs": list(dict.fromkeys(r for s in sections for i in s["items"] for r in
                                       i["refs"] + [x for line in i.get("lines", []) for x in line["refs"]]))}


def render_text(sections):
    blocks = []
    for section in sections:
        head = _tr_upper(section["title"]) + (f" ({section['subtitle']})" if section.get("subtitle") else "")
        lines = []
        for item in section["items"]:
            label = f"{item['dateText']}: " if item.get("dateText") else ""
            who = f"{item['who']} — " if item.get("who") else ""
            status = f" ({item['status']['label']})" if item.get("status") else ""
            refs = "".join(f" [{ref}]" for ref in item["refs"]) + (f" +{item['moreRefs']}" if item.get("moreRefs") else "")
            lines.append(f"- {label}{who}{item['text']}{status}{refs}")
            for line in item.get("lines", []):
                more = f" +{line['moreRefs']}" if line.get("moreRefs") else ""
                lines.append(f"    · {line['text']}" + "".join(f" [{ref}]" for ref in line["refs"]) + more)
        if not lines and section.get("empty"):
            lines.append(f"- {section['empty']}")
        blocks.append(head + "\n" + "\n".join(lines))
    return "\n\n".join(blocks)


def parties_answer(downloads_root, scope_id, case_context=None):
    scope_id = str(scope_id or "").strip()
    if not scope_id.startswith("case:"):
        return {"ok": False, "errorCode": "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED",
                "error": "Taraflar yalnız tam dava indeksi için hazırlanabilir."}
    try:
        case = load_case_full(downloads_root, scope_id)
    except (OSError, ValueError) as error:
        return {"ok": False, "errorCode": "AI_INDEX_UNAVAILABLE", "error": f"Dava indeksi okunamadı: {error}"}
    context = case_context or {}
    built = build_parties(case["rows"], case["documents"], case_no=case["caseId"] or context.get("caseId"),
                          court=case["institutionName"] or context.get("institutionName"),
                          catalog_available=case["catalogAvailable"])
    titles = {row["ref"]: row for row in case["rows"]}
    return {
        "ok": True,
        "kind": "UYAP_AI_ANSWER",
        "schemaVersion": 1,
        "answer": built["answer"],
        "workflowId": "parties",
        "workflowTitle": "Taraflar",
        "workflowNotice": NOTICE,
        "partiesSchema": PARTIES_SCHEMA,
        "partySections": built["sections"],
        "retrievalMode": RETRIEVAL_MODE,
        "sourceRefs": built["refs"],
        "sources": [{"sourceRef": ref, "title": (titles.get(ref) or {}).get("title"), "documentType": None,
                     "documentDate": (titles.get(ref) or {}).get("date"), "selectionReason": "Künye (kural tabanlı)",
                     "summaryRole": "rule_based_party"} for ref in built["refs"]],
        "provider": "Yerel kurallar",
        "model": "",
        "providerCalled": False,
        "modelAnswerAccepted": None,
        "rawDocumentsUploaded": False,
        "networkUsed": False,
        "store": False,
    }
