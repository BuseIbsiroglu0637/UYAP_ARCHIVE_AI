"""Rule-based case facts for UYAP Archive AI summaries.

UYAP tensip / duruşma zabıtları, iddianameler ve dilekçeler sabit bir biçim
izler: büyük harfli "ETİKET : değer" satırları, "GEREĞİ DÜŞÜNÜLDÜ" sonrasında
numaralı ara kararlar ve "duruşmanın … günü saat … bırakılmasına" cümlesi.
Bu modül o kalıpları LLM kullanmadan okur. Künye, son celse, sonraki duruşma,
süreler ve akış böylece modelin yanlış anlayamayacağı biçimde kaynaktan gelir
ve her olgu kendi kaynak referansını (Dxxxxxx) taşır.
"""

from __future__ import annotations

import re
import time
import unicodedata

# --------------------------------------------------------------------------
# Document roles. The UYAP document title is the authoritative signal: it is
# standardised ("Tensip Zaptı", "Duruşma Zaptı", "Savunma Dilekçesi" …). Body
# text is only a fallback for untitled records, and never with generic words
# such as "hüküm" (it appears in "Nafaka Hükümlerine Uymamak", in petitions and
# in routine letters) or "şikayet dilekçesi" (every hearing record reads it).
# --------------------------------------------------------------------------
TITLE_ROLE_RULES = (
    ("procedural", "Önemli süreç işlemi", 80,
     ("ara karar", "tensip", "duruşma zapt", "duruşma tutana", "ön inceleme", "keşif zapt")),
    ("final_decision", "Sonuç / karar", 100,
     ("gerekçeli karar", "kısa karar", "nihai karar", "karar evrak", "hüküm", "ilam")),
    ("opening", "Dosya başlangıcı", 90,
     ("dava dilek", "şikayet dilek", "şikâyet dilek", "iddianame", "başvuru dilek", "talep dilek",
      "takip talebi", "itiraz dilek")),
    ("expert", "Uzman / bilirkişi incelemesi", 75, ("bilirkişi", "uzman rapor", "heyet rapor", "adli tıp")),
    ("response", "Esaslı cevap / bilgi", 70,
     ("cevap dilek", "savunma", "beyan dilek", "mazeret", "cevabi yazı", "müzekkere cevab", "gelen cevap yazı")),
    ("enforcement", "Maddi işlem / tespit", 68, ("haciz", "istihkak", "tespit tutana")),
    ("deadline", "Tebligat / süre işlemi", 55, ("tebligat", "tebliğ", "muhtıra")),
    ("correspondence", "Yazışma / talimat", 45, ("talimat", "müzekkere", "üst yazı", "yazısı")),
    ("low_value", "Mali / idari kayıt", 5,
     ("reddiyat", "makbuz", "tahsilat", "harç", "masraf", "alındı", "diğer evrak", "ön büro", "vekaletname")),
)

BODY_ROLE_RULES = (
    ("final_decision", "Sonuç / karar", 100, ("gerekçeli karar", "nihai karar", "kesin karar")),
    ("opening", "Dosya başlangıcı", 90,
     ("dava dilek", "şikayet dilek", "başvuru dilek", "talep dilek", "takip talebi", "itiraz dilek")),
    ("procedural", "Önemli süreç işlemi", 80, ("ara karar", "tensip", "duruşma zapt", "duruşma tutana", "ön inceleme")),
    ("expert", "Uzman / bilirkişi incelemesi", 75, ("bilirkişi", "uzman rapor", "heyet rapor", "inceleme rapor")),
    ("response", "Esaslı cevap / bilgi", 70, ("cevap dilek", "savunma", "müzekkere cev", "kurum cev", "cevabi yazı")),
    ("enforcement", "Maddi işlem / tespit", 68, ("haciz", "istihkak", "tespit tutana", "haciz tutana", "keşif")),
    ("deadline", "Tebligat / süre işlemi", 55, ("muhtıra", "kesin süre", "süre ver")),
)

TIMELINE_ROLES = {"opening", "procedural", "final_decision", "expert", "response"}
PARTY_SOURCE_ROLES = {"opening", "procedural", "final_decision", "response"}

_TR_LOWER = str.maketrans({"I": "ı", "İ": "i"})
_TR_UPPER = str.maketrans({"i": "İ", "ı": "I"})


def tr_lower(value):
    return str(value or "").translate(_TR_LOWER).lower()


def tr_fold(value):
    """Case- and accent-insensitive comparison key for Turkish text."""
    text = unicodedata.normalize("NFKD", tr_lower(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", text.replace("ı", "i")).strip()


def _tr_capitalize(word):
    low = tr_lower(word)
    for index, ch in enumerate(low):
        if ch.isalpha():
            first = ch.translate(_TR_UPPER)
            first = first if first != ch else ch.upper()
            return low[:index] + first + low[index + 1:]
    return low


def tr_title(value):
    """Re-case ALL-CAPS words for display; leave mixed-case words and abbreviations."""
    words = []
    for word in str(value or "").split():
        letters = [ch for ch in word if ch.isalpha()]
        if len(letters) > 1 and "." not in word and all(ch.isupper() for ch in letters):
            words.append(_tr_capitalize(word))
        else:
            words.append(word)
    return " ".join(words)


def title_document_role(title):
    """Return (role, reason, base_score) from the UYAP title, or None."""
    folded = tr_lower(title)
    for role, reason, score, patterns in TITLE_ROLE_RULES:
        if any(pattern in folded for pattern in patterns):
            return role, reason, score
    return None


def body_document_role(text):
    folded = tr_lower(text)
    for role, reason, score, patterns in BODY_ROLE_RULES:
        if any(pattern in folded for pattern in patterns):
            return role, reason, score
    return None


def is_attachment_title(title):
    return bool(re.search(r"(?:^|\s|-)ek\s*\d+\b", tr_lower(title)))


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------
_MONTHS = {
    "ocak": 1, "şubat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "haziran": 6,
    "temmuz": 7, "ağustos": 8, "eylül": 9, "ekim": 10, "kasım": 11, "aralık": 12,
}
_DATE_NUMERIC = re.compile(r"(?<!\d)(\d{1,2})[./](\d{1,2})[./](\d{4})(?!\d)")
_DATE_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{1,2})-(\d{1,2})(?!\d)")
_DATE_WORDS = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")\s+(\d{4})(?!\d)")


def _valid(year, month, day):
    return 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31


def find_dates(text):
    """All calendar dates mentioned in text as (year, month, day) tuples."""
    found = set()
    raw = str(text or "")
    for day, month, year in _DATE_NUMERIC.findall(raw):
        if _valid(int(year), int(month), int(day)):
            found.add((int(year), int(month), int(day)))
    for year, month, day in _DATE_ISO.findall(raw):
        if _valid(int(year), int(month), int(day)):
            found.add((int(year), int(month), int(day)))
    for day, month_name, year in _DATE_WORDS.findall(tr_lower(raw)):
        month = _MONTHS[month_name]
        if _valid(int(year), month, int(day)):
            found.add((int(year), month, int(day)))
    return found


def parse_date(value):
    dates = sorted(find_dates(value))
    return dates[0] if dates else None


def format_date(value):
    if not value:
        return ""
    year, month, day = value
    return f"{day:02d}/{month:02d}/{year}"


def _today():
    now = time.localtime()
    return (now.tm_year, now.tm_mon, now.tm_mday)


# --------------------------------------------------------------------------
# Privacy: identifiers never belong in a summary that may be printed/shared.
# --------------------------------------------------------------------------
_MASKS = (
    (re.compile(r"(?<![\dA-Za-z])TR\d{2}(?:\s?\d{4}){5}\s?\d{2}(?!\d)"), "TR•• •••• (IBAN gizlendi)"),
    (re.compile(r"(?<!\d)\d{5}-\d{5}-\d{5}(?!\d)"), "•••••-•••••-•••••"),
    (re.compile(r"(?i)((?:vkn|vergi\s*(?:kimlik\s*)?(?:no|numarası))\s*[:：]?\s*)\d{10}(?!\d)"), r"\1••••••••••"),
    (re.compile(r"(?<!\d)[1-9]\d{10}(?!\d)"), "•••••••••••"),
)


def mask_identifiers(text):
    """Mask TCKN, UETS addresses, VKN and IBAN. Returns (masked_text, count)."""
    masked = str(text or "")
    total = 0
    for pattern, replacement in _MASKS:
        masked, count = pattern.subn(replacement, masked)
        total += count
    return masked, total


# --------------------------------------------------------------------------
# Header labels: "SANIK : AD SOYAD, baba adı …", "VEKİLLERİ : Av. …"
# --------------------------------------------------------------------------
_LABEL_LINE = re.compile(r"^\s*([A-ZÇĞİÖŞÜÂÎÛ][A-ZÇĞİÖŞÜÂÎÛ .]{1,40}?)\s*:\s*(.*?)\s*$")
PARTY_LABELS = {
    "MÜŞTEKİ": "Müşteki", "MÜŞTEKİLER": "Müşteki", "ŞİKAYETÇİ": "Şikayetçi", "ŞİKÂYETÇİ": "Şikayetçi",
    "KATILAN": "Katılan", "MAĞDUR": "Mağdur", "SANIK": "Sanık", "SANIKLAR": "Sanık",
    "ŞÜPHELİ": "Şüpheli", "DAVACI": "Davacı", "DAVACILAR": "Davacı", "DAVALI": "Davalı",
    "DAVALILAR": "Davalı", "ALACAKLI": "Alacaklı", "BORÇLU": "Borçlu",
}
COUNSEL_LABELS = {
    "VEKİL": "vekili", "VEKİLİ": "vekili", "VEKİLLERİ": "vekili",
    "MÜDAFİ": "müdafii", "MÜDAFİİ": "müdafii", "MÜDAFİLERİ": "müdafii",
}
DEFENDANT_ROLES = {"Sanık", "Şüpheli"}
SUBJECT_LABELS = {"SUÇ": "Suç", "SUÇLAR": "Suç", "DAVA": "Dava", "DAVANIN KONUSU": "Dava"}
SUBJECT_DATE_LABELS = {"SUÇ TARİHİ": "Suç tarihi", "SUÇ TARİHLERİ": "Suç tarihi", "DAVA TARİHİ": "Dava tarihi"}
_HEADER_STOP = re.compile(
    r"(?i)belli edilen gün|belirli gün|gereği düşünüldü|açıklamalar|^\s*g\.?\s?d\.?\s*:|vekilinin dava dilek|dosya incelendi")
_NAME_OK = re.compile(r"[A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû.' -]+")


def _label_key(label):
    return re.sub(r"\s+", " ", label.replace(".", " ")).strip()


def name_from_value(value):
    """Person name at the start of a label value, without birth/address/ID detail."""
    head = re.split(r"[,(\[]|\s-\s|\s{2,}|\bTCKN\b|\bT\.C\.", str(value or ""), maxsplit=1)[0]
    head = re.sub(r"(?:\s+\d[\d\s]*)+$", "", head).strip(" .:;-")
    words = head.split()
    if not 2 <= len(words) <= 7 or not _NAME_OK.fullmatch(head):
        return None
    if any(len(word) > 24 for word in words):
        return None
    return tr_title(head)


def _counsel_split(label):
    """'MÜŞTEKİ VEKİLİ' -> ('Müşteki', 'vekili'); 'VEKİLLERİ' -> (None, 'vekili')."""
    parts = label.split()
    if parts and parts[-1] in COUNSEL_LABELS:
        owner = " ".join(parts[:-1])
        return PARTY_LABELS.get(owner), COUNSEL_LABELS[parts[-1]]
    return None, None


def parse_header(text):
    """Parties, counsel and case subject from the label block at the top of a record."""
    parties, subject, subject_date = [], None, None
    current_party = None
    counsel_mode = None
    for line in str(text or "").splitlines()[:80]:
        if _HEADER_STOP.search(line):
            break
        match = _LABEL_LINE.match(line)
        if not match:
            if counsel_mode and re.match(r"^\s*Av\.?\s", line, re.I):
                name = name_from_value(line.strip())
                if name and current_party is not None:
                    current_party["counsel"].append({"name": name, "label": counsel_mode})
                continue
            counsel_mode = None
            continue
        label, value = _label_key(match.group(1)), match.group(2)
        owner, counsel_label = _counsel_split(label)
        if counsel_label:
            target = current_party
            if owner:
                target = next((p for p in reversed(parties) if p["role"] == owner), None)
            elif counsel_label == "müdafii":
                target = next((p for p in reversed(parties) if p["role"] in DEFENDANT_ROLES), current_party)
            if target is None:
                target = {"role": owner or ("Sanık" if counsel_label == "müdafii" else "Taraf"), "name": None, "counsel": []}
                parties.append(target)
            name = name_from_value(value)
            if name:
                target["counsel"].append({"name": name, "label": counsel_label})
            counsel_mode = counsel_label
            continue
        counsel_mode = None
        if label in PARTY_LABELS:
            current_party = {"role": PARTY_LABELS[label], "name": name_from_value(value), "counsel": []}
            parties.append(current_party)
        elif label in SUBJECT_LABELS and value and subject is None:
            subject = {"label": SUBJECT_LABELS[label], "text": tr_title(value.strip(" .;"))[:160]}
        elif label in SUBJECT_DATE_LABELS and subject_date is None:
            date = parse_date(value)
            if date:
                subject_date = {"label": SUBJECT_DATE_LABELS[label], "date": date}
    return parties, subject, subject_date


# --------------------------------------------------------------------------
# Hearing records and interim decisions
# --------------------------------------------------------------------------
_DECISION_MARKER = re.compile(
    r"(?i)GERE[ĞG][İI]\s+D[ÜU][ŞS][ÜU]N[ÜU]LD[ÜU]\s*[:/.\s]*|(?m:^\s*G\.?\s?D\.?\s*:)")
_DECISION_END = re.compile(
    r"(?im)^\s*(?:katip|kâtip|zabıt kâtibi|zabıt katibi|hakim|hâkim|başkan|üye)\b|©|e-imzal|bu belge 5070")
_NEXT_CLAUSE = re.compile(r"(?is)\bbu\s+(?:nedenle|nedenlerle|sebeple|sebeplerle)\b.*$")
_ITEM_SPLIT = re.compile(r"(?m)^\s*(\d{1,2})\s*[-–]\s*\)?\s*")
_NEXT_HEARING = (
    # Ceza: "duruşmanın …"; hukuk: "ön inceleme duruşmasının … yapılmasına".
    re.compile(r"(?i)duruşma(?:sı)?nın\s+(\d{1,2}[./]\d{1,2}[./]\d{4})\s+(?:günü\s+)?(?:saat\s+(\d{1,2}[:.]\d{2}))?"),
    re.compile(r"(?i)(\d{1,2}[./]\d{1,2}[./]\d{4})\s+(?:günü|tarihine|tarihinde)\s+(?:saat\s+(\d{1,2}[:.]\d{2})\S*\s+)?"
               r"(?:ertelen|bırakıl|yapılmas)"),
)
_SCHEDULING_VERB = re.compile(r"(?i)ertelen|bırakıl|yapılmas|açılmas")
_HEARING_DATE = re.compile(r"(?i)DURU[ŞS]MA\s+TAR(?:[İI]H[İI])?\s*:\s*(\d{1,2}/\d{1,2}/\d{4})")
_CELSE = re.compile(r"(?i)CELSE\s+NO\s*:\s*(\d+)")
_TALIMAT = re.compile(r"(?i)TAL[İI]MAT\s+NO\s*:")
_COURT = re.compile(r"T\.C\.\s+([A-ZÇĞİÖŞÜ0-9][A-ZÇĞİÖŞÜ0-9.\s]{2,80}?MAHKEMES[İI])")
_ATTENDANCE = re.compile(r"(?i)\b(geldi|gelmedi|gelen olmad|hazır bulun|katılmad|mazeret)")
_DEADLINE = re.compile(
    r"(?i)(\d+|bir|iki|üç|dört|beş|altı|yedi|sekiz|dokuz|on beş|on|otuz)\s*(haftalık|günlük|aylık|hafta|gün|ay)\s+"
    r"(kesin\s+)?süre")
_LINKED_FILE = re.compile(
    r"([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü.]*(?:\s+[A-ZÇĞİÖŞÜ0-9][A-Za-zÇĞİÖŞÜçğıöşü.]*){0,5}\s+(?:Dairesi|Mahkemesi))"
    r"[’'A-Za-zçğıöşü]*[^\d\n]{0,40}?(\d{4}/\d{1,7})\s+Esas")


# Sentence boundary that does not split after "Av.", "Dr.", "No.", "Mah." etc.
_SENTENCE_SPLIT = re.compile(
    r"(?<!\bAv\.)(?<!\bAV\.)(?<!\bDr\.)(?<!\bNo\.)(?<!\bMah\.)(?<!\bSk\.)(?<!\bCad\.)(?<!\bT\.C\.)(?<=[.!?])\s+|\n")


def _short(text, limit=260):
    value = re.sub(r"\s+", " ", str(text or "")).strip(" ,;")
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _time_text(value):
    return value.replace(".", ":") if value else None


def next_hearing_in(text):
    """The hearing date the record schedules.

    A date followed by a scheduling verb ("…'e ertelenmesine", "… yapılmasına")
    wins over a passing mention of an earlier hearing; the last such clause is
    the operative one. Without any, the first match keeps the old behaviour.
    """
    raw = str(text or "")
    first, scheduled = None, None
    for pattern in _NEXT_HEARING:
        for match in pattern.finditer(raw):
            date = parse_date(match.group(1))
            if not date:
                continue
            found = (match.start(), {"date": date, "time": _time_text(match.group(2))})
            first = first or found
            if _SCHEDULING_VERB.search(raw[match.end():match.end() + 60]) or _SCHEDULING_VERB.search(match.group(0)):
                scheduled = max(scheduled, found, key=lambda item: item[0]) if scheduled else found
    chosen = scheduled or first
    return chosen[1] if chosen else None


def parse_decisions(text):
    """Numbered interim decisions and the closing next-hearing clause."""
    raw = str(text or "")
    marker = None
    for match in _DECISION_MARKER.finditer(raw):
        marker = match
    if marker is None:
        return [], None
    body = raw[marker.end():]
    end = _DECISION_END.search(body)
    if end:
        body = body[: end.start()]
    clause_match = _NEXT_CLAUSE.search(body)
    clause = clause_match.group(0) if clause_match else ""
    if clause_match:
        body = body[: clause_match.start()]
    body = body.strip(" /\n")
    parts = _ITEM_SPLIT.split(body)
    items = []
    if len(parts) > 1:
        for index in range(1, len(parts) - 1, 2):
            item = _short(parts[index + 1])
            if len(item) >= 8:
                items.append(item)
    elif len(body) >= 8:
        items.append(_short(body))
    return items, (_short(clause, 200) if clause else None)


def parse_hearing(text, title=""):
    raw = str(text or "")
    head = raw[:400]
    date_match = _HEARING_DATE.search(raw)
    date = parse_date(date_match.group(1)) if date_match else None
    celse = _CELSE.search(raw)
    court = _COURT.search(head)
    marker = None
    for match in _DECISION_MARKER.finditer(raw):
        marker = match
    before = raw[: marker.start()] if marker else raw
    attendance = []
    for sentence in _SENTENCE_SPLIT.split(before):
        sentence = sentence.strip()
        if 8 <= len(sentence) <= 220 and _ATTENDANCE.search(sentence):
            attendance.append(_short(sentence, 200))
        if len(attendance) >= 3:
            break
    decisions, clause = parse_decisions(raw)
    return {
        "date": date,
        "celse": int(celse.group(1)) if celse else None,
        "court": tr_title(re.sub(r"\s+", " ", court.group(1))) if court else None,
        "isTalimat": bool(_TALIMAT.search(raw)) or "talimat" in tr_lower(title),
        "attendance": attendance,
        "decisions": decisions,
        "nextHearing": next_hearing_in(clause or raw),
    }


def deadlines_in(items):
    """Only the clause that grants the time limit, not the whole interim decision."""
    found = []
    for item in items:
        match = _DEADLINE.search(item)
        if not match:
            continue
        clause = next((part for part in re.split(r",\s+", item) if _DEADLINE.search(part)), item)
        found.append({"text": _short(clause, 200), "kesin": bool(match.group(3))})
    return found


def linked_files_in(text, own_case_no=None):
    found = []
    for name, number in _LINKED_FILE.findall(str(text or "")):
        if own_case_no and number == own_case_no:
            continue
        label = f"{tr_title(name.strip())} {number} Esas"
        if label not in found:
            found.append(label)
    return found


# --------------------------------------------------------------------------
# Whole-case extraction
# --------------------------------------------------------------------------
def _clean_title(title):
    value = re.sub(r"\s+\d{1,2}[./]\d{1,2}[./]\d{4}\b", "", str(title or "")).strip(" -")
    return value or "Adsız evrak"


def _doc_date(document):
    return document.get("date") or parse_date(document.get("title"))


def _merge_party(parties, party, ref):
    key = (party["role"], tr_fold(party["name"]) if party["name"] else None)
    existing = next((p for p in parties if (p["role"], tr_fold(p["name"]) if p["name"] else None) == key), None)
    if existing is None and party["name"] is None:
        existing = next((p for p in parties if p["role"] == party["role"]), None)
    if existing is None:
        existing = {"role": party["role"], "name": party["name"], "refs": [], "counsel": []}
        parties.append(existing)
    if ref not in existing["refs"]:
        existing["refs"].append(ref)
    for counsel in party["counsel"]:
        known = next((c for c in existing["counsel"] if tr_fold(c["name"]) == tr_fold(counsel["name"])), None)
        if known is None:
            existing["counsel"].append({**counsel, "refs": [ref]})
        elif ref not in known["refs"]:
            known["refs"].append(ref)


def extract_case_facts(documents, institution_name=None, case_id=None, today=None):
    """Deterministic facts for a case from its indexed documents.

    documents: iterable of {"ref", "title", "date" (y, m, d) or None, "text"}.
    """
    docs = []
    for document in documents:
        role = title_document_role(document.get("title"))
        docs.append({**document, "date": _doc_date(document), "role": role[0] if role else "other"})
    docs.sort(key=lambda d: (d["date"] or (0, 0, 0), d["ref"]))

    case_no = None
    match = re.search(r"(20\d{2})[/_ -](\d{1,7})", str(case_id or ""))
    if match:
        case_no = f"{match.group(1)}/{int(match.group(2))}"

    parties, subject, subject_date = [], None, None
    hearings, linked = [], []
    for doc in docs:
        text = doc.get("text") or ""
        if doc["role"] in PARTY_SOURCE_ROLES:
            found_parties, found_subject, found_date = parse_header(text)
            for party in found_parties:
                _merge_party(parties, party, doc["ref"])
            if found_subject and subject is None:
                subject = {**found_subject, "ref": doc["ref"]}
            if found_date and subject_date is None:
                subject_date = {**found_date, "ref": doc["ref"]}
        if doc["role"] == "procedural":
            hearing = parse_hearing(text, doc.get("title"))
            hearing.update({"ref": doc["ref"], "title": _clean_title(doc.get("title")), "documentDate": doc["date"]})
            hearing["date"] = hearing["date"] or doc["date"]
            hearing["isTensip"] = "tensip" in tr_lower(doc.get("title"))
            hearing["isHearingRecord"] = bool(re.search(r"duruşma (?:zapt|tutana)", tr_lower(doc.get("title"))))
            hearings.append(hearing)
            for label in linked_files_in(" ".join(hearing["decisions"]), case_no):
                if label not in [item["text"] for item in linked]:
                    linked.append({"text": label, "ref": doc["ref"]})

    records = [h for h in hearings if h["isHearingRecord"]]
    own_records = [h for h in records if not h["isTalimat"]] or records
    last_hearing = max(own_records, key=lambda h: (h["date"] or (0, 0, 0), h["ref"])) if own_records else None

    next_hearing = None
    # A talimat court's own hearing date is not the case's next hearing.
    with_next = [h for h in hearings if h.get("nextHearing") and not h["isTalimat"]] or [
        h for h in hearings if h.get("nextHearing")]
    if with_next:
        source = max(with_next, key=lambda h: (h["documentDate"] or h["date"] or (0, 0, 0), h["ref"]))
        next_hearing = {**source["nextHearing"], "ref": source["ref"], "decidedOn": source["date"]}
        next_hearing["past"] = next_hearing["date"] < (today or _today())

    deadlines = []
    if last_hearing:
        for item in deadlines_in(last_hearing["decisions"]):
            deadlines.append({**item, "decidedOn": last_hearing["date"], "ref": last_hearing["ref"]})

    timeline = []
    if subject_date:
        timeline.append({"date": subject_date["date"], "label": subject_date["label"], "ref": subject_date["ref"]})
    hearing_by_ref = {h["ref"]: h for h in hearings}
    for doc in docs:
        if doc["role"] not in TIMELINE_ROLES or is_attachment_title(doc.get("title")) or not doc["date"]:
            continue
        label = _clean_title(doc.get("title"))
        hearing = hearing_by_ref.get(doc["ref"])
        if hearing:
            notes = []
            if hearing["celse"]:
                notes.append(f"{hearing['celse']}. celse")
            if hearing["isTalimat"] and hearing["court"]:
                notes.append(f"talimat · {hearing['court']}")
            if hearing.get("nextHearing"):
                prefix = "talimat duruşma günü " if hearing["isTalimat"] else "sonraki duruşma "
                notes.append(prefix + format_date(hearing["nextHearing"]["date"]))
            if notes:
                label += " (" + "; ".join(notes) + ")"
        entry = {"date": doc["date"], "label": label, "ref": doc["ref"]}
        if not any(e["date"] == entry["date"] and e["label"] == entry["label"] for e in timeline):
            timeline.append(entry)
    timeline.sort(key=lambda e: (e["date"], e["ref"]))
    if len(timeline) > 14:
        timeline = timeline[:4] + timeline[-10:]

    court_name = str(institution_name or "").strip() or None
    if not court_name:
        head_court = next((h["court"] for h in hearings if h["court"] and not h["isTalimat"]), None)
        court_name = head_court
    return {
        "schemaVersion": 1,
        "court": court_name,
        "caseNo": case_no,
        "subject": subject,
        "subjectDate": subject_date,
        "parties": parties,
        "linkedFiles": linked,
        "hearings": hearings,
        "lastHearing": last_hearing,
        "nextHearing": next_hearing,
        "deadlines": deadlines,
        "timeline": timeline,
    }


# --------------------------------------------------------------------------
# Rendering. Each line ends with its source tag so the reader can check it.
# --------------------------------------------------------------------------
SECTION_KUNYE = "KÜNYE"
SECTION_LAST_HEARING = "SON CELSE VE ARA KARARLAR"
SECTION_NEXT = "SONRAKİ DURUŞMA VE SÜRELER"
SECTION_OVERVIEW = "GENEL DURUM"
SECTION_DEVELOPMENTS = "ÖNE ÇIKAN GELİŞMELER"
SECTION_TIMELINE = "AKIŞ"
SECTION_COVERAGE = "KAPSAM VE EKSİKLER"


def _tag(*refs):
    unique = [ref for ref in dict.fromkeys(r for r in refs if r)]
    return (" " + " ".join(f"[{ref}]" for ref in unique)) if unique else ""


def render_fact_sections(facts):
    """Deterministic sections as {heading: [lines]} (ordered)."""
    sections = {}

    kunye = []
    if facts.get("court") or facts.get("caseNo"):
        kunye.append("Mahkeme / dosya: " + " · ".join(
            part for part in (facts.get("court"), f"{facts['caseNo']} Esas" if facts.get("caseNo") else None) if part))
    if facts.get("subject"):
        kunye.append(f"{facts['subject']['label']}: {facts['subject']['text']}{_tag(facts['subject']['ref'])}")
    if facts.get("subjectDate"):
        kunye.append(f"{facts['subjectDate']['label']}: {format_date(facts['subjectDate']['date'])}"
                     f"{_tag(facts['subjectDate']['ref'])}")
    for party in facts.get("parties") or []:
        groups = {}
        for counsel in party["counsel"]:
            groups.setdefault(counsel["label"], []).append(counsel)
        refs = list(party["refs"])
        counsel_text = []
        for label, members in groups.items():
            counsel_text.append(f"{label}: " + ", ".join(m["name"] for m in members))
            for member in members:
                refs.extend(member["refs"])
        if party["name"]:
            line = f"{party['role']}: {party['name']}"
            if counsel_text:
                line += " — " + "; ".join(counsel_text)
        elif counsel_text:
            line = f"{party['role']} " + "; ".join(counsel_text)
        else:
            continue
        kunye.append(line + _tag(*refs))
    for linked in (facts.get("linkedFiles") or [])[:4]:
        kunye.append(f"Bağlantılı dosya: {linked['text']}{_tag(linked['ref'])}")
    if kunye:
        sections[SECTION_KUNYE] = kunye

    last = facts.get("lastHearing")
    hearing_lines = []
    if last:
        head = format_date(last["date"]) or "Tarihsiz"
        if last.get("celse"):
            head += f" · {last['celse']}. celse"
        if last.get("isTalimat") and last.get("court"):
            head += f" · talimat ({last['court']})"
        hearing_lines.append(head + _tag(last["ref"]))
        if last.get("attendance"):
            hearing_lines.append("Hazır bulunma: " + " ".join(last["attendance"]) + _tag(last["ref"]))
        for index, decision in enumerate(last.get("decisions") or [], 1):
            hearing_lines.append(f"Ara karar {index}: {decision}{_tag(last['ref'])}")
    else:
        hearing_lines.append("Okunabilen evraklar arasında duruşma zaptı bulunamadı.")
    sections[SECTION_LAST_HEARING] = hearing_lines

    next_lines = []
    upcoming = facts.get("nextHearing")
    if upcoming:
        line = "Sonraki duruşma: " + format_date(upcoming["date"])
        if upcoming.get("time"):
            line += f" saat {upcoming['time']}"
        if upcoming.get("past"):
            line += " — tarihi geçmiş; yeni bir duruşma zaptı arşive henüz girmemiş olabilir"
        next_lines.append(line + _tag(upcoming["ref"]))
    else:
        next_lines.append("Kaynaklarda ileri tarihli duruşma kararı bulunamadı.")
    for deadline in facts.get("deadlines") or []:
        when = format_date(deadline.get("decidedOn"))
        note = f" ({when} celsesinde verildi{'; kesin süre' if deadline.get('kesin') else ''})" if when else ""
        next_lines.append(f"Süre: {deadline['text']}{note}{_tag(deadline['ref'])}")
    if facts.get("deadlines"):
        next_lines.append("Not: sürenin başlangıcı tebliğ veya öğrenme tarihine bağlı olabilir; zaptı kontrol edin.")
    sections[SECTION_NEXT] = next_lines

    timeline = [f"{format_date(e['date'])} · {e['label']}{_tag(e['ref'])}" for e in facts.get("timeline") or []]
    if timeline:
        sections[SECTION_TIMELINE] = timeline
    return sections


def render_coverage_lines(coverage):
    coverage = coverage or {}
    total = coverage.get("totalDocuments")
    indexed = coverage.get("indexedDocuments")
    sent = coverage.get("sentToAi")
    parts = []
    if isinstance(total, int):
        parts.append(f"{total} evrak")
    if isinstance(indexed, int):
        parts.append(f"{indexed} evrakın metni okunabildi")
    if isinstance(sent, int):
        parts.append(f"{sent} evrak AI'ya gönderildi")
    if isinstance(total, int) and isinstance(indexed, int) and total > indexed:
        parts.append(f"{total - indexed} evrak metinsiz (taranmış görüntü olabilir; OCR gerekir)")
    lines = [" · ".join(parts)] if parts else []
    samples = coverage.get("notIndexedSamples") or []
    if samples:
        shown = "; ".join(
            f"{_clean_title(item.get('title'))}{' (' + item['date'] + ')' if item.get('date') else ''}"
            for item in samples[:6])
        more = len(coverage.get("notIndexed") or samples) - min(len(samples), 6)
        lines.append("Metni okunamayan evraklardan: " + shown + (f" ve {more} evrak daha" if more > 0 else ""))
    elif isinstance(total, int) and isinstance(indexed, int) and total > indexed and not coverage.get("metaAvailable"):
        lines.append("Okunamayan evrakların listesi için dava indeksini yeniden oluşturun.")
    return lines
