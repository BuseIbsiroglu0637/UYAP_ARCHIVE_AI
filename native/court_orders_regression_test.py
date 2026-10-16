"""Rule-based "Ara Kararlar ve Yapılacaklar".

All names, numbers and case details are synthetic.
"""
from pathlib import Path
import tempfile

import ai_provider
from ai_court_orders import _CLOSING, addressee, build_court_orders, classify_action, court_orders_answer
from ai_local_index import build_archive_case_index
from important_dates_regression_test import TENSIP, _archive_case

TODAY = (2026, 10, 14)


def test_each_decision_gets_its_action_and_addressee():
    cases = [
        ("Örnekköy 3. Aile Mahkemesi'nin 2025/88 Esas sayılı dosyasının istenmesine,", "request", "Mahkeme kalemi"),
        ("Sanık müdafiine adres bildirmesi için 1 haftalık süre verilmesine,", "party_task", "Sanık müdafii"),
        ("Davalı vekiline delillerini bildirmesi için 2 hafta kesin süre verilmesine,", "party_task", "Davalı vekili"),
        ("HMK 139. maddesi uyarınca tarafların delillerini sunmak üzere 2 hafta süre verilmesine,", "party_task",
         "Taraflar"),
        # The main verb comes first: the dismissal decides, the service is routine.
        ("Davacının ihtiyati tedbir talebinin REDDİNE, davacı vekilinin UETS adresine tebliğine,", "ruling", None),
        ("Taraflara davetiye çıkarılmasına,", "notice", "Mahkeme kalemi"),
        ("Bu nedenle duruşmanın 20/10/2026 günü saat 10:43'e ertelenmesine", "hearing", "Taraflar"),
        ("Dosyanın bilirkişiye tevdiine,", "expert", "Bilirkişi"),
        # A finding orders nothing.
        ("Müzekkerelere cevap verildiği, tebligatların tamamlandığı anlaşılmakla,", "finding", None),
    ]
    for text, action, who in cases:
        assert classify_action(text) == action, (text, classify_action(text))
        assert addressee(text, action) == who, (text, addressee(text, action))

    # The record's closing sentence, whole or shortened, is not part of the last decision.
    closings = {
        "Duruşmanın 13/10/2026 günü saat 10:00'a bırakılmasına,\nDosya üzerinde yapılan inceleme sonunda karar "
        "verildi. 11/08/2026": "Duruşmanın 13/10/2026 günü saat 10:00'a bırakılmasına",
        "Banka hesap numarasının bildirilmesi hususunun ihtarına, Dosya üzerinde yapılan inceleme sonucu…":
            "Banka hesap numarasının bildirilmesi hususunun ihtarına",
        "İstinaf yolu açık olmak üzere oybirliğiyle karar verildi. 29/06/2…": "İstinaf yolu açık olmak üzere",
        # An order to examine the file is not a closing sentence.
        "Dosya üzerinde inceleme yapılmasına": "Dosya üzerinde inceleme yapılmasına",
    }
    for raw, expected in closings.items():
        assert _CLOSING.sub("", raw).strip() == expected, (raw, _CLOSING.sub("", raw))


HEARING = """T.C.
ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ
DURUŞMA TUTANAĞI
CELSE NO : 2
DURUŞMA TARİHİ : 13/10/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Örnekköy 3. Aile Mahkemesi'nin 2025/88 Esas sayılı dosyasının istenmesine,
2- Sanık müdafiine adres bildirmesi için 1 haftalık süre verilmesine,
Bu nedenle duruşmanın 20/10/2026 günü saat 10:43'e ertelenmesine karar verildi.
Katip
"""

TENSIP_CEZA = """T.C.
ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ
TENSİP TUTANAĞI
TENSİP TARİHİ : 11/08/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Tapu Müdürlüğüne müzekkere yazılmasına,
2- Duruşmanın 13/10/2026 günü saat 10:00'a bırakılmasına,
Dosya üzerinde yapılan inceleme sonunda karar verildi. 11/08/2026
"""

ARA_KARAR = """T.C.
ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ
ARA KARAR
ARA KARAR TARİHİ : 01/07/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Katılan vekiline (12345-67890-12345) delillerini bildirmesi için 2 hafta kesin süre verilmesine,
2- Taraflara davetiye çıkarılmasına,
karar verildi. 01/07/2026
"""

TALIMAT = """T.C.
YANKÖY ASLİYE CEZA MAHKEMESİ
DURUŞMA TUTANAĞI
TALİMAT NO : 2026/7
CELSE NO : 1
DURUŞMA TARİHİ : 20/09/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Tanığın dinlenmesine,
Bu nedenle duruşmanın 30/09/2026 günü saat 11:00'e bırakılmasına karar verildi.
"""

CATALOG = [
    ("D000001", "Ara Karar Evrakı", "2026-07-01"),
    ("D000002", "Kapalı E-Tebliğ Mazbatası", "2026-07-05"),
    ("D000003", "Delil Dilekçesi", "2026-07-10"),
    ("D000004", "Tensip Zaptı", "2026-08-11"),
    ("D000005", "Genel Müzekkere", "2026-08-12"),
    ("D000006", "Tapu Müdürlüğü Cevap Yazısı", "2026-08-30"),
    ("D000007", "Talimat Duruşma Zaptı", "2026-09-20"),
    ("D000008", "Duruşma Zaptı", "2026-10-13"),
    ("D000009", "Dosya İsteme Yazısı", "2026-10-13"),
    ("D000010", "Ara Karar Evrakı", "2026-10-14"),        # scanned: no text
]
TEXTS = {"D000001": ARA_KARAR, "D000004": TENSIP_CEZA, "D000007": TALIMAT, "D000008": HEARING}


def _built():
    catalog = [{"ref": ref, "title": title, "date": day, "indexed": ref in TEXTS} for ref, title, day in CATALOG]
    texts = {ref: {"title": next(c[1] for c in CATALOG if c[0] == ref), "date": None, "text": text}
             for ref, text in TEXTS.items()}
    return build_court_orders(catalog, texts, today=TODAY)


def _items(section):
    return [(item["who"], item["status"]["label"] if item.get("status") else None, item["kind"])
            for item in section["items"]]


def test_latest_own_records_with_status_and_related_documents():
    built = _built()
    sections = built["sections"]
    keys = [section["key"] for section in sections]
    # The case's own three records; the newer talimat record only fills up.
    assert keys == ["open", "record-D000008", "record-D000004", "record-D000001", "coverage"], keys
    hearing, tensip, interim = sections[1], sections[2], sections[3]
    assert hearing["title"] == "13.10.2026 · Duruşma Zaptı — 2. celse" and hearing["refs"] == ["D000008"], hearing

    assert _items(hearing) == [("Mahkeme kalemi", "Cevap bekleniyor", "request"),
                               ("Sanık müdafii", "Süre işliyor", "party_task"),
                               ("Taraflar", "6 gün sonra", "hearing")], _items(hearing)
    request = hearing["items"][0]
    assert request["lines"][0]["text"] == "Yazı gönderilmiş: Dosya İsteme Yazısı · 13.10.2026", request["lines"]
    assert request["lines"][0]["refs"] == ["D000009"]
    task = hearing["items"][1]
    assert task["lines"][0]["text"] == "Süre: 1 hafta — en erken bitiş 20.10.2026", task["lines"]

    # Evidence is looked for only until the next own record.
    assert _items(tensip) == [("Mahkeme kalemi", "Cevap gelmiş", "request"),
                              ("Taraflar", "Duruşma kaydı var", "hearing")], _items(tensip)
    letters = [line["text"] for line in tensip["items"][0]["lines"]]
    assert letters == ["Yazı gönderilmiş: Genel Müzekkere · 12.08.2026",
                       "Gelen evrak / cevap: Tapu Müdürlüğü Cevap Yazısı · 30.08.2026"], letters
    assert tensip["items"][1]["lines"][0]["refs"] == ["D000008"], tensip["items"][1]
    # The record's closing sentence is not part of the last decision.
    assert tensip["items"][1]["text"] == "Duruşmanın 13/10/2026 günü saat 10:00'a bırakılmasına", tensip["items"][1]

    assert _items(interim) == [("Katılan vekili", "İlgili evrak var", "party_task"),
                               ("Mahkeme kalemi", "Tebligat kaydı var", "notice")], _items(interim)
    first = [line["text"] for line in interim["items"][0]["lines"]]
    assert first == ["Süre: 2 hafta kesin — en erken bitiş 15.07.2026",
                     "Dilekçe gelmiş: Delil Dilekçesi · 10.07.2026"], first

    open_items = sections[0]["items"]
    assert [(item["dateText"], item["status"]["label"]) for item in open_items] == [
        ("14.10.2026", "Okunamadı"), ("13.10.2026", "Cevap bekleniyor"), ("20.10.2026", "Süre işliyor")], open_items
    assert open_items[0]["refs"] == ["D000010"] and "metni okunamadı" in open_items[0]["text"]
    assert open_items[2]["text"].startswith("Sanık müdafii — Sanık müdafiine adres bildirmesi"), open_items[2]

    text = built["answer"]
    assert text.startswith("AÇIK İŞLER\n"), text
    assert "12345-67890-12345" not in text, "identifiers must be masked"
    assert "Talimat duruşması" not in text
    assert "D000007" not in built["refs"] and {"D000008", "D000009", "D000010"} <= set(built["refs"])


def test_route_needs_no_provider_and_keeps_the_legacy_id():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        _archive_case(root, [
            ("Tensip Zaptı", "2026-09-11", ".txt", TENSIP),
            ("Genel Müzekkere", "2026-09-12", ".txt", "Bankalara yazılan müzekkere."),
        ])
        scope = build_archive_case_index(root, "Örnekköy 3. Aile Mahkemesi", "2026/77")["scopeId"]

        def no_provider():
            raise AssertionError("court orders must not touch the AI provider")
        original = ai_provider.provider_status
        ai_provider.provider_status = no_provider
        try:
            result = ai_provider.answer_from_local_index(root, scope, "Ara kararları göster", workflow_id="court_orders")
        finally:
            ai_provider.provider_status = original
        assert result["ok"] is True and result["providerCalled"] is False, result
        assert result["workflowId"] == "court_orders" and result["ordersSchema"] == "court_orders_v1", result
        assert result["workflowTitle"] == "Ara Kararlar ve Yapılacaklar"
        record = next(s for s in result["orderSections"] if s["key"].startswith("record-"))
        assert record["title"] == "11.09.2026 · Tensip Zaptı", record
        first = record["items"][0]
        assert (first["who"], first["status"]["label"]) == ("Mahkeme kalemi", "Cevap bekleniyor"), first
        assert first["lines"][0]["text"] == "Yazı gönderilmiş: Genel Müzekkere · 12.09.2026", first["lines"]
        assert all(source["sourceRef"] in result["sourceRefs"] for source in result["sources"])
        assert court_orders_answer(root, "not-a-case")["errorCode"] == "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED"
        # The removed card's id still resolves for older clients.
        assert "new_updates" in ai_provider._LEGACY_WORKFLOW_TASKS


if __name__ == "__main__":
    test_each_decision_gets_its_action_and_addressee()
    test_latest_own_records_with_status_and_related_documents()
    test_route_needs_no_provider_and_keeps_the_legacy_id()
    print("Court orders regression: PASS")
