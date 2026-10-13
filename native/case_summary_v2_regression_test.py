"""Case summary v2: rule-based facts, role-aware selection, validated narrative.

All names, numbers and case details below are synthetic.
"""
from pathlib import Path
import hashlib
import sqlite3
import tempfile

import ai_provider
from ai_case_facts import (
    extract_case_facts,
    find_dates,
    mask_identifiers,
    render_fact_sections,
    title_document_role,
    SECTION_KUNYE,
    SECTION_LAST_HEARING,
    SECTION_NEXT,
)
from ai_local_index import build_archive_case_index, case_summary_facts, fit_snippet, summary_context


INSTITUTION = "Örnekköy 4. Asliye Ceza Mahkemesi"
CASE_ID = "2026/45"

TENSIP = """T.C.
ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ
TENSİP ZAPTI
ESAS NO : 2026/45 Esas
MÜŞTEKİ : AYŞE DEMİRTAŞ, Ali kızı, 1980 doğumlu
VEKİLİ : Av. KEREM YALIN
SANIK : MURAT KAYAOĞLU, Hasan oğlu, TCKN 12345678950
SUÇ : NAFAKA HÜKÜMLERİNE UYMAMAK
SUÇ TARİHİ : 15/01/2026
Dosya incelendi.
GEREĞİ DÜŞÜNÜLDÜ:
1- İddianamenin kabulüne,
2- Sanığa iddianamenin tebliğine,
Bu nedenle duruşmanın 14/04/2026 günü saat 10:30'a bırakılmasına karar verildi.
Katip
Hakim
"""

SAVUNMA = """ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ'NE
SANIK : MURAT KAYAOĞLU
MÜDAFİİ : Av. SELİN ORAL
KONU : Savunmalarımızın sunulmasıdır.
Müvekkil nafaka borcunu işsizlik nedeniyle ödeyemediğini, ödeme planı teklif ettiğini beyan etmektedir.
Müvekkilin beraatine karar verilmesini talep ederiz.
"""

MAZERET = """ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ'NE
SANIK MÜDAFİİ : Av. SELİN ORAL
Aynı gün başka bir mahkemede duruşmam bulunduğundan mazeretimin kabulünü ve duruşma gününün
sistem üzerinden öğrenilmesine izin verilmesini talep ederim.
"""

DURUSMA = """T.C.
ÖRNEKKÖY 4. ASLİYE CEZA MAHKEMESİ
DURUŞMA TUTANAĞI
ESAS NO : 2026/45 Esas
CELSE NO : 1
DURUŞMA TARİHİ : 14/04/2026
MÜŞTEKİ : AYŞE DEMİRTAŞ
MÜŞTEKİ VEKİLİ : Av. KEREM YALIN
SANIK : MURAT KAYAOĞLU
SANIK MÜDAFİİ : Av. SELİN ORAL
Belli edilen gün ve saatte duruşma açıldı.
Müşteki vekili Av. KEREM YALIN geldi. Sanık müdafii tarafından mazeret dilekçesi gönderildiği görüldü.
GEREĞİ DÜŞÜNÜLDÜ:
1- Örnekköy 3. Aile Mahkemesi'nin 2025/88 Esas sayılı dosyasının istenmesine,
2- Sanık müdafiine adres bildirmesi için 2 haftalık kesin süre verilmesine, UETS adresine (12345-67890-12345) tebliğine,
3- Sanık müdafiinin mazeretinin kabulüne,
Bu nedenle duruşmanın 19/05/2026 günü saat 09:15'e ertelenmesine karar verildi.
Katip
Hakim
"""

TALIMAT = """T.C.
YANKÖY ASLİYE CEZA MAHKEMESİ
DURUŞMA TUTANAĞI
TALİMAT NO : 2026/7
CELSE NO : 1
DURUŞMA TARİHİ : 20/04/2026
Sanık gelmedi. Başka gelen olmadı.
GEREĞİ DÜŞÜNÜLDÜ:
1- Talimatın mahal mahkemesine iadesine,
Bu nedenle duruşmanın 30/04/2026 günü saat 11:00'e bırakılmasına karar verildi.
Katip
"""

MAKBUZ = "Tahsilat makbuzu. Tahsil edilen tutar: 615,40 TL. Ödeme yapan: vekil."

# (display name, date, extension, content) — dates fix the D00000x order.
DOCUMENTS = (
    ("Tensip Zaptı", "2026-02-10", ".txt", TENSIP),            # D000001
    ("Tahsilat Makbuzu", "2026-02-15", ".txt", MAKBUZ),        # D000002
    ("Savunma Dilekçesi", "2026-03-20", ".txt", SAVUNMA),      # D000003
    ("Bilirkişi Raporu", "2026-04-01", ".jpg", None),          # D000004 (image, no OCR)
    ("Mazeret Dilekçesi", "2026-04-13", ".txt", MAZERET),      # D000005
    ("Duruşma Zaptı", "2026-04-14", ".txt", DURUSMA),          # D000006
    ("Talimat Duruşma Zaptı", "2026-04-20", ".txt", TALIMAT),  # D000007
)


def build_case(root):
    archive = root / "UYAP_Arsiv"
    case_folder = archive / INSTITUTION / "2026_45_Dosya" / "2026_45"
    case_folder.mkdir(parents=True)
    db = sqlite3.connect(archive / "_archive.db")
    db.execute(
        """CREATE TABLE documents(
            session_id TEXT, document_id TEXT, case_id TEXT, display_name TEXT,
            uyap_path TEXT, local_path TEXT, size INTEGER, sha256 TEXT, status TEXT,
            updated_at TEXT, institution_name TEXT, target_path TEXT, document_date TEXT,
            source_system TEXT
        )"""
    )
    for index, (name, date, extension, content) in enumerate(DOCUMENTS, start=1):
        path = case_folder / f"evrak{index}{extension}"
        if content is None:
            path.write_bytes(b"\xff\xd8\xff\xe0" + bytes(range(64)) + b"\xff\xd9")
        else:
            path.write_text(content, encoding="utf-8")
        data = path.read_bytes()
        db.execute(
            "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "session-1", f"doc-{index}", CASE_ID, name, f"Klasör/{path.name}", str(path), len(data),
                hashlib.sha256(data).hexdigest(), "VERIFIED", f"{date}T10:00:00Z", INSTITUTION,
                str(path.relative_to(archive)).replace("\\", "/"), date, "UYAP",
            ),
        )
    db.commit()
    db.close()
    built = build_archive_case_index(root, INSTITUTION, CASE_ID)
    assert built["readyForAi"] is True, built
    assert built["indexedDocuments"] == 6, built
    return built["scopeId"]


def test_title_decides_document_role():
    assert title_document_role("Duruşma Zaptı")[0] == "procedural"
    assert title_document_role("Gerekçeli Karar")[0] == "final_decision"
    assert title_document_role("Mazeret Dilekçesi")[0] == "response"
    assert title_document_role("Tahsilat Makbuzu")[0] == "low_value"
    # A hearing record that mentions "talimat" is still a hearing record.
    assert title_document_role("Talimat Duruşma Zaptı")[0] == "procedural"


def test_masking_hides_identity_and_notification_numbers():
    masked, count = mask_identifiers("TCKN 12345678950, UETS 12345-67890-12345, IBAN TR12 0006 4000 0011 2345 6789 01")
    assert count == 3, (masked, count)
    assert "12345678950" not in masked and "67890" not in masked and "0006 4000" not in masked, masked
    # Case numbers and dates are not identifiers.
    assert mask_identifiers("2026/45 Esas, 14/04/2026")[1] == 0


def test_role_check_folds_turkish_capitals():
    # "SANIK".casefold() == "sanik": a plain casefold rejected every answer
    # whose source wrote the role in an all-caps header.
    assert ai_provider._unsupported_roles("Sanık müdafii mazeret bildirdi.", "SANIK MÜDAFİİ : Av. X") == []
    assert ai_provider._unsupported_roles("Müşteki vekili geldi.", "MÜŞTEKİ VEKİLİ : Av. Y") == []
    assert ai_provider._unsupported_roles("Bilirkişi raporu geldi.", "SANIK : Z") == ["bilirkişi"]


def test_fit_snippet_keeps_the_decision_block_of_a_long_record():
    filler = "Tanık beyanı alındı ve okundu. " * 120
    text = "DURUŞMA TUTANAĞI\nCELSE NO : 3\n" + filler + "GEREĞİ DÜŞÜNÜLDÜ:\n1- Duruşmanın 02/06/2026 günü saat 10:00'a bırakılmasına,"
    snippet = fit_snippet(text, 1400)
    assert len(snippet) <= 1400, len(snippet)
    assert "DURUŞMA TUTANAĞI" in snippet
    assert "02/06/2026" in snippet, "decision block must survive truncation"


def test_rule_based_facts_from_synthetic_case():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        scope = build_case(root)
        payload = case_summary_facts(root, scope, INSTITUTION, CASE_ID, today=(2026, 5, 1))
        facts = payload["facts"]
        assert facts["caseNo"] == "2026/45"
        assert facts["subject"]["text"] == "Nafaka Hükümlerine Uymamak", facts["subject"]
        assert facts["subjectDate"]["date"] == (2026, 1, 15)
        roles = {(party["role"], party["name"]): [c["name"] for c in party["counsel"]] for party in facts["parties"]}
        assert roles[("Müşteki", "Ayşe Demirtaş")] == ["Av. Kerem Yalın"], roles
        assert roles[("Sanık", "Murat Kayaoğlu")] == ["Av. Selin Oral"], roles

        last = facts["lastHearing"]
        assert last["ref"] == "D000006" and last["date"] == (2026, 4, 14) and last["celse"] == 1, last
        assert len(last["decisions"]) == 3, last["decisions"]
        assert any("Av. KEREM YALIN geldi" in line for line in last["attendance"]), last["attendance"]

        upcoming = facts["nextHearing"]
        # The talimat court's 30/04/2026 hearing is not this case's next hearing.
        assert upcoming["date"] == (2026, 5, 19) and upcoming["time"] == "09:15", upcoming
        assert upcoming["ref"] == "D000006" and upcoming["past"] is False, upcoming
        assert len(facts["deadlines"]) == 1 and facts["deadlines"][0]["kesin"] is True, facts["deadlines"]
        assert "adres bildirmesi için 2 haftalık kesin süre" in facts["deadlines"][0]["text"]
        assert "tebliğine" not in facts["deadlines"][0]["text"], "deadline line must not repeat the whole decision"
        assert [item["text"] for item in facts["linkedFiles"]] == ["Örnekköy 3. Aile Mahkemesi 2025/88 Esas"]

        labels = [entry["label"] for entry in facts["timeline"]]
        assert any("talimat duruşma günü 30/04/2026" in label for label in labels), labels
        assert not any("Makbuz" in label for label in labels), labels

        coverage = payload["coverage"]
        assert coverage["totalDocuments"] == 7 and coverage["indexedDocuments"] == 6, coverage
        assert [item["title"] for item in coverage["notIndexedSamples"]] == ["Bilirkişi Raporu"], coverage

        sections = render_fact_sections(facts)
        for heading in (SECTION_KUNYE, SECTION_LAST_HEARING, SECTION_NEXT):
            assert sections[heading], heading
            for line in sections[heading]:
                # Court / case number come from the case identity, not a document.
                assert line.endswith("]") or line.startswith(("Not:", "Mahkeme / dosya:")), f"untagged fact line: {line}"


def test_selection_prefers_records_over_receipts():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        scope = build_case(root)
        context = summary_context(root, scope, limit=4, snippet_chars=1400)
        refs = [row["sourceRef"] for row in context["results"]]
        assert "D000002" not in refs, "a receipt must not take a slot from a record"
        assert "D000006" in refs and "D000001" in refs, refs
        hearing = next(row for row in context["results"] if row["sourceRef"] == "D000006")
        assert "19/05/2026" in hearing["snippet"]


def test_narrative_validation_drops_unsupported_and_redundant_items():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        scope = build_case(root)
        facts = case_summary_facts(root, scope, INSTITUTION, CASE_ID, today=(2026, 5, 1))["facts"]
        snippets = {row["sourceRef"]: row["snippet"] for row in summary_context(root, scope, 6, 1400)["results"]}
        allowed = set(snippets)
        parsed = {
            "durum": {"metin": "Sanık nafaka borcunu işsizlik nedeniyle ödeyemediğini savunmaktadır.", "kaynaklar": ["D000003"]},
            "gelismeler": [
                {"metin": "Sonraki duruşma 19/05/2026 saat 09:15 olarak belirlendi.", "kaynaklar": ["D000006"]},
                {"metin": "Sanık müdafii mazeret dilekçesi sunarak duruşma gününün sistem üzerinden öğrenilmesini talep etti.",
                 "kaynaklar": ["D000005", "D000001"]},
                {"metin": "Sanık müdafii mazeret dilekçesi vererek duruşma gününün sistem üzerinden öğrenilmesini talep etti.",
                 "kaynaklar": ["D000005"]},
                {"metin": "Duruşma 25/03/2026 tarihinde yapıldı ve tanıklar dinlendi.", "kaynaklar": ["D000006"]},
                {"metin": "Bilirkişi raporu dosyaya sunuldu.", "kaynaklar": ["D000099"]},
                {"metin": "Müşteki vekili Av. Selin Oral duruşmaya katıldı ve beyanda bulundu.", "kaynaklar": ["D000006"]},
                {"metin": "Duruşmada ne olduğu belirtilmemiştir.", "kaynaklar": ["D000006"]},
                {"metin": "Duruşma ve mazeret bilgileri kaynaktan doğrudan alınmıştır.", "kaynaklar": ["D000006"]},
            ],
        }
        overview, developments, dropped = ai_provider._validate_narrative(parsed, allowed, snippets, facts)
        assert overview and overview["refs"] == ["D000003"], overview
        assert len(developments) == 1, developments
        assert developments[0]["refs"] == ["D000005"], "unrelated co-citation must be pruned"
        assert "prefixes" not in developments[0]
        assert dropped == {
            "duplicates_rule_based_section": 1,
            "near_duplicate": 1,
            "date_not_in_cited_source": 1,
            "unknown_citation": 1,
            "person_role_mismatch": 1,
            "no_information_claim": 1,
            "meta_statement": 1,
        }, dropped
        # Model output seen on a real run, and case content that must survive.
        for meta in ("Dosyanın genel durumu, son celse tarihi ve sonraki duruşma bilgileri kaynaktan doğrudan alınmıştır.",
                     "Uygulamanın kaynaktan kuralla çıkarıldığı VERIFIED yerel kaynaklar bulunmuştur."):
            assert ai_provider._META_STATEMENT.search(meta), meta
        for content in ("Sanık tek gelir kaynağının kesildiğini savundu.", "Sanık hakkında HAGB uygulaması talep edildi."):
            assert not ai_provider._META_STATEMENT.search(content), content


def test_case_summary_v2_composes_facts_narrative_and_coverage_offline():
    calls = []

    def fake_narrative(status, config, headers, profile, instructions, user_input):
        calls.append(user_input)
        parsed = {
            "durum": {"metin": "Sanık nafaka borcunu işsizlik nedeniyle ödeyemediğini savunmaktadır.", "kaynaklar": ["D000003"]},
            "gelismeler": [
                {"metin": "Son celse 14/04/2026 tarihinde yapıldı.", "kaynaklar": ["D000006"]},
                {"metin": "Sanık müdafii ödeme planı teklif edildiğini ve beraat talep edildiğini bildirdi.", "kaynaklar": ["D000003"]},
            ],
        }
        return {"parsed": parsed, "text": "{}", "responseId": "resp-1", "usage": None, "schemaEnforced": True, "errorCode": None}

    original_narrative, original_config = ai_provider._structured_narrative, ai_provider._provider_config
    ai_provider._structured_narrative = fake_narrative
    ai_provider._provider_config = lambda: {"provider": "lmstudio", "model": "synthetic", "baseUrl": "http://127.0.0.1:1234/v1"}
    try:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            scope = build_case(root)
            status = {"provider": "lmstudio", "model": "synthetic", "configured": True}
            profile = ai_provider._provider_limits("lmstudio")
            result = ai_provider._answer_case_summary(
                status, profile, root, scope, profile["maxSources"],
                {"institutionName": INSTITUTION, "caseId": CASE_ID, "totalDocuments": 7})
    finally:
        ai_provider._structured_narrative, ai_provider._provider_config = original_narrative, original_config

    assert len(calls) == 1
    assert "Sonraki duruşma: 19/05/2026 saat 09:15" in calls[0], "known facts must be given to the model"
    assert result["ok"] is True and result["retrievalMode"] == "case_summary_v1", result
    assert result["summarySchema"] == "case_summary_v2"
    answer = result["answer"]
    headings = [line for line in answer.splitlines() if line and not line.startswith("-") and line.isupper()]
    assert headings[:3] == ["KÜNYE", "SON CELSE VE ARA KARARLAR", "SONRAKİ DURUŞMA VE SÜRELER"], headings
    assert "GENEL DURUM" in headings and "ÖNE ÇIKAN GELİŞMELER" in headings and "KAPSAM VE EKSİKLER" in headings
    assert "Son celse 14/04/2026" not in answer, "narrative must not restate the rule-based hearing section"
    assert "ödeme planı teklif" in answer
    assert "Sonraki duruşma: 19/05/2026 saat 09:15" in answer
    assert "7 evrak · 6 evrakın metni okunabildi" in answer, answer
    assert "Bilirkişi Raporu" in answer, "unreadable documents must be named"
    # Identifiers never reach the summary, even when the source holds them.
    assert "12345678950" not in answer and "12345-67890-12345" not in answer
    assert result["maskedIdentifiers"] >= 1 and "Gizlilik:" in answer
    assert result["narrative"]["kept"] == 2 and result["narrative"]["droppedReasons"] == {"duplicates_rule_based_section": 1}
    assert "D000006" in result["sourceRefs"] and "D000002" not in result["sourceRefs"]
    assert all(source["sourceRef"] in result["sourceRefs"] for source in result["sources"])
    assert find_dates(answer) >= {(2026, 4, 14), (2026, 5, 19)}


if __name__ == "__main__":
    test_title_decides_document_role()
    test_masking_hides_identity_and_notification_numbers()
    test_role_check_folds_turkish_capitals()
    test_fit_snippet_keeps_the_decision_block_of_a_long_record()
    test_rule_based_facts_from_synthetic_case()
    test_selection_prefers_records_over_receipts()
    test_narrative_validation_drops_unsupported_and_redundant_items()
    test_case_summary_v2_composes_facts_narrative_and_coverage_offline()
    print("Case summary v2 regression: PASS")
