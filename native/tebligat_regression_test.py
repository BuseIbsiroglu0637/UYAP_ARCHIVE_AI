"""Rule-based "Tebligatlar" and tebliğ-based last days in "Ara Kararlar".

Texts follow the layout of UYAP envelopes and UETS mazbatas as a PDF reader
returns them. All names, numbers and case details are synthetic.
"""
from datetime import date as Date
from pathlib import Path
import json
import tempfile

import ai_provider
from ai_court_orders import build_court_orders, court_orders_answer
from ai_important_dates import build_important_dates
from ai_local_index import build_archive_case_index
from ai_tebligat import (
    build_tebligat, calendar_flags, case_deadlines, collect_notices, parse_notice, tebligat_answer,
)
from important_dates_regression_test import _archive_case

TODAY = (2026, 10, 14)
UETS = "12345-67890-12345"
TCKN = "12345678950"

ENVELOPE = f"""2. Aile Mahkemesi
Dosya No: 2026/77 Esas
No:
Taahhütlü
Adı ve Soyadı:
TEBLİĞ MAZBATASI
İşbu tebliğ zarfında cevap dilekçesi bulunmaktadır. 6100 s. H.M.K.'nun
136/1. maddesine göre, iki hafta içinde cevaba cevap dilekçesi verebileceğiniz hususu ihtar olunur.
ÖRNEKKÖY
T.C.
Davacı AHMET YILMAZ Vekili Av. ZEYNEP KAYA
{UETS} Uets Hesap Sahibi: Zeynep Kaya
BU ZARFTA Cevap Dilekçesi - [Vekil Mehmet Demir], Ek-Hkm_Cevap_Dilekcesi, Ek-
Hkm_Cevap_Dilekcesi VARDIR.
3-Tebliğin yapılması . . . . . . sebebiyle emin edilemediğinden . . .
"""

RESULT = f"""2. Aile Mahkemesi
Dosya No: 2026/77 Esas
No:Taahhütlü
Adı ve Soyadı:
{UETS} UETS Hesap Sahibi: ZEYNEP KAYA
136/1. maddesine göre, iki hafta içinde cevaba cevap dilekçesi verebileceğiniz
hususu ihtar olunur.
T.C.
Davacı AHMET YILMAZ Vekili Av. ZEYNEP KAYA
Vekili :
BU ZARFTA Cevap Dilekçesi - [Vekil Mehmet Demir] - 21/09/2026
VARDIR.
Örnek No: 25
Tarih Açıklama
05/10/2026 11:26 Muhatap hesabına teslim edilmek üzere UETStarafından teslim alındı.
05/10/2026 11:35 Tebligat alıcı için ayrılmış tebligat alanına (hesabına)başarılı bir şekilde konuldu.
05/10/2026 21:39 Tebligat, alıcısı tarafından açıldı.
10/10/2026
Tebligat, alıcının hesabına iletilmesine müteakip
mevzuat gereği belirlenen süre sonunda otomatik
olarak okundu sayıldı.
E-TEBLİĞ MAZBATASI
Karar No:
*12345678901234*
Yukarıda Belirtilen Tebliğ Bilgileri Entegrasyon Kapsamında PTT Ulusal Elektronik Tebligat Sistemi (UETS) Sorgu Sonuçlarıdır.
"""

TENSIP = """T.C.
ÖRNEKKÖY 2. AİLE MAHKEMESİ
TENSİP ZAPTI
TENSİP TARİHİ : 01/10/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Taraflara HMK 139. madde uyarınca dilekçelerinde gösterdikleri ancak henüz sunmadıkları belgeleri mahkemeye sunmak veya başka yerden getirtilecek belgelerin getirtilebilmesi amacıyla gereken açıklamayı yapmak üzere davetinin tebliğinden itibaren iki hafta kesin süre verilmesine, aksi halde o delile dayanmaktan vazgeçmiş sayılacaklarına,
2- Davalı vekiline banka hesap numarasını bildirmesi için 1 haftalık süre verilmesine,
3- Ön inceleme duruşmasının 11/11/2026 günü saat 10:50'de yapılmasına,
Dosya üzerinde yapılan inceleme sonunda karar verildi. 01/10/2026
"""


def _tebligat(muhatap, holder, contents, events, open_=False):
    """A UETS result in the same layout, for other addressees and envelopes."""
    rows = "\n".join(events)
    zarf = f"BU ZARFTA {contents}\nVARDIR.\n" if contents else ""
    return (f"2. Aile Mahkemesi\nDosya No: 2026/77 Esas\n{UETS} UETS Hesap Sahibi: {holder}\nT.C.\n{muhatap}\n"
            f"Vekili :\n{zarf}Tarih Açıklama\n{rows}\nE-TEBLİĞ MAZBATASI\n")


SERVED_TENSIP_DAVACI = _tebligat(
    "Davacı AHMET YILMAZ Vekili Av. ZEYNEP KAYA", "ZEYNEP KAYA", "Tensip Zaptı - 01/10/2026",
    ["01/10/2026 17:18 Muhatap hesabına teslim edilmek üzere UETStarafından teslim alındı.",
     "01/10/2026 17:22 Tebligat alıcı için ayrılmış tebligat alanına (hesabına)başarılı bir şekilde konuldu.",
     "06/10/2026", "Tebligat, alıcının hesabına iletilmesine müteakip mevzuat gereği belirlenen süre sonunda "
     "otomatik olarak okundu sayıldı."])
# No "okundu sayıldı" row yet: the day is computed (placed + 5).
SERVED_TENSIP_DAVALI = _tebligat(
    "Davalı AYŞE YILMAZ Vekili Av. MEHMET DEMİR", "MEHMET DEMİR", "Tensip Zaptı - 01/10/2026",
    ["02/10/2026 09:10 Muhatap hesabına teslim edilmek üzere UETStarafından teslim alındı.",
     "02/10/2026 09:12 Tebligat alıcı için ayrılmış tebligat alanına (hesabına)başarılı bir şekilde konuldu."])
PAPER = f"""2. Aile Mahkemesi
Dosya No: 2026/77 Esas
TEBLİĞ MAZBATASI
İşbu tebliğ zarfında dava dilekçesi ve ihtar içeren tensip zaptı bulunmaktadır. 6100 s. H.M.K.'nun
122/1. maddesine göre, iki hafta içinde cevap dilekçesi vermek suretiyle davaya cevap verebileceğiniz hususu ihtar olunur.
T.C.
DAVALI AYŞE YILMAZ (M***** ****N, B****E kızı **.**.1989 doğumlu) TC:{TCKN}
Örnek Mah. Deneme Sk. No: 4/1 Merkez
BU ZARFTA Dava Dilekçesi, Tensip Zaptı - 03/09/2026 VARDIR.
"""
FINAL_JUDGMENT = """T.C.
ÖRNEKKÖY BÖLGE ADLİYE MAHKEMESİ
2. HUKUK DAİRESİ
HÜKÜM
1)Davacı vekilinin talebinin kabulü ile ara kararın KALDIRILMASINA,
Dair, dosya üzerinde yapılan inceleme sonucunda, HMK'nın 362/(1)-f maddesi uyarınca kesin olmak üzere oybirliği ile karar verildi. 15/09/2026
"""
SERVED_JUDGMENT = _tebligat(
    "Davacı AHMET YILMAZ Vekili Av. ZEYNEP KAYA", "ZEYNEP KAYA", "Karar İlamı - 22/09/2026",
    ["22/09/2026 17:36 Muhatap hesabına teslim edilmek üzere UETStarafından teslim alındı.",
     "22/09/2026 19:59 Tebligat alıcı için ayrılmış tebligat alanına (hesabına)başarılı bir şekilde konuldu.",
     "27/09/2026", "Tebligat, alıcının hesabına iletilmesine müteakip mevzuat gereği belirlenen süre sonunda "
     "otomatik olarak okundu sayıldı."])


def _catalog(rows):
    return [{"ref": f"D{index:06d}", "title": title, "date": day} for index, (title, day, _) in enumerate(rows, 1)]


def _documents(rows):
    return {f"D{index:06d}": {"title": title, "date": day, "text": text}
            for index, (title, day, text) in enumerate(rows, 1) if text}


CASE = [
    ("Kapalı Tebligat 05/10/2026", "2026-10-05", ENVELOPE),                    # D000001
    ("Kapalı E-Tebliğ Mazbatası 11/10/2026", "2026-10-11", RESULT),            # D000002
    ("Kapalı E-Tebliğ Mazbatası 12/10/2026", "2026-10-12", RESULT),            # D000003 same mazbata filed twice
    ("Tensip Zaptı 01/10/2026", "2026-10-01", TENSIP),                         # D000004
    ("Kapalı E-Tebliğ Mazbatası 08/10/2026", "2026-10-08", SERVED_TENSIP_DAVACI),  # D000005
    ("Kapalı E-Tebliğ Mazbatası 09/10/2026", "2026-10-09", SERVED_TENSIP_DAVALI),  # D000006
    ("Kapalı Tebligat 03/09/2026", "2026-09-03", PAPER),                       # D000007
    ("Tebliğ Mazbatası 10/09/2026", "2026-09-10", None),                       # D000008 scanned, no text
    ("Karar İlamı 22/09/2026", "2026-09-22", FINAL_JUDGMENT),                  # D000009
    ("Kapalı E-Tebliğ Mazbatası 29/09/2026", "2026-09-29", SERVED_JUDGMENT),   # D000010
    ("Kapalı Tebligat 13/10/2026", "2026-10-13",                               # D000011 no result yet
     ENVELOPE.replace("Cevap Dilekçesi - [Vekil Mehmet Demir], Ek-Hkm_Cevap_Dilekcesi, Ek-\nHkm_Cevap_Dilekcesi",
                      "Bilirkişi Raporu - 12/10/2026")),
]


def test_a_uets_mazbata_is_read_without_its_address_or_ids():
    parsed = parse_notice(RESULT, "Kapalı E-Tebliğ Mazbatası 11/10/2026", Date(2026, 10, 11))
    assert parsed["kind"] == "result" and parsed["electronic"]
    assert parsed["person"] == {"role": "Davacı", "party": "Ahmet Yılmaz", "counselType": "vekili",
                                "counsel": "Av. Zeynep Kaya"}, parsed["person"]
    assert parsed["holder"] == "Zeynep Kaya"
    assert parsed["contents"] == [{"title": "Cevap Dilekçesi", "day": Date(2026, 9, 21)}], parsed["contents"]
    assert [(e["kind"], e["day"]) for e in parsed["events"]] == [
        ("accepted", Date(2026, 10, 5)), ("placed", Date(2026, 10, 5)), ("opened", Date(2026, 10, 5)),
        ("deemed", Date(2026, 10, 10))], parsed["events"]
    assert parsed["limits"] == [{"amount": 2, "unit": "hafta", "kesin": False, "label": "Cevaba cevap süresi",
                                 "basis": "tebligattaki ihtar"}], parsed["limits"]
    envelope = parse_notice(ENVELOPE, "Kapalı Tebligat 05/10/2026", Date(2026, 10, 5))
    assert envelope["kind"] == "envelope" and envelope["electronic"]
    # Attachments ("Ek-…") are not separate papers; the HMK article travels with the ihtar.
    assert envelope["contents"] == [{"title": "Cevap Dilekçesi", "day": None}], envelope["contents"]
    assert envelope["limits"][0]["basis"] == "tebligattaki ihtar (HMK 136/1)", envelope["limits"]
    paper = parse_notice(PAPER, "Kapalı Tebligat 03/09/2026", Date(2026, 9, 3))
    assert not paper["electronic"]
    assert paper["person"]["party"] == "Ayşe Yılmaz" and paper["person"]["role"] == "Davalı", paper["person"]
    assert TCKN not in json.dumps(paper, default=str, ensure_ascii=False)


def test_envelopes_pair_with_results_and_duplicates_collapse():
    notices, unreadable = collect_notices(_catalog(CASE), _documents(CASE))
    by_ref = {n["refs"][0]: n for n in notices}
    answer = by_ref["D000002"]
    assert answer["state"] == "served" and answer["served"] == Date(2026, 10, 10) and not answer["computed"]
    # The envelope matched, the mazbata filed twice became one row.
    assert answer["refs"] == ["D000002", "D000001", "D000003"], answer["refs"]
    assert by_ref["D000006"]["served"] == Date(2026, 10, 7) and by_ref["D000006"]["computed"], by_ref["D000006"]
    assert by_ref["D000007"]["state"] == "paper" and by_ref["D000007"]["served"] is None
    assert by_ref["D000011"]["state"] == "awaiting", by_ref["D000011"]
    assert [u["ref"] for u in unreadable] == ["D000008"] and unreadable[0]["kind"] == "scan"


def test_last_days_run_from_each_sides_tebligat():
    built = build_tebligat(_catalog(CASE), _documents(CASE), today=TODAY)
    top = built["sections"][0]
    assert top["key"] == "deadlines"
    rows = [(i["dateText"], i["who"], i["text"]) for i in top["items"]]
    # HMK 139 is given to both sides: each counts from its own tebliğ.
    assert ("20.10.2026", "Davacı vekili Av. Zeynep Kaya", "Delil / belge süresi: 2 hafta kesin") in rows, rows
    assert ("21.10.2026", "Davalı vekili Av. Mehmet Demir", "Delil / belge süresi: 2 hafta kesin") in rows, rows
    # Item 2 is the davalı side's alone.
    assert ("14.10.2026", "Davalı vekili Av. Mehmet Demir", "Adres bildirme süresi: 1 hafta") not in rows
    assert any(r[1] == "Davalı vekili Av. Mehmet Demir" and r[0] == "14.10.2026" for r in rows), rows
    assert not any(r[1] == "Davacı vekili Av. Zeynep Kaya" and r[0] == "13.10.2026" for r in rows), rows
    # The envelope's ihtar: cevaba cevap 2 weeks from 10.10 ends on a Saturday.
    reply = next(i for i in top["items"] if i["text"] == "Cevaba cevap süresi: 2 hafta")
    assert reply["dateText"] == "24.10.2026" and reply["status"]["label"] == "10 gün kaldı", reply
    assert any("Cumartesi" in line["text"] and "26.10.2026" in line["text"] for line in reply["lines"]), reply
    assert reply["lines"][0]["text"] == "Tebliğ 10.10.2026" and reply["lines"][0]["refs"][0] == "D000002"
    # Upcoming first, earliest first.
    days = [i["dateText"] for i in top["items"] if i["status"]["label"] != "Süre doldu"]
    assert days == sorted(days, key=lambda d: d[6:] + d[3:5] + d[:2]), days

    text = json.dumps(built["sections"], ensure_ascii=False)
    assert UETS not in text and TCKN not in text and "Deneme Sk" not in text
    groups = {s["title"]: s for s in built["sections"][1:-1]}
    judgment = groups["22.09.2026 · Karar İlamı"]["items"][0]
    assert any("kesin; kanun yolu süresi yok" in line["text"] for line in judgment["lines"]), judgment
    paper = groups["03.09.2026 · Dava Dilekçesi, Tensip Zaptı"]["items"][0]
    assert paper["status"]["label"] == "Fiziki tebligat"
    assert any("cevap süresi 2 hafta" in line["text"] and "hesaplanamadı" in line["text"] for line in paper["lines"])
    coverage = " ".join(i["text"] for i in built["sections"][-1]["items"])
    assert "1 tebliğ mazbatası taranmış" in coverage and "1 e-tebligatın mazbatası yok" in coverage, coverage


def test_holidays_and_adli_tatil_are_flagged_not_applied():
    assert "01.06.2026" in calendar_flags(Date(2026, 5, 28))[0]  # Kurban Bayramı, then a Sunday
    assert "Pazar" in calendar_flags(Date(2026, 10, 25))[0] and "26.10.2026" in calendar_flags(Date(2026, 10, 25))[0]
    assert "yarım gün" in calendar_flags(Date(2026, 10, 28))[0]
    assert any("adli tatil" in flag for flag in calendar_flags(Date(2026, 8, 12)))
    assert calendar_flags(Date(2026, 10, 21)) == []


def test_court_orders_count_from_the_tebligat():
    catalog, documents = _catalog(CASE), _documents(CASE)
    notices, _ = collect_notices(catalog, documents)
    built = build_court_orders(catalog, documents, today=TODAY, served=notices)
    tensip = next(s for s in built["sections"] if s["key"] == "record-D000004")
    first, second = tensip["items"][0], tensip["items"][1]
    texts = [line["text"] for line in first["lines"]]
    assert texts[0] == "Süre: 2 hafta kesin — tebliğden sayıldı", texts
    assert "Tebliğ 06.10.2026 — Davacı vekili Av. Zeynep Kaya → son gün 20.10.2026" in texts, texts
    assert "Tebliğ 07.10.2026 — Davalı vekili Av. Mehmet Demir → son gün 21.10.2026" in texts, texts
    second_texts = [line["text"] for line in second["lines"]]
    assert "Tebliğ 07.10.2026 — Davalı vekili Av. Mehmet Demir → son gün 14.10.2026" in second_texts, second_texts
    assert not any("Zeynep Kaya" in t for t in second_texts), second_texts
    # Without tebligat the old earliest-end line stays.
    plain = build_court_orders(catalog, documents, today=TODAY)
    plain_first = next(s for s in plain["sections"] if s["key"] == "record-D000004")["items"][0]
    assert plain_first["lines"][0]["text"] == "Süre: 2 hafta kesin — en erken bitiş 15.10.2026", plain_first


def test_important_dates_show_tebligat_last_days_instead_of_the_earliest_end():
    catalog, documents = _catalog(CASE), _documents(CASE)
    plain = build_important_dates(catalog, documents, today=TODAY)
    upcoming = [i["text"] for i in plain["sections"][0]["items"]]
    assert any(t.startswith("Süre en erken biter") and "HMK 139" in t for t in upcoming), upcoming
    built = build_important_dates(catalog, documents, today=TODAY, tebligat_deadlines=case_deadlines(catalog, documents))
    rows = [(i["dateText"], i["text"]) for i in built["sections"][0]["items"]]
    assert ("24.10.2026", "Cevaba cevap süresi son günü — Davacı vekili Av. Zeynep Kaya (tebliğ 10.10.2026)") in rows
    assert ("20.10.2026", "Delil / belge süresi son günü — Davacı vekili Av. Zeynep Kaya (tebliğ 06.10.2026)") in rows
    assert not any(t.startswith("Süre en erken biter") and "HMK 139" in t for _, t in rows), rows


def test_answer_from_a_built_index_needs_no_provider():
    files = [(title, day, ".txt", text) for title, day, text in CASE if text]
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        _archive_case(root, files)
        scope = build_archive_case_index(root, "Örnekköy 3. Aile Mahkemesi", "2026/77")["scopeId"]

        def no_provider():
            raise AssertionError("tebligat must not touch the AI provider")
        original = ai_provider.provider_status
        ai_provider.provider_status = no_provider
        try:
            result = ai_provider.answer_from_local_index(root, scope, "Tebligatları göster", workflow_id="tebligat")
            orders = court_orders_answer(root, scope, today=TODAY)
        finally:
            ai_provider.provider_status = original
        assert result["ok"] and result["workflowId"] == "tebligat" and result["tebligatSchema"] == "tebligat_v1"
        assert result["providerCalled"] is False and result["tebligatSections"][0]["key"] == "deadlines"
        assert "SÜRESİ İŞLEYENLER" in result["answer"], result["answer"]
        assert "tebliğden sayıldı" in orders["answer"], orders["answer"]
        answer = tebligat_answer(root, "session-not-a-case")
        assert answer["errorCode"] == "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED"


if __name__ == "__main__":
    test_a_uets_mazbata_is_read_without_its_address_or_ids()
    test_envelopes_pair_with_results_and_duplicates_collapse()
    test_last_days_run_from_each_sides_tebligat()
    test_holidays_and_adli_tatil_are_flagged_not_applied()
    test_court_orders_count_from_the_tebligat()
    test_important_dates_show_tebligat_last_days_instead_of_the_earliest_end()
    test_answer_from_a_built_index_needs_no_provider()
    print("Tebligat regression: PASS")
