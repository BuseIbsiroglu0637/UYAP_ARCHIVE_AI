"""Rule-based "Önemli Tarihler", the case catalog and the scanned-PDF fast path.

All names, numbers and case details are synthetic.
"""
from pathlib import Path
import hashlib
import json
import sqlite3
import tempfile
import time
import zlib

import ai_provider
from ai_important_dates import build_important_dates, classify_title, important_dates_answer, time_limits
from ai_local_index import _index_db, _pdf_text, archive_case_scope_id, build_archive_case_index
from datetime import date

TODAY = (2026, 10, 14)


def test_titles_map_to_events_in_the_right_order():
    expected = {
        "İstinaf Başvuru Dilekçesi": "appeal",          # not the opening petition
        "Bam Dosya Gönderme Kontrol Formu": "appeal_sent",
        "Ara Karar Evrakı": "interim",                  # not the judgment
        "Karar İlamı": "decision",
        "Tensip Zaptı": "tensip",
        "Duruşma Zaptı 13.10.2026": "hearing",
        "Hukuk Genel Tutanak": "minutes",
        "Dava Dilekçesi": "opening",
        "Cevaba Cevap Dilekçesi": "reply",
        "Delil Dilekçesi": "petition",
        "Kapalı E-Tebliğ Mazbatası 20_08_2026": "notice",
        "Müzekkere Cevabı": "letter_in",                # incoming, not outgoing
        "Kurum ve Kuruluşlardan Gelen Cevap Yazısı - Ek 10": "letter_in",
        "Genel Müzekkere": "letter_out",
        "Reddiyat Makbuzu": "low",
        "Vekaletname": "low",
    }
    for title, kind in expected.items():
        assert classify_title(title)[0] == kind, (title, classify_title(title))
    assert classify_title("Kapalı E-Tebliğ Mazbatası 20_08_2026")[1] == "Kapalı E-Tebliğ Mazbatası"


def test_time_limits_give_only_the_earliest_possible_end():
    decided = date(2026, 10, 13)
    one_week = time_limits("2- Sanık müdafiine adres bildirmesi için 1 haftalık süre verilmesine,", decided)
    assert len(one_week) == 1 and one_week[0]["earliestEnd"] == date(2026, 10, 20), one_week
    assert one_week[0]["clause"].startswith("2- Sanık müdafiine adres"), one_week
    kesin = time_limits("Davalı vekiline 2 HAFTALIK KESİN SÜRE verilmesine", decided)
    assert kesin[0]["kesin"] is True and kesin[0]["earliestEnd"] == date(2026, 10, 27), kesin
    remedy = time_limits("Dosya üzerinden tebliğden itibaren 2 hafta içinde Bölge Adliye Mahkemesine İSTİNAF yolu "
                         "açık olmak üzere karar verildi.", date(2026, 3, 27))
    assert remedy[0]["remedy"] == "istinaf" and remedy[0]["earliestEnd"] == date(2026, 4, 10), remedy
    month = time_limits("itiraz için bir ay içinde başvurulabilir", date(2026, 1, 31))
    assert month[0]["earliestEnd"] == date(2026, 2, 28), month


TENSIP = """T.C.
ÖRNEKKÖY 3. AİLE MAHKEMESİ
TENSİP TUTANAĞI
ESAS NO : 2026/77 Esas
DAVACI : AYŞE DEMİRTAŞ - [12345-67890-12345]
DAVALI : MURAT KAYAOĞLU
DAVA TARİHİ : 23/03/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Bankalara müzekkere yazılmasına,
2- Ön inceleme duruşmasının 11/11/2026 günü saat 10:50'de yapılmasına, taraflara davetiye çıkarılmasına,
Dosya üzerinde yapılan inceleme sonunda karar verildi. 11/09/2026
Katip
Hakim
"""

ARA_KARAR = """T.C.
ÖRNEKKÖY 3. AİLE MAHKEMESİ
ARA KARAR
ESAS NO : 2026/77 Esas
DAVA TARİHİ : 23/03/2026
ARA KARAR TARİHİ : 27/03/2026
GEREĞİ DÜŞÜNÜLDÜ:
Hüküm: Gerekçesi yukarıda açıklandığı üzere;
1-Davacının İHTİYATİ TEDBİR TALEBİNİN REDDİNE, davacı vekilinin UETS adresine (12345-67890-12345) tebliğine,
2-Dosya üzerinden tebliğden itibaren 2 hafta içinde Bölge Adliye Mahkemesine İSTİNAF yolu açık olmak üzere karar verildi. 27/03/2026
Katip
Hakim
"""

BAM = """T.C.
BÖLGE ADLİYE MAHKEMESİ
12. HUKUK DAİRESİ
ESAS NO : 2026/400
KARAR NO : 2026/900
KARAR TARİHİ : 15/05/2026
Davacı vekilinin istinaf başvurusunun esastan reddine, kesin olmak üzere oybirliği ile karar verildi. 15/05/2026
"""

CATALOG = [
    ("D000001", "Dava Dilekçesi", "2026-03-23", False),
    ("D000002", "Dava Dilekçesi", "2026-03-23", False),
    ("D000003", "Ara Karar Evrakı", "2026-03-27", True),
    ("D000004", "Reddiyat Makbuzu", "2026-03-28", False),
    ("D000005", "İstinaf Başvuru Dilekçesi", "2026-04-09", False),
    ("D000006", "Bam Dosya Gönderme Kontrol Formu", "2026-04-27", False),
    ("D000007", "Karar İlamı", "2026-05-22", True),
    ("D000008", "Cevaba Cevap Dilekçesi", "2026-06-01", False),
    ("D000009", "Tensip Zaptı", "2026-09-11", True),
    ("D000010", "Kapalı E-Tebliğ Mazbatası", "2026-09-18", False),
    ("D000011", "Kapalı E-Tebliğ Mazbatası", "2026-09-18", False),
    ("D000012", "Genel Müzekkere", "2026-10-13", False),
    ("D000013", "Genel Müzekkere", "2026-10-13", False),
    ("D000014", "Genel Müzekkere", "2026-10-13", False),
]
TEXTS = {"D000003": ARA_KARAR, "D000007": BAM, "D000009": TENSIP}


def _built():
    catalog = [{"ref": ref, "title": title, "date": day, "indexed": indexed} for ref, title, day, indexed in CATALOG]
    texts = {ref: {"title": next(c[1] for c in CATALOG if c[0] == ref), "date": None, "text": text}
             for ref, text in TEXTS.items()}
    return build_important_dates(catalog, texts, today=TODAY, total_documents=len(CATALOG))


def test_upcoming_recent_and_milestones_for_a_family_court_case():
    built = _built()
    sections = {section["key"]: section for section in built["sections"]}
    upcoming = sections["upcoming"]["items"]
    assert upcoming and upcoming[0]["dateText"] == "11.11.2026 saat 10:50", upcoming
    assert upcoming[0]["text"] == "Ön inceleme duruşması — 28 gün sonra" and upcoming[0]["refs"] == ["D000009"]
    # The March istinaf window ended long ago, so it is not "upcoming".
    assert not any(item["kind"] == "limit" for item in upcoming), upcoming

    recent = [(item["dateText"], item["text"], item["refs"]) for item in sections["recent"]["items"]]
    assert recent[0] == ("13.10.2026", "Müzekkere / yazı gönderildi (3)", ["D000012", "D000013", "D000014"]), recent
    assert ("18.09.2026", "Kapalı E-Tebliğ Mazbatası (2)", ["D000010", "D000011"]) in recent, recent
    assert any(text == "Tensip Zaptı" and day == "11.09.2026" for day, text, _ in recent), recent

    milestones = [(item["dateText"], item["text"], item["refs"]) for item in sections["milestones"]["items"]]
    assert milestones[0] == ("23.03.2026", "Dava açıldı — dava dilekçesi", ["D000001", "D000002", "D000003"]), milestones
    assert ("27.03.2026", "Ara karar — Davacının İhtiyati Tedbir Talebinin Reddine", ["D000003"]) in [
        (d, t.split(",")[0], r) for d, t, r in milestones], milestones
    assert ("09.04.2026", "İstinaf başvurusu", ["D000005"]) in milestones
    assert ("27.04.2026", "Dosya üst mahkemeye gönderildi", ["D000006"]) in milestones
    # The BAM decision is dated by its own KARAR TARİHİ, not the UYAP upload date.
    assert ("15.05.2026", "Bölge Adliye Mahkemesi kararı — kesin", ["D000007"]) in milestones, milestones
    assert ("01.06.2026", "Cevaba Cevap Dilekçesi", ["D000008"]) in milestones

    text = built["answer"]
    assert text.startswith("YAKLAŞAN\n- 11.11.2026 saat 10:50 · Ön inceleme duruşması — 28 gün sonra [D000009]"), text
    assert "SON İŞLEMLER (" in text and "KİLOMETRE TAŞLARI" in text and "KAPSAM" in text
    assert "Reddiyat" not in text and "1 mali / idari kayıt" in text
    assert "14 evrakın tamamı başlık ve UYAP tarihiyle, 3 tanesi metniyle tarandı." in text
    assert "12345-67890-12345" not in text, "identifiers must be masked"


CEZA_HEARING = """T.C.
ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ
DURUŞMA TUTANAĞI
CELSE NO : 1
DURUŞMA TARİHİ : 13/10/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Örnekköy 3. Aile Mahkemesi'nin 2025/88 Esas sayılı dosyasının istenmesine,
2- Sanık müdafiine adres bildirmesi için 1 haftalık süre verilmesine,
Bu nedenle duruşmanın 20/10/2026 günü saat 10:43'e ertelenmesine karar verildi.
Katip
"""

TALIMAT = """T.C.
YANKÖY ASLİYE CEZA MAHKEMESİ
DURUŞMA TUTANAĞI
TALİMAT NO : 2026/7
CELSE NO : 1
DURUŞMA TARİHİ : 20/10/2026
GEREĞİ DÜŞÜNÜLDÜ:
1- Talimatın iadesine,
Bu nedenle duruşmanın 30/10/2026 günü saat 11:00'e bırakılmasına karar verildi.
"""


def test_talimat_hearing_is_not_the_next_hearing_and_limits_are_upcoming():
    catalog = [{"ref": "D000001", "title": "Duruşma Zaptı", "date": "2026-10-13", "indexed": True},
               {"ref": "D000002", "title": "Talimat Duruşma Zaptı", "date": "2026-10-20", "indexed": True}]
    texts = {"D000001": {"title": "Duruşma Zaptı", "date": None, "text": CEZA_HEARING},
             "D000002": {"title": "Talimat Duruşma Zaptı", "date": None, "text": TALIMAT}}
    built = build_important_dates(catalog, texts, today=TODAY)
    upcoming = built["sections"][0]["items"]
    assert upcoming[0]["dateText"] == "20.10.2026 saat 10:43" and upcoming[0]["text"] == "Duruşma — 6 gün sonra", upcoming
    limit = next(item for item in upcoming if item["kind"] == "limit")
    assert limit["dateText"] == "20.10.2026", limit
    assert limit["text"].startswith("Süre en erken biter — Sanık müdafiine adres bildirmesi için 1 haftalık süre"), limit
    assert limit["text"].endswith("(13.10.2026 kararından sayıldı)"), limit
    recent = built["sections"][1]["items"]
    assert any(item["text"].startswith("Talimat duruşması (Yanköy Asliye Ceza Mahkemesi)") for item in recent), recent

    # After the hearing day has passed without a newer record, say so.
    later = build_important_dates(catalog[:1], {"D000001": texts["D000001"]}, today=(2026, 10, 25))
    stale = later["sections"][0]["items"]
    assert stale[0]["kind"] == "stale" and stale[0]["dateText"] == "20.10.2026 saat 10:43", stale


def _archive_case(root, documents):
    """Synthetic archive DB + files, like a finished UYAP download."""
    archive = root / "UYAP_Arsiv"
    folder = archive / "Örnekköy 3. Aile Mahkemesi" / "2026_77_Dosya" / "2026_77"
    folder.mkdir(parents=True)
    db = sqlite3.connect(archive / "_archive.db")
    db.execute("""CREATE TABLE documents(session_id TEXT, document_id TEXT, case_id TEXT, display_name TEXT,
        uyap_path TEXT, local_path TEXT, size INTEGER, sha256 TEXT, status TEXT, updated_at TEXT,
        institution_name TEXT, target_path TEXT, document_date TEXT, source_system TEXT)""")
    for index, (name, day, extension, content) in enumerate(documents, start=1):
        path = folder / f"evrak{index}{extension}"
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        data = path.read_bytes()
        db.execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            "s", f"doc-{index}", "2026/77", name, f"K/{path.name}", str(path), len(data),
            hashlib.sha256(data).hexdigest(), "VERIFIED", f"{day}T10:00:00Z", "Örnekköy 3. Aile Mahkemesi",
            str(path.relative_to(archive)).replace("\\", "/"), day, "UYAP"))
    db.commit()
    db.close()


def test_index_build_records_every_document_and_answer_needs_no_provider():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        _archive_case(root, [
            ("Tensip Zaptı", "2026-09-11", ".txt", TENSIP),
            ("Kapalı E-Tebliğ Mazbatası", "2026-09-18", ".jpg", b"\xff\xd8\xff\xe0" + bytes(64) + b"\xff\xd9"),
            ("Genel Müzekkere", "2026-10-13", ".txt", "Bankalara yazılan müzekkere."),
        ])
        built = build_archive_case_index(root, "Örnekköy 3. Aile Mahkemesi", "2026/77")
        assert built["readyForAi"] is True and built["indexedDocuments"] == 2, built
        scope = built["scopeId"]
        db, _ = _index_db(root, scope)
        try:
            catalog = json.loads(db.execute("SELECT value FROM ai_meta WHERE key='case_catalog'").fetchone()[0])
        finally:
            db.close()
        assert catalog["totalDocuments"] == 3 and catalog["caseId"] == "2026/77", catalog
        assert [(d["title"], d["indexed"]) for d in catalog["documents"]] == [
            ("Tensip Zaptı", True), ("Kapalı E-Tebliğ Mazbatası", False), ("Genel Müzekkere", True)], catalog

        def no_provider():
            raise AssertionError("important dates must not touch the AI provider")
        original = ai_provider.provider_status
        ai_provider.provider_status = no_provider
        try:
            result = ai_provider.answer_from_local_index(root, scope, "Önemli tarihleri çıkar", workflow_id="important_dates")
        finally:
            ai_provider.provider_status = original
        assert result["ok"] is True and result["providerCalled"] is False, result
        assert result["datesSchema"] == "important_dates_v2" and result["provider"] == "Yerel kurallar"
        # The unreadable tebligat still appears, dated by its UYAP date.
        assert "18.09.2026 · Kapalı E-Tebliğ Mazbatası [D000002]" in result["answer"], result["answer"]
        assert "3 evrakın tamamı başlık ve UYAP tarihiyle, 2 tanesi metniyle tarandı." in result["answer"]
        assert set(result["sourceRefs"]) <= {"D000001", "D000002", "D000003"}
        assert all(source["sourceRef"] in result["sourceRefs"] for source in result["sources"])

        # An index built before the catalog existed still works and says what is missing.
        db, _ = _index_db(root, scope)
        try:
            db.execute("DELETE FROM ai_meta")
            db.commit()
        finally:
            db.close()
        legacy = important_dates_answer(root, scope, today=TODAY)
        assert legacy["ok"] is True and "Tam dava indeksini güncelle" in legacy["answer"], legacy["answer"]
        assert important_dates_answer(root, "not-a-case")["errorCode"] == "AI_WORKFLOW_ARCHIVE_CASE_REQUIRED"


def _pdf(objects):
    body = b"%PDF-1.4\n"
    for number, (header, payload) in enumerate(objects, start=1):
        body += f"{number} 0 obj\n".encode() + header + b"\nstream\n" + payload + b"\nendstream\nendobj\n"
    return body + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"


def test_scanned_pdf_image_data_is_skipped_and_text_still_found():
    content = b"BT /F1 12 Tf (Ornek dava tutanagi metni) Tj ET"
    flate = zlib.compress(content)
    # A "scan" whose image bytes happen to contain text operators.
    image = (b"BT [(SAHTE)] TJ ET (" * 4000) + bytes(range(256)) * 400
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "taranmis.pdf"
        path.write_bytes(_pdf([
            (b"<< /Type /XObject /Subtype /Image /Filter /DCTDecode /Length %d >>" % len(image), image),
            (b"<< /Filter /FlateDecode /Length %d >>" % len(flate), flate),
        ]))
        started = time.monotonic()
        text = _pdf_text(path)
        assert time.monotonic() - started < 5, "image streams must not be scanned"
        assert "Ornek dava tutanagi metni" in text and "SAHTE" not in text, text[:200]


if __name__ == "__main__":
    test_titles_map_to_events_in_the_right_order()
    test_time_limits_give_only_the_earliest_possible_end()
    test_upcoming_recent_and_milestones_for_a_family_court_case()
    test_talimat_hearing_is_not_the_next_hearing_and_limits_are_upcoming()
    test_index_build_records_every_document_and_answer_needs_no_provider()
    test_scanned_pdf_image_data_is_skipped_and_text_still_found()
    print("Important dates regression: PASS")
