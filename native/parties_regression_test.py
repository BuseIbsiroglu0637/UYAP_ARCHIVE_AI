"""Rule-based "Taraflar": header parsing, file attribution and the view.

All names, numbers and case details are synthetic; the layouts follow real UYAP records.
"""
from pathlib import Path
import tempfile

import ai_provider
from ai_case_facts import parse_header
from ai_local_index import build_archive_case_index
from ai_parties import build_parties, header_meta, parties_answer
from important_dates_regression_test import TENSIP, _archive_case


def _sides(text):
    return [(p["role"], p["name"], [c["name"] for c in p["counsel"]], p.get("note")) for p in parse_header(text)[0]]


def test_header_layouts_of_petitions_decisions_and_enforcement_letters():
    # "VEREN DAVACI" on a wrapped label; counsel per side; ID and UETS stay out of names.
    assert _sides("CEVABA CEVAP\nVEREN DAVACI : Murat Kaya (TCKN : 12345678950)\nVEKİLİ : Av. Selin Oral [--] UETS\n"
                  "DAVALI : Ayşe Demir\nVEKİLİ : Av. Kerem Yalın\nKONU : Cevaba cevap") == [
        ("Davacı", "Murat Kaya", ["Av. Selin Oral"], None), ("Davalı", "Ayşe Demir", ["Av. Kerem Yalın"], None)]
    # "BULUNAN-DAVALI" and two lawyers under one label with addresses between them.
    assert _sides("DAVACI : AYŞE DEMİR - Merkez Mah. Çınar Sk. No: 3\nSÜRE UZATIM TALEBİNDE\n"
                  "BULUNAN-DAVALI : MURAT KAYA - Yeni Mah. No: 8\nVEKİLLERİ : Av. CAN SU-[--] UETS\n"
                  "Av. ALİ VELİ - [--] UETS\nDAVA : Boşanma") == [
        ("Davacı", "Ayşe Demir", [], None), ("Davalı", "Murat Kaya", ["Av. Can Su", "Av. Ali Veli"], None)]
    # Upper-court layout: the role on its own line, the qualifier in brackets.
    assert _sides("DAVACI\n(İstinaf Kanun Yoluna Başvuran) : MURAT KAYA - \nVEKİLİ : Av. SELİN ORAL\n"
                  "DAVALI : AYŞE DEMİR") == [
        ("Davacı", "Murat Kaya", ["Av. Selin Oral"], "istinaf kanun yoluna başvuran"), ("Davalı", "Ayşe Demir", [], None)]
    # Counsel written into the value; compound roles; address labels are not sides.
    assert _sides("2. CEVAP DİLEKÇESİ VEREN\nDAVALI : Ayşe Demir vekili Av. Kerem Yalın\n") == [
        ("Davalı", "Ayşe Demir", ["Av. Kerem Yalın"], None)]
    assert _sides("DAVACI-KARŞI DAVALI : AYŞE DEMİR\nDAVACI ADRESİ : Merkez Mah. No 3\n") == [
        ("Davacı", "Ayşe Demir", [], "karşı davalı")]
    assert _sides("ALACAKLI : ÖRNEK VARLIK YÖNETİM ANONİM ŞİRKETİ, 1234567890 Vergi Nolu,\nVEKİLİ : Av. Nur Ak\n"
                  "Bahariye Mah. 1671. Sok. No:94\nAv. Can Su\nBORÇLU : MURAT KAYA,  TC Nolu,\n") == [
        ("Alacaklı", "Örnek Varlık Yönetim Anonim Şirketi", ["Av. Nur Ak", "Av. Can Su"], None),
        ("Borçlu", "Murat Kaya", [], None)]
    meta = header_meta("T.C.\nÖRNEKKÖY 05/03/2026\nİCRA DAİRESİ\n2025/39 TLMT.\nALACAKLI : X Y\nBORÇLU : Z W\n",
                       [{"role": "Alacaklı"}, {"role": "Borçlu"}])
    assert (meta["kind"], meta["number"], meta["office"]) == ("icra", "2025/39", "Örnekköy İcra Dairesi"), meta
    expert = header_meta("ESAS NO : 2026/45 Esas\nBİLİRKİŞİ : Adı: ZEYNEP ARSLAN, Sicili: 46447, Uzmanlık: ses\n", [])
    assert expert["experts"] == ["Zeynep Arslan"] and expert["number"] == "2026/45", expert


DAVA = """ÖRNEKKÖY NÖBETÇİ AİLE MAHKEMESİNE
DAVACI : MURAT KAYA - 12345678950
VEKİLİ : Av. SELİN ORAL
DAVALI : AYŞE DEMİR - Merkez Mah. Çınar Sk. No: 3
KONU : Katılma alacağı
AÇIKLAMALAR :
"""
TENSIP_45 = """T.C.
ÖRNEKKÖY
3. AİLE MAHKEMESİ
TENSİP TUTANAĞI
ESAS NO : 2026/45 Esas
HAKİM : Zehra ÇINAR 123456
KATİP : Ece TAN 2345
DAVACI : MURAT KAYA- - [--] UETS
VEKİLİ : Av. SELİN ORAL - [--] UETS
DAVALI : AYŞE DEMİR- - Merkez Mah. Çınar Sk. No: 3
DAVA : Katılma Alacağı
GEREĞİ DÜŞÜNÜLDÜ:
1- Dosyanın ön incelemeye bırakılmasına,
"""
CEVAP_TYPO = """ÖRNEKKÖY 3. AİLE MAHKEMESİNE
DOSYA NO : 2025/45
DAVALI : Ayşe Demir
VEKİLİ : Av. Kerem Yalın
DAVACI : Murat Kaya
VEKİLİ : Av. Selin Oral
KONU : Cevap dilekçemizdir.
"""
ZABIT = """T.C.
ÖRNEKKÖY
3. AİLE MAHKEMESİ
DURUŞMA TUTANAĞI
ESAS NO : 2026/45 Esas
HAKİM : Deniz KORKMAZ 654321
DAVACI : MURAT KAYA
VEKİLİ : Av. TUNA ER
DAVALI : AYŞE DEMİR
VEKİLİ : Av. KEREM YALIN
GEREĞİ DÜŞÜNÜLDÜ:
"""
BAM = """T.C.
ANKARA
BÖLGE ADLİYE MAHKEMESİ
12. HUKUK DAİRESİ
ESAS NO : 2026/900
BAŞKAN : Ali Uzun (1)
İNCELENEN KARARIN
MAHKEMESİ : ÖRNEKKÖY 3. AİLE MAHKEMESİ
NUMARASI : 2026/45 Esas
DAVACI
(İstinaf Kanun Yoluna Başvuran) : MURAT KAYA -
VEKİLİ : Av. SELİN ORAL
DAVALI : AYŞE DEMİR
"""
ATTACHED_DIVORCE = """T.C.
ÖRNEKKÖY
3. AİLE MAHKEMESİ
ARA KARAR
ESAS NO : 2025/88 Esas
HAKİM : Zehra ÇINAR 1
DAVACI-KARŞI DAVALI : AYŞE DEMİR - Merkez Mah.
SÜRE UZATIM TALEBİNDE
BULUNAN-DAVALI : MURAT KAYA - Yeni Mah.
VEKİLLERİ : Av. CAN SU-[--] UETS
Av. ALİ VELİ - [--] UETS
ARA KARAR TARİHİ : 09/12/2025
GEREĞİ DÜŞÜNÜLDÜ:
"""
ENFORCEMENT = """T.C.
ÖRNEKKÖY 05/03/2026
İCRA DAİRESİ
2025/39 TLMT.
ALACAKLI : ÖRNEK VARLIK YÖNETİM ANONİM ŞİRKETİ, 1234567890 Vergi Nolu,
VEKİLİ : Av. Nur Ak
BORÇLU : MURAT KAYA,  TC Nolu,
"""
INDICTMENT = """T.C.
ÖRNEKKÖY
CUMHURİYET BAŞSAVCILIĞI
Soruşturma No : 2025/5533
Esas No : 2026/133
İ D D İ A N A M E
DAVACI :K.H.
MÜŞTEKİ :ÖRNEK BANKASI ANONİM ŞİRKETİ
VEKİLİ :Av. NUR AK
ŞÜPHELİ :MURAT KAYA, Hasan oğlu, 01/01/1990 doğumlu
SUÇ :Muhafaza Görevini Kötüye Kullanma
SORUŞTURMA EVRAKI İNCELENDİ:
"""
EXPERT = """ESAS NO : 2026/45 Esas
BİLİRKİŞİ : Adı: ZEYNEP ARSLAN, Sicili: 46447
"""
UNKNOWN = "DAVACI : AYŞE DEMİR\nDAVALI : MURAT KAYA\n"

CATALOG = [
    ("D000001", "Dava Dilekçesi", "2026-03-23", DAVA),
    ("D000002", "Tensip Zaptı", "2026-03-27", TENSIP_45),
    ("D000003", "Cevap Dilekçesi", "2026-04-20", CEVAP_TYPO),
    ("D000004", "Karar İlamı", "2026-05-15", BAM),
    ("D000005", "Duruşma Zaptı", "2026-09-11", ZABIT),
    ("D000006", "Dosyaya Eklenecek Evrak - Ek 3", "2026-03-27", ATTACHED_DIVORCE),
    ("D000007", "Genel Müzekkere - Ek 10", "2026-10-06", ENFORCEMENT),
    ("D000008", "Genel Müzekkere - Ek 11", "2026-10-06", INDICTMENT),
    ("D000009", "Bilirkişi Raporu", "2026-08-01", EXPERT),
    ("D000010", "Dosyaya Eklenecek Evrak - Ek 4", "2026-03-27", UNKNOWN),
    ("D000011", "Vekaletname", "2026-03-23", None),
]


def _built():
    catalog = [{"ref": ref, "title": title, "date": day, "indexed": text is not None}
               for ref, title, day, text in CATALOG]
    texts = {ref: {"title": title, "date": day, "text": text} for ref, title, day, text in CATALOG if text}
    return build_parties(catalog, texts, case_no="2026/45", court="Örnekköy 3. Aile Mahkemesi")


def test_sides_counsel_history_bench_and_other_files():
    built = _built()
    sections = {section["key"]: section for section in built["sections"]}
    own = sections["own"]
    assert own["subtitle"] == "Örnekköy 3. Aile Mahkemesi · 2026/45", own
    assert [(i["dateText"], i["text"]) for i in own["items"]] == [("Davacı", "Murat Kaya"), ("Davalı", "Ayşe Demir")]
    murat, ayse = own["items"]
    lines = [line["text"] for line in murat["lines"]]
    # The mistyped "2025/45" petition and the upper-court decision count for this case.
    assert lines == ["Vekili: Av. Tuna Er · son kayıt 11.09.2026",
                     "Daha önce: Av. Selin Oral (vekili, son 15.05.2026)",
                     "İstinaf kanun yoluna başvuran"], lines
    assert murat["lines"][2]["refs"] == ["D000004"]
    assert [line["text"] for line in ayse["lines"]] == ["Vekili: Av. Kerem Yalın · son kayıt 11.09.2026"]
    # The attached divorce file's lawyers never become this case's counsel.
    assert "Can Su" not in str(own)

    bench = [(i["dateText"], i["text"], [line["text"] for line in i["lines"]]) for i in sections["bench"]["items"]]
    assert bench == [("Hakim", "Deniz Korkmaz · son kayıt 11.09.2026", ["Önceki: Zehra Çınar (son 27.03.2026)"]),
                     ("Bilirkişi", "Zeynep Arslan", [])], bench

    other = sections["other"]["items"]
    assert other[0]["dateText"] == "Dava" and other[0]["text"] == "2025/88 Esas · Örnekköy 3. Aile Mahkemesi", other[0]
    assert [line["text"] for line in other[0]["lines"]] == [
        "Davacı: Ayşe Demir (bu dosyada davalı)",
        "Davalı: Murat Kaya — vekilleri Av. Can Su, Av. Ali Veli (bu dosyada davacı)"], other[0]
    assert (other[1]["dateText"], other[1]["who"], other[1]["text"]) == (
        "Şüpheli", "Murat Kaya", "Soruşturma · 2025/5533 soruşturma · Örnekköy Cumhuriyet Başsavcılığı"), other[1]
    assert [line["text"] for line in other[1]["lines"]] == [
        "Müşteki: Örnek Bankası A.Ş.", "Suç: Muhafaza Görevini Kötüye Kullanma"], other[1]
    assert (other[2]["dateText"], other[2]["text"]) == ("Borçlu", "İcra takibi · 2025/39 · Örnekköy İcra Dairesi")
    assert [line["text"] for line in other[2]["lines"]] == ["Alacaklı: Örnek Varlık Yönetim A.Ş."], other[2]

    coverage = [item["text"] for item in sections["coverage"]["items"]]
    assert "1 ek evrakın hangi dosyaya ait olduğu künyesinden anlaşılamadı; taraf listesine katılmadı." in coverage
    assert "1 evrakın metni okunamadığı için (taranmış olabilir) künyesi taranamadı." in coverage
    assert "Vekaletname evrakı: 1" in coverage

    text = built["answer"]
    assert text.startswith("BU DOSYANIN TARAFLARI (Örnekköy 3. Aile Mahkemesi · 2026/45)\n- Davacı: Murat Kaya"), text
    assert "12345678950" not in text and "1234567890" not in text, "identifiers must never be shown"
    assert "Mah." not in text and "UETS" not in text, "addresses and UETS must never be shown"


def test_route_needs_no_provider():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        _archive_case(root, [("Tensip Zaptı", "2026-09-11", ".txt", TENSIP)])
        scope = build_archive_case_index(root, "Örnekköy 3. Aile Mahkemesi", "2026/77")["scopeId"]

        def no_provider():
            raise AssertionError("parties must not touch the AI provider")
        original = ai_provider.provider_status
        ai_provider.provider_status = no_provider
        try:
            result = ai_provider.answer_from_local_index(root, scope, "Tarafları göster", workflow_id="parties")
        finally:
            ai_provider.provider_status = original
        assert result["ok"] is True and result["providerCalled"] is False, result
        assert result["workflowId"] == "parties" and result["partiesSchema"] == "parties_v1", result
        own = result["partySections"][0]
        assert own["subtitle"] == "Örnekköy 3. Aile Mahkemesi · 2026/77", own
        assert [(i["dateText"], i["text"]) for i in own["items"]] == [("Davacı", "Ayşe Demirtaş"),
                                                                      ("Davalı", "Murat Kayaoğlu")], own
        assert own["items"][1]["status"]["label"] == "Vekil görünmüyor"
        assert "12345-67890-12345" not in result["answer"]
        assert parties_answer(root, "not-a-case")["errorCode"] == "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED"


if __name__ == "__main__":
    test_header_layouts_of_petitions_decisions_and_enforcement_letters()
    test_sides_counsel_history_bench_and_other_files()
    test_route_needs_no_provider()
    print("Parties regression: PASS")
