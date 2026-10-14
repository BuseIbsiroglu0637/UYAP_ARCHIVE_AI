from pathlib import Path
import hashlib
import os
import json
import sqlite3
import tempfile
import zipfile
from ai_local_index import (
    assess_text_quality,
    PDF_EXTRACTOR_METHOD,
    XLSX_EXTRACTOR_METHOD,
    _quality_gate_pdf_chunks,
    _xlsx_text,
    _select_summary_rows,
    _select_summary_documents,
    archive_case_scope_id,
    build_archive_case_index,
    index_status,
    inspect_archive_case,
)
from ai_provider import (
    _validate_citations,
    _validate_grounding,
    _validate_summary_v1,
    _is_local_http_url,
    _task_instructions,
    _is_summary_query,
)
from uyap_archive_agent import list_archive_registry, save_ai_summary, list_ai_summaries

ROOT = Path(__file__).resolve().parents[1]


def test_ai_policy_defaults_fail_closed():
    policy = json.loads((ROOT / "config" / "ai-policy.json").read_text(encoding="utf-8"))
    assert policy["enabledByDefault"] is False
    assert policy["provider"]["mode"] == "local-native-gateway"
    assert policy["provider"]["name"] == "openai"
    assert "lmstudio" in policy["provider"]["supportedProviders"]
    assert policy["provider"]["enabledByDefault"] is False
    assert policy["provider"]["providerSource"].startswith("UYAP_AI_PROVIDER")
    assert policy["provider"]["credentialSource"].startswith("OPENAI_API_KEY")
    assert policy["provider"]["baseUrlSource"].startswith("UYAP_AI_BASE_URL")
    assert policy["provider"]["apiKeyInExtensionAllowed"] is False
    assert policy["archiveBoundary"]["verifiedOnly"] is True
    assert policy["archiveBoundary"]["allowQueued"] is False
    assert policy["archiveBoundary"]["allowRetryWait"] is False
    assert policy["archiveBoundary"]["allowReviewRequired"] is False
    assert policy["archiveBoundary"]["allowFailed"] is False
    assert policy["archiveBoundary"]["allowSkippedWithSha256PhysicalProof"] is False
    assert policy["cloud"]["rawDocumentUploadByDefault"] is False
    assert policy["cloud"]["explicitUserConsentRequiredForRawDocumentUpload"] is True
    assert policy["answers"]["citationsRequired"] is True
    assert policy["context"]["includeAbsolutePaths"] is False
    assert policy["context"]["includeUyapDocumentIds"] is False
    assert policy["context"]["includeTokensOrUrls"] is False


def test_ai_context_builder_is_verified_only_and_privacy_light():
    source = (ROOT / "background" / "ai-context.js").read_text(encoding="utf-8")
    assert '=== "verified"' in source
    assert 'status === "skipped"' not in source
    assert 'sourceRef(index)' in source
    assert 'padStart(6, "0")' in source
    assert "rawDocumentContentIncluded: false" in source
    assert "absolutePathsIncluded: false" in source
    assert "uyapDocumentIdsIncluded: false" in source
    assert "citationsRequired: true" in source
    assert "targetPath:" not in source
    assert "localPath:" not in source


def test_pdf_text_quality_gate_accepts_turkish_and_rejects_mojibake():
    good = (
        "Kastamonu 1. İş Mahkemesi tarafından düzenlenen bilirkişi raporu incelendi. "
        "Taraflara tebliğ tarihinden itibaren iki haftalık süre verildi. "
        "Duruşma günü 15 Haziran 2026 olarak belirlendi."
    )
    bad = (
        "Talimat Gönderme Yazısı 法院審理資料測試亂碼測試亂碼測試 "
        "訴訟程序資料內容異常字符 韓文테스트문자열 漢字資料亂碼 "
        "T.C. SATIYE DEMİRBAS 法律文書編碼錯誤資料"
    )
    good_result = assess_text_quality(good)
    fragmented = (
        "Y K B1K4 G B 8US 09/11/2024 E D EN : YAPI BANKASI ve Kredi Bil gi "
        "MUHATAP: ÖZEN ÇÖZTÜRK No: 75956 41 TCKN: 4812 4369658 "
        "Ana Borcu MERKEZ MAH. SK. NO: 8 KAPI NO: 2 TÜRKİYE KONUSU"
    )
    mixed_script = (
        "T.C. Dosya No 2026/110 Tahsilat Yapı Bankası Masraf Avans "
        "法院資料亂碼 測試字元 bozulmuş karma alfabe metni"
    )
    bad_result = assess_text_quality(bad)
    fragmented_result = assess_text_quality(fragmented)
    mixed_result = assess_text_quality(mixed_script)
    assert good_result["accepted"] is True, good_result
    assert good_result["score"] >= 0.72, good_result
    assert bad_result["accepted"] is False, bad_result
    assert bad_result["reason"] in {"mixed_script_mojibake", "suspicious_cid_glyphs", "low_quality_score"}, bad_result
    assert fragmented_result["accepted"] is False, fragmented_result
    assert fragmented_result["reason"] in {"fragmented_glyph_spacing", "low_quality_score"}, fragmented_result
    assert mixed_result["accepted"] is False, mixed_result
    assert mixed_result["reason"] in {"mixed_script_mojibake", "suspicious_cid_glyphs", "low_quality_score"}, mixed_result
    assert PDF_EXTRACTOR_METHOD == "pdf-content-stream-v5-mixed-script-gated"


def test_pdf_chunk_quality_gate_filters_mixed_documents():
    clean = (
        "Kastamonu 1. İş Mahkemesi müzekkere cevabı. Taraflara tebliğ yapıldı ve "
        "dosyanın 15 Haziran 2026 tarihli duruşması için bilgi sunuldu."
    )
    corrupt = (
        "Dosya No 384 Y K B 1 K 4 G B 8 U S E D E N B i l g i M U H A T A P "
        "tek harfli parçalanmış metin örneği ile birlikte anlamsız glif dizisi"
    )
    gated = _quality_gate_pdf_chunks([clean, corrupt])
    assert gated["chunks"] == [clean], gated
    assert gated["rejectedChunks"] == 1, gated
    assert gated["rejectedByReason"], gated


def test_xlsx_semantic_extractor_drops_numeric_only_rows():
    shared_strings = """<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>Müzekkere Cevabı</t></si>
      <si><t>Görüntülü görüşme işleminiz başarıyla tamamlandı.</t></si>
    </sst>"""
    worksheet = """<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1">
          <c r="A1"><v>1655422</v></c><c r="B1"><v>18778</v></c>
          <c r="C1"><v>18784</v></c><c r="D1"><v>4022</v></c>
        </row>
        <row r="2">
          <c r="A2" t="s"><v>0</v></c>
          <c r="B2" t="s"><v>1</v></c>
          <c r="C2"><v>20260710</v></c>
        </row>
      </sheetData>
    </worksheet>"""
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "sample.xlsx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("xl/sharedStrings.xml", shared_strings)
            archive.writestr("xl/worksheets/sheet1.xml", worksheet)
        text = _xlsx_text(path)
    assert XLSX_EXTRACTOR_METHOD == "xlsx-semantic-v2-noise-gated"
    assert "Müzekkere Cevabı" in text
    assert "Görüntülü görüşme işleminiz başarıyla tamamlandı." in text
    assert "20260710" in text
    assert "1655422" not in text
    assert "18778" not in text


def test_summary_prompt_is_structured_and_source_grounded():
    prompt = _task_instructions("Bu dosyayı özetle")
    assert "Genel durum" in prompt
    assert "Öne çıkan gelişmeler" in prompt
    assert "Önemli tarihler / süreler" in prompt
    assert "Belirsiz veya eksik kalan noktalar" in prompt
    assert "[Dxxxxxx]" in prompt
    assert "teknik kayıt, sayı dizisi veya anlamsız veri" in prompt
    assert "En fazla 8 kısa madde" in prompt


def test_summary_prompt_forbids_invented_roles_and_detects_summary_request():
    prompt = _task_instructions("Bu dosyayı özetle")
    assert _is_summary_query("Bu dosyayı özetle") is True
    assert _is_summary_query("duruşma tarihi nedir") is False
    assert "müzekkere' bir taraf veya rol değildir" in prompt
    assert "soruşturma mühendisi" in prompt
    assert "aşama hakkında hüküm kurma" in prompt


def test_summary_context_diversifies_sources_and_avoids_numeric_noise():
    rows = [
        ("D000001", "1655422 18778 18784 4022 18779 1655422 18780", "Eski", "XLSX", "2025-01-01", 0),
        ("D000001", "Şikayet dilekçesi incelendi ve kolluktan açık kimlik ile adres bilgilerinin tespiti istendi.", "Eski", "XLSX", "2025-01-01", 1),
        ("D000002", "Ara karar ile taraflara belge sunmaları için iki haftalık süre verildi.", "Orta", "UDF", "2025-06-15", 0),
        ("D000003", "Bilirkişi raporu dosyaya sunuldu ve raporun taraflara tebliğine karar verildi.", "Yeni", "UDF", "2026-01-10", 0),
        ("D000004", "Duruşma günü 20 Ekim 2026 olarak belirlendi.", "En yeni", "UDF", "2026-10-20", 0),
    ]
    chosen = _select_summary_rows(rows, 3)
    refs = [row[0] for row in chosen]
    assert len(set(refs)) == 3
    assert "D000001" in refs
    selected_d1 = next(row for row in chosen if row[0] == "D000001")
    assert "Şikayet dilekçesi" in selected_d1[1]
    assert "1655422 18778" not in selected_d1[1]


def test_case_summary_v1_prefers_opening_final_and_distinct_documents():
    rows = [
        ("D000001", "Dava dilekçesi ile alacak talep edilmiştir.", "Dava Dilekçesi 03/09/2025", "UDF", "2025-09-03", 0),
        ("D000010", "Ara karar ile belge sunulması istenmiştir.", "Ara Karar", "UDF", "2025-10-01", 0),
        ("D000020", "Bilirkişi raporu dosyaya sunulmuştur.", "Bilirkişi Raporu", "PDF", "2025-12-15", 0),
        ("D000078", "Gerekçeli karar ile uyuşmazlık hakkında hüküm kurulmuştur.", "Gerekçeli Karar Evrakı 22/04/2026", "UDF", "2026-04-22", 0),
        ("D000078", "Aynı kararın ikinci metin parçasıdır.", "Gerekçeli Karar Evrakı 22/04/2026", "UDF", "2026-04-22", 1),
        ("D000081", "Son tebligat işlemi yapılmıştır.", "Tebligat", "UDF", "2026-05-01", 0),
    ]
    chosen = _select_summary_documents(rows, 5)
    refs = [item["sourceRef"] for item in chosen]
    assert len(refs) == len(set(refs)), chosen
    assert "D000001" in refs, chosen
    assert "D000078" in refs, chosen
    assert any(item["selectionReason"].startswith("Dosya başlangıcı") for item in chosen), chosen
    assert any(item["reason"] == "Sonuç / karar" for item in chosen), chosen
    assert len(chosen) <= 5


def test_case_summary_v1_does_not_require_model_written_citations():
    source_text = (
        "[D000001]\nBaşlık: Dava Dilekçesi\nMetin:\nAlacak talebiyle dava açılmıştır.\n\n"
        "[D000078]\nBaşlık: Gerekçeli Karar\nMetin:\n22 Nisan 2026 tarihinde gerekçeli karar oluşturulmuştur."
    )
    answer = (
        "DOSYANIN KONUSU\nAlacak talebine ilişkin bir uyuşmazlık bulunmaktadır.\n\n"
        "SON DURUM\n22 Nisan 2026 tarihli gerekçeli karar dosyada yer almaktadır.\n\n"
        "ÖNE ÇIKAN GELİŞMELER\n- Dava dilekçesi sunulmuştur.\n- Gerekçeli karar oluşturulmuştur.\n\n"
        "ÖNEMLİ TARİHLER\n- 22 Nisan 2026 — Gerekçeli karar."
    )
    ok, error, detail = _validate_summary_v1(answer, source_text)
    assert ok is True, (error, detail)
    assert "[D000001]" not in answer


def test_grounding_rejects_uncited_claims_single_source_summary_and_invented_roles():
    allowed = {"D000001", "D000002"}
    source_text = (
        "[D000001]\nBelge, banka tarafından gönderilen kredi sözleşmesi bilgisini içerir.\n"
        "[D000002]\nMahkeme 10 Ekim 2026 tarihli duruşma gününü bildirmiştir."
    )

    ok, error, detail = _validate_grounding(
        "Genel durum: Banka belgeleri sunmuştur.\nDuruşma tarihi 10 Ekim 2026'dır [D000002].",
        allowed,
        source_text,
        summary_request=True,
    )
    assert ok is False and error == "AI_RESPONSE_UNCITED_CLAIMS"
    assert detail["uncitedClaims"]

    ok, error, detail = _validate_grounding(
        "Banka belgeleri sunmuştur [D000001].",
        allowed,
        source_text,
        summary_request=True,
    )
    assert ok is False and error == "AI_RESPONSE_INSUFFICIENT_SOURCE_DIVERSITY"
    assert detail["minimumDistinctSources"] == 2

    ok, error, detail = _validate_grounding(
        "Davacı banka belge sunmuştur [D000001].\nDuruşma tarihi 10 Ekim 2026'dır [D000002].",
        allowed,
        source_text,
        summary_request=True,
    )
    assert ok is False and error == "AI_RESPONSE_UNSUPPORTED_ROLE"
    assert "davacı" in detail["unsupportedRoles"]

    ok, error, detail = _validate_grounding(
        "Banka belgeleri sunmuştur [D000001].\nMahkeme 10 Ekim 2026 tarihli duruşma gününü bildirmiştir [D000002].",
        allowed,
        source_text,
        summary_request=True,
    )
    assert ok is True and error is None
    assert set(detail["citedSourceRefs"]) == allowed


def test_grounding_accepts_markdown_summary_headings_without_citations():
    allowed = {"D000001", "D000002"}
    source_text = (
        "[D000001]\nBelge, banka tarafından gönderilen kredi sözleşmesi bilgisini içerir.\n"
        "[D000002]\nMahkeme 10 Ekim 2026 tarihli duruşma gününü bildirmiştir."
    )
    answer = (
        "**1. Genel durum**\n"
        "Banka tarafından kredi sözleşmesi bilgisi sunulmuştur [D000001].\n\n"
        "### 2. Öne çıkan gelişmeler\n"
        "Mahkeme 10 Ekim 2026 tarihli duruşma gününü bildirmiştir [D000002].\n\n"
        "**5. Belirsiz veya eksik kalan noktalar:**"
    )
    ok, error, detail = _validate_grounding(
        answer,
        allowed,
        source_text,
        summary_request=True,
    )
    assert ok is True, (error, detail)
    assert set(detail["citedSourceRefs"]) == allowed


def test_archive_case_ai_requires_complete_physical_case_and_builds_isolated_index():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        archive = root / "UYAP_Arsiv"
        case_folder = archive / "Kastamonu 2. İş Mahkemesi" / "2026_529_Dosya"
        case_folder.mkdir(parents=True)

        first = case_folder / "evrak1.txt"
        second = case_folder / "evrak2.txt"
        first.write_text("Mahkeme 10 Ekim 2026 tarihli duruşma gününü bildirmiştir.", encoding="utf-8")
        second.write_text("Taraflara bilirkişi raporuna karşı beyanda bulunmaları için süre verilmiştir.", encoding="utf-8")

        db = sqlite3.connect(archive / "_archive.db")
        db.execute(
            """CREATE TABLE documents(
                session_id TEXT, document_id TEXT, case_id TEXT, display_name TEXT,
                uyap_path TEXT, local_path TEXT, size INTEGER, sha256 TEXT, status TEXT,
                updated_at TEXT, institution_name TEXT, target_path TEXT, document_date TEXT,
                source_system TEXT
            )"""
        )
        for index, path in enumerate((first, second), start=1):
            data = path.read_bytes()
            db.execute(
                """INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"session-{index}",
                    f"doc-{index}",
                    "2026/529",
                    path.name,
                    f"Klasör/{path.name}",
                    str(path),
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    "VERIFIED",
                    f"2026-10-{10 + index:02d}T10:00:00Z",
                    "Kastamonu 2. İş Mahkemesi",
                    str(path.relative_to(archive)).replace("\\", "/"),
                    f"2026-10-{10 + index:02d}",
                    "UYAP",
                ),
            )
        db.commit()
        db.close()

        inspection = inspect_archive_case(root, "Kastamonu 2. İş Mahkemesi", "2026/529")
        assert inspection["readyForAi"] is True, inspection
        assert inspection["totalDocuments"] == 2, inspection
        assert inspection["verifiedPhysicalDocuments"] == 2, inspection
        assert inspection["scopeId"] == archive_case_scope_id("Kastamonu 2. İş Mahkemesi", "2026_529")

        built = build_archive_case_index(root, "Kastamonu 2. İş Mahkemesi", "2026/529")
        assert built["readyForAi"] is True, built
        assert built["availableDocuments"] == 2, built
        assert built["availableChunks"] >= 2, built
        status = index_status(root, built["scopeId"])
        assert status["ready"] is True, status
        assert status["documents"] == 2, status

        second.write_text("değiştirildi", encoding="utf-8")
        incomplete = inspect_archive_case(root, "Kastamonu 2. İş Mahkemesi", "2026/529")
        assert incomplete["readyForAi"] is False, incomplete
        assert incomplete["problemDocuments"] == 1, incomplete
        assert incomplete["issues"][0]["reason"] in {"size_mismatch", "sha256_mismatch"}, incomplete


def test_workspace_archive_case_ai_contract_is_independent_from_active_session():
    constants = (ROOT / "shared" / "constants.js").read_text(encoding="utf-8")
    worker = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
    workspace = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")
    html = (ROOT / "workspace" / "index.html").read_text(encoding="utf-8")
    assert 'GET_AI_ARCHIVE_CASES: "get_ai_archive_cases"' in constants
    assert 'BUILD_AI_ARCHIVE_CASE_INDEX: "build_ai_archive_case_index"' in constants
    assert 'AI_ASK_ARCHIVE_CASE: "ai_ask_archive_case"' in constants
    assert "case MESSAGE.GET_AI_ARCHIVE_CASES:" in worker
    assert "listArchiveRegistry({limit:1})" in worker
    assert 'action:"ai_build_archive_case_index"' in worker
    assert "resolveAiArchiveCase" in worker
    assert "MESSAGE.GET_AI_ARCHIVE_CASES" in workspace
    assert "MESSAGE.BUILD_AI_ARCHIVE_CASE_INDEX" in workspace
    assert "MESSAGE.AI_ASK_ARCHIVE_CASE" in workspace
    assert 'id="aiArchiveCasePicker"' in html
    assert "Tam dava indeksini hazırla" in workspace
    assert "Dosya özetini oluştur" in workspace
    assert "Özet için seçilen evraklar" in workspace
    assert "deterministic_case_summary_v1" in (ROOT / "native" / "ai_provider.py").read_text(encoding="utf-8")
    assert "Oturumdan bağımsız" in workspace or "oturumdan bağımsız" in workspace


def test_archive_registry_orphan_files_do_not_inflate_logical_case_completeness():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        archive = root / "UYAP_Arsiv"
        case_folder = archive / "Kastamonu 1. Asliye Hukuk Mahkemesi" / "2025_281_Dosya" / "2025_281"
        case_folder.mkdir(parents=True)

        registered = []
        for index in range(2):
            path = case_folder / f"evrak-{index + 1}.txt"
            path.write_text(f"Doğrulanmış evrak {index + 1}", encoding="utf-8")
            registered.append(path)
        orphan = case_folder / "eski-kayit-disi.txt"
        orphan.write_text("Kayıt dışı fiziksel dosya", encoding="utf-8")

        db = sqlite3.connect(archive / "_archive.db")
        db.execute(
            """CREATE TABLE documents(
                session_id TEXT, document_id TEXT, case_id TEXT, display_name TEXT,
                uyap_path TEXT, local_path TEXT, size INTEGER, sha256 TEXT, status TEXT,
                updated_at TEXT, institution_name TEXT, target_path TEXT, source_system TEXT
            )"""
        )
        for index, path in enumerate(registered, start=1):
            data = path.read_bytes()
            db.execute(
                """INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"session-{index}",
                    f"doc-{index}",
                    "2025/281",
                    path.name,
                    f"2025_281/{path.name}",
                    str(path),
                    len(data),
                    hashlib.sha256(data).hexdigest(),
                    "VERIFIED",
                    f"2026-10-{10 + index:02d}T10:00:00Z",
                    "Kastamonu 1. Asliye Hukuk Mahkemesi",
                    str(path.relative_to(archive)).replace("\\", "/"),
                    "UYAP",
                ),
            )
        db.commit()
        db.close()

        # list_archive_registry normally searches selected/browser archive roots
        # in addition to the supplied root. Keep this unit test hermetic so a
        # developer's real UYAP_Arsiv cannot contaminate the temporary fixture.
        import uyap_archive_agent as agent_module
        original_candidates = agent_module._archive_root_candidates
        try:
            agent_module._archive_root_candidates = lambda supplied_root=None: [Path(str(supplied_root)).resolve()]
            registry = list_archive_registry({"downloadsRoot": str(root), "limit": 1})
        finally:
            agent_module._archive_root_candidates = original_candidates

        case = next(
            item for item in registry["caseIndex"]
            if item["institutionName"] == "Kastamonu 1. Asliye Hukuk Mahkemesi"
            and item["caseId"] == "2025/281"
        )
        assert case["documentCount"] == 2, case
        assert case["physicalReadyCount"] == 2, case
        assert case["problemCount"] == 0, case
        assert case["unregisteredPhysicalCount"] == 1, case
        assert registry["physicalDiscoveredFiles"] >= 1, registry


def test_ai_summary_archive_persists_and_deduplicates_locally():
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        central = root / "central-history"
        (root / "UYAP_Arsiv").mkdir(parents=True)
        previous = os.environ.get("UYAP_AI_SUMMARY_STORE_ROOT")
        os.environ["UYAP_AI_SUMMARY_STORE_ROOT"] = str(central)
        try:
            message = {
                "downloadsRoot": str(root),
                "institutionName": "Kastamonu İcra Hukuk Mahkemesi",
                "caseId": "2025/413",
                "answer": (
                    "DOSYANIN KONUSU\nTakip dosyasındaki uyuşmazlık özetlenmiştir.\n\n"
                    "SON DURUM\nGerekçeli karar dosyada yer almaktadır.\n\n"
                    "ÖNE ÇIKAN GELİŞMELER\n- Dava dilekçesi sunulmuştur.\n\n"
                    "ÖNEMLİ TARİHLER\n- 22.04.2026 — Gerekçeli karar."
                ),
                "sources": [
                    {"sourceRef": "D000001", "title": "Dava Dilekçesi", "selectionReason": "Dosya başlangıcı"},
                    {"sourceRef": "D000078", "title": "Gerekçeli Karar", "selectionReason": "Sonuç / karar"},
                ],
                "sourceRefs": ["D000001", "D000078"],
                "provider": "lmstudio",
                "model": "qwen2.5-3b-instruct",
                "retrievalMode": "case_summary_v1",
                "selectionPolicy": "deterministic_case_summary_v1",
            }
            first = save_ai_summary(message)
            second = save_ai_summary(message)
            assert first["ok"] is True and first["deduplicated"] is False, first
            assert second["ok"] is True and second["deduplicated"] is True, second
            assert first["summary"]["summaryId"] == second["summary"]["summaryId"]
            assert str(first["summary"]["path"]).startswith(str(central.resolve()))
            listing = list_ai_summaries({
                "downloadsRoot": str(root),
                "institutionName": message["institutionName"],
                "caseId": message["caseId"],
                "limit": 20,
            })
            assert listing["count"] == 1, listing
            assert str(listing["historyRoot"]).startswith(str(central.resolve()))
            saved = listing["summaries"][0]
            assert saved["answer"].startswith("DOSYANIN KONUSU")
            assert len(saved["sources"]) == 2
            assert Path(saved["path"]).is_file()
            payload = json.loads(Path(saved["path"]).read_text(encoding="utf-8"))
            assert payload["privacy"]["localOnly"] is True
            assert payload["privacy"]["rawDocumentsStored"] is False
            assert payload["privacy"]["rawSourceSnippetsStored"] is False
            assert payload["storage"] == "central_local"

            # Simulate archive-root rediscovery: history must still survive even
            # when the old archive mirror is no longer available.
            moved_root = root / "moved"
            (moved_root / "UYAP_Arsiv").mkdir(parents=True)
            listing_after_move = list_ai_summaries({
                "downloadsRoot": str(moved_root),
                "institutionName": message["institutionName"],
                "caseId": message["caseId"],
                "limit": 20,
            })
            assert listing_after_move["count"] == 1, listing_after_move
            assert listing_after_move["summaries"][0]["summaryId"] == first["summary"]["summaryId"]
        finally:
            if previous is None:
                os.environ.pop("UYAP_AI_SUMMARY_STORE_ROOT", None)
            else:
                os.environ["UYAP_AI_SUMMARY_STORE_ROOT"] = previous


def test_ai_workspace_has_readable_result_history_and_pdf_export_contract():
    html = (ROOT / "workspace" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "workspace" / "workspace.css").read_text(encoding="utf-8")
    workspace = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")
    constants = (ROOT / "shared" / "constants.js").read_text(encoding="utf-8")
    worker = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
    print_html = (ROOT / "workspace" / "summary-print.html").read_text(encoding="utf-8")
    print_js = (ROOT / "workspace" / "summary-print.js").read_text(encoding="utf-8")
    print_css = (ROOT / "workspace" / "summary-print.css").read_text(encoding="utf-8")

    assert 'id="aiResultPanel"' in html
    assert 'id="aiSummaryHistory"' in html
    assert 'id="aiSummaryPdf"' in html
    assert ".ai-summary-section" in css
    assert ".ai-summary-history" in css
    assert 'GET_AI_ARCHIVE_SUMMARIES:"get_ai_archive_summaries"' in workspace
    assert "renderCaseSummaryHtml" in workspace
    assert "openSummaryPrint" in workspace
    assert "Kayıt geçmişi okunamadı:" in workspace
    for name in ("GET_AI_ARCHIVE_SUMMARY", "DELETE_AI_ARCHIVE_SUMMARY", "RESTORE_AI_ARCHIVE_SUMMARY"):
        assert name in constants and name in workspace and f"MESSAGE.{name}" in worker, name
    for action in ("ai_get_summary", "ai_delete_summary", "ai_restore_summary"):
        assert f'"{action}"' in worker, action
    assert "AI_PRINT_SUMMARY_KEY" in workspace
    assert 'GET_AI_ARCHIVE_SUMMARIES: "get_ai_archive_summaries"' in constants
    assert 'action:"ai_save_summary"' in worker
    assert 'action:"ai_list_summaries"' in worker
    assert "summary-print.css" in print_html
    assert "summary-print.js" in print_html
    assert 'const KEY="uyapAiPrintSummaryV1"' in print_js
    assert "window.print()" in print_js
    assert "@page{size:A4" in print_css


def test_provider_gateway_is_secret_safe_citation_gated_and_local_capable():
    source = (ROOT / "native" / "ai_provider.py").read_text(encoding="utf-8")
    assert 'OPENAI_API_KEY' in source
    assert 'UYAP_AI_PROVIDER' in source
    assert 'UYAP_AI_BASE_URL' in source
    assert "HKEY_CURRENT_USER" in source
    assert 'r"Environment"' in source
    assert '"windows_user_environment"' in source
    assert 'https://api.openai.com/v1/responses' in source
    assert 'http://127.0.0.1:1234/v1/responses' in source
    assert '"lmstudio"' in source
    assert "LMSTUDIO_TIMEOUT_SECONDS = 300" in source
    assert "LMSTUDIO_MAX_SOURCES = 6" in source
    assert "LMSTUDIO_MAX_SOURCE_SNIPPET_CHARS = 1400" in source
    assert "LMSTUDIO_MAX_OUTPUT_TOKENS = 600" in source
    assert 'timeout=profile["timeoutSeconds"]' in source
    assert '"store": False' in source
    assert '"apiKeyExposedToExtension": False' in source
    assert '"rawDocumentsUploaded": False' in source
    assert "_source_prompt(" in source
    assert _is_local_http_url("http://127.0.0.1:1234/v1/responses") is True
    assert _is_local_http_url("http://localhost:1234/v1/responses") is True
    assert _is_local_http_url("https://example.com/v1/responses") is False
    assert _is_local_http_url("http://192.168.1.10:1234/v1/responses") is False
    ok, error, invalid = _validate_citations("Sonuç [D000001].", {"D000001"})
    assert ok is True and error is None and invalid == []
    ok, error, invalid = _validate_citations("Kaynak yok.", {"D000001"})
    assert ok is False and error == "AI_RESPONSE_MISSING_CITATIONS"
    ok, error, invalid = _validate_citations("Yanlış [D999999].", {"D000001"})
    assert ok is False and error == "AI_RESPONSE_UNKNOWN_CITATION" and invalid == ["D999999"]


def test_workspace_message_contract_exposes_ai_context_and_status():
    constants = (ROOT / "shared" / "constants.js").read_text(encoding="utf-8")
    worker = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
    workspace = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")
    assert 'GET_AI_CONTEXT: "get_ai_context"' in constants
    assert 'GET_AI_SERVICE_STATUS: "get_ai_service_status"' in constants
    assert "case MESSAGE.GET_AI_CONTEXT:" in worker
    assert "buildAiContext" in worker
    assert 'action:"ai_provider_status"' in worker
    # Legacy session AI messages remain available in the worker for backward
    # compatibility, but the product workspace must use archive-case scope.
    assert "MESSAGE.GET_AI_ARCHIVE_CASES" in workspace
    assert "MESSAGE.GET_AI_SERVICE_STATUS" in workspace
    assert "MESSAGE.SEARCH_AI_ARCHIVE_CASE" in workspace
    assert "MESSAGE.AI_ASK_ARCHIVE_CASE" in workspace
    assert 'aiRetrieve' in workspace
    assert "BUILD_AI_INDEX" in constants
    assert "GET_AI_INDEX_STATUS" in constants
    assert "SEARCH_AI_INDEX" in constants
    assert "AI_ASK" in constants
    assert "GET_AI_ARCHIVE_CASES" in constants
    assert "BUILD_AI_ARCHIVE_CASE_INDEX" in constants
    assert "GET_AI_ARCHIVE_CASE_INDEX_STATUS" in constants
    assert "SEARCH_AI_ARCHIVE_CASE" in constants
    assert "AI_ASK_ARCHIVE_CASE" in constants
    native = (ROOT / "native" / "uyap_archive_agent.py").read_text(encoding="utf-8")
    worker = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
    native_bridge = (ROOT / "background" / "native-agent.js").read_text(encoding="utf-8")
    installer = (ROOT / "native" / "install_host.py").read_text(encoding="utf-8")
    self_test = (ROOT / "native" / "host_self_test.py").read_text(encoding="utf-8")
    local_index = (ROOT / "native" / "ai_local_index.py").read_text(encoding="utf-8")
    assert "ai_local_index_v1" in native
    assert "archive_case_ai_v1" in native
    assert 'ARCHIVE_CASE_AI_CAPABILITY = "archive_case_ai_v1"' in native_bridge
    assert "!result.capabilities?.includes(ARCHIVE_CASE_AI_CAPABILITY)" in native_bridge
    assert '"ai_build_archive_case_index"' in native_bridge
    assert 'if (action === "ai_answer") return 6 * 60_000;' in native_bridge
    assert "ai_summary_archive_v1" in installer
    assert "check_host_details" in installer
    assert "def check_host_details" in self_test
    assert 'AI_DIAGNOSTIC_FILE = "ai-diagnostic-latest.json"' in native
    assert '"queryStored": False' in native
    assert '"rawSourceSnippetsStored": False' in native
    assert '"providerOutputPreviewMaxChars": 1200' in native
    assert "diagnosticPath" in native
    assert "Tanı JSON:" in worker
    assert 'action == "ai_build_index"' in native
    assert "rawDocumentsUploaded" in local_index
    assert "networkUsed" in local_index






def test_important_dates_model_failure_returns_only_grounded_local_candidates():
    from ai_provider import (
        _deterministic_date_candidates,
        _validate_important_dates_answer,
        _validate_grounding,
    )
    rows = [
        {"sourceRef": "D000001", "eventDate": "2026-04-11",
         "eventType": "Duruşma", "title": "Duruşma Tutanağı"},
        {"sourceRef": "D000002", "eventDate": "2026-04-12",
         "eventType": "Ara karar", "title": "Ara Karar"},
        {"sourceRef": "D999999", "eventDate": "2026-02-31",
         "eventType": "Karar", "title": "Geçersiz tarih"},
    ]
    output = _deterministic_date_candidates(rows)
    assert output == (
        "- 11.04.2026 — Duruşma adayı [D000001]\n"
        "- 12.04.2026 — Ara karar adayı [D000002]"
    ), output
    ok, error, _ = _validate_grounding(
        output, {"D000001", "D000002"},
        "[D000001] Duruşma\n[D000002] Ara karar",
        summary_request=False,
    )
    assert ok is True, error
    ok, error, _ = _validate_important_dates_answer(output, rows)
    assert ok is True, error
    bad_model_output = (
        "Özet: Bu dosyada tüm yasal süreler dolmuştur.\n"
        "- 01.04.2026 — Duruşma [D000001]"
    )
    ok, error, _ = _validate_grounding(
        bad_model_output, {"D000001", "D000002"},
        "[D000001] Duruşma\n[D000002] Ara karar",
        summary_request=False,
    )
    assert ok is False and error == "AI_RESPONSE_UNCITED_CLAIMS"


def test_important_dates_model_failure_fallback_wiring_and_ui_notice():
    provider = (ROOT / "native" / "ai_provider.py").read_text(encoding="utf-8")
    workspace = (ROOT / "workspace" / "workspace.js").read_text(encoding="utf-8")
    css = (ROOT / "workspace" / "workspace.css").read_text(encoding="utf-8")
    assert 'if not valid and workflow_id == "important_dates":' in provider
    assert "_validate_grounding(safe_answer" in provider or (
        "safe_answer, allowed_refs, source_text" in provider
    )
    assert "_validate_important_dates_answer(safe_answer, results)" in provider
    assert '"responseRecovery": "verified_date_candidates_v1"' in provider
    assert "modelAnswerAccepted" in provider
    assert "esc(result.workflowNotice)" in workspace
    assert ".ai-saved-badge[hidden]{display:none!important}" in css

def test_important_dates_require_source_event_proximity_and_sane_dates():
    from ai_workflows import _date_events, _normalize_event_date
    from ai_provider import _validate_important_dates_answer

    sample = (
        "Dava tarihi: 23/03/2026\n"
        "Evrak kaydedildi: 24/03/2026\n"
        "Duruşma tarihi: 11/04/2026\n"
        "Ara karar 12.04.2026 tarihinde verilmiştir.\n"
        "Tebliğ tarihi 31/02/2026 hatalı kayıt\n"
    )
    found = _date_events(sample)
    assert len(found) == 2, found
    close_header = _date_events("Dava tarihi: 23/03/2026, duruşma tarihi: 30/03/2026")
    assert not any(item["eventDate"] == "2026-03-23" for item in close_header), close_header
    assert any(item["eventDate"] == "2026-03-30" for item in close_header), close_header
    assert [x["eventDate"] for x in found] == ["2026-04-11", "2026-04-12"], found
    assert _normalize_event_date("2026-04-11") == "2026-04-11"
    assert _normalize_event_date("11/04/2026") == "2026-04-11"
    assert _normalize_event_date("31/02/2026") is None
    candidates = [
        {"sourceRef": "D000001", "eventDate": "2026-04-11", "eventType": "Duruşma"},
        {"sourceRef": "D000002", "eventDate": "2026-04-12", "eventType": "Ara karar"},
    ]
    good = "- 11.04.2026 — Duruşma: duruşma günü [D000001]\n- 12/04/2026 — Ara karar [D000002]"
    valid, error, _ = _validate_important_dates_answer(good, candidates)
    assert valid and error is None, (valid, error)
    for bad in (
        "- 23/03/2026 — Dava tarihi [D000001]",
        "- 11/04/2026 — Dava tarihi [D000001]",
        "- 12/04/2026 — Duruşma [D000001]",
        "- 11/04/2026 — Duruşma [D999999]",
        "- 31/02/2026 — Duruşma [D000001]",
    ):
        valid, error, _ = _validate_important_dates_answer(bad, candidates)
        assert not valid, (bad, error)


def test_six_ai_workflows_route_by_explicit_id_and_verified_case_scope():
    from ai_local_index import _index_db, archive_case_scope_id
    from ai_workflows import WORKFLOW_IDS, workflow_context
    from ai_provider import _WORKFLOW_TASKS, answer_from_local_index

    assert WORKFLOW_IDS == ("case_summary", "important_dates", "court_orders", "parties", "tebligat")
    assert set(WORKFLOW_IDS) - {"case_summary"} == set(_WORKFLOW_TASKS)
    assert answer_from_local_index(".", "not-a-case", "kronolojik özet",
                                   workflow_id="unknown")["errorCode"] == "AI_WORKFLOW_UNSUPPORTED"
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "UYAP_Arsiv").mkdir()
        scope = archive_case_scope_id("Örnek Mahkeme", "2026/12")
        db, fts5 = _index_db(root, scope)
        try:
            entries = [
                ("D000001", "doc-a", "2026-04-03", "Duruşma Tutanağı",
                 "03.04.2026 tarihli duruşmada vekili hazır bulundu. Davacı Ayşe Kaya temsil edildi."),
                ("D000002", "doc-b", "2026-04-09", "Tebligat",
                 "09.04.2026 tarihli tebligat tebliğ edilmiştir. Süre başlangıcı açık değildir."),
                ("D000003", "doc-c", "2026-04-11", "Ara Karar",
                 "Mahkeme 11.04.2026 tarihinde ara karar vermiştir."),
            ]
            def insert(item):
                ref, doc_hash, date, title, snippet = item
                db.execute("""INSERT INTO ai_documents
                 (source_ref,session_id,document_id_hash,file_sha256,title,document_type,
                  document_date,extraction_method,extraction_quality,text_chars,chunk_count,indexed_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (ref,scope,doc_hash,("a"*63+str(len(doc_hash)%10)),title,"UDF",date,
                  "test","accepted",len(snippet),1,"2026-10-13"))
                db.execute("INSERT INTO ai_chunks(source_ref,chunk_no,text) VALUES(?,?,?)",
                           (ref,0,snippet))
                if fts5:
                    db.execute("INSERT INTO ai_chunks_fts(source_ref,text) VALUES(?,?)",(ref,snippet))
            for item in entries:
                insert(item)
            db.commit()
        finally:
            db.close()

        for workflow_id in ("case_summary","important_dates","parties","classification","chronology"):
            result = workflow_context(root, scope, workflow_id, "gelişmeleri göster", 6)
            assert result["results"], (workflow_id,result)
            assert all(row["sourceRef"].startswith("D") for row in result["results"])

        first = workflow_context(root,scope,"new_updates","",6)
        assert first["noticeCode"] == "AI_UPDATES_BASELINE_CREATED", first
        second = workflow_context(root,scope,"new_updates","",6)
        assert second["noticeCode"] == "AI_UPDATES_NO_NEW_DOCUMENTS", second
        db, _ = _index_db(root,scope)
        try:
            insert(("D000004","doc-d","2026-04-12","Cevap Dilekçesi",
                    "12.04.2026 tarihinde yeni cevap dilekçesi verilmiştir."))
            db.commit()
        finally:
            db.close()
        changed = workflow_context(root,scope,"new_updates","",6)
        assert [item["sourceRef"] for item in changed["results"]] == ["D000004"], changed
        assert changed.get("noticeCode") is None, changed
        assert changed["coverage"] == "selected_sources_only"
        assert answer_from_local_index(root,"not-a-case","Açıklama",
                                       workflow_id="improper")["errorCode"] == "AI_WORKFLOW_UNSUPPORTED"


def test_six_ai_workflows_workspace_and_worker_contract():
    html = (ROOT/"workspace"/"index.html").read_text(encoding="utf-8")
    js = (ROOT/"workspace"/"workspace.js").read_text(encoding="utf-8")
    worker = (ROOT/"background"/"service-worker.js").read_text(encoding="utf-8")
    native = (ROOT/"native"/"uyap_archive_agent.py").read_text(encoding="utf-8")
    provider = (ROOT/"native"/"ai_provider.py").read_text(encoding="utf-8")
    for workflow_id in ("case_summary","important_dates","court_orders","parties","tebligat"):
        assert f'data-ai-workflow="{workflow_id}"' in html
    assert 'tebligat:"Tebligatları göster"' in js and 'tebligat:["tebligatSchema","tebligatSections"]' in worker
    # Retired cards: still accepted natively from older builds, never shown.
    for workflow_id in ("new_updates","classification","chronology"):
        assert f'data-ai-workflow="{workflow_id}"' not in html
    assert 'workflowId:state.aiWorkflowId' in js
    assert 'id="aiFreeformMode"' in html
    assert 'setAiPrompt("",{navigate:false,workflowId:"ask"})' in js
    assert 'request.workflowId||"ask"' in worker
    assert 'workflow_id=message.get("workflowId")' in native
    assert 'summary_request = workflow_id == "case_summary"' in provider
    assert 'function isCaseSummaryPrompt(){return state.aiWorkflowId==="case_summary";}' in js

if __name__ == "__main__":
    test_important_dates_model_failure_returns_only_grounded_local_candidates()
    test_important_dates_model_failure_fallback_wiring_and_ui_notice()
    test_important_dates_require_source_event_proximity_and_sane_dates()
    test_six_ai_workflows_route_by_explicit_id_and_verified_case_scope()
    test_six_ai_workflows_workspace_and_worker_contract()
    test_ai_policy_defaults_fail_closed()
    test_ai_context_builder_is_verified_only_and_privacy_light()
    test_pdf_text_quality_gate_accepts_turkish_and_rejects_mojibake()
    test_pdf_chunk_quality_gate_filters_mixed_documents()
    test_xlsx_semantic_extractor_drops_numeric_only_rows()
    test_summary_prompt_is_structured_and_source_grounded()
    test_summary_prompt_forbids_invented_roles_and_detects_summary_request()
    test_summary_context_diversifies_sources_and_avoids_numeric_noise()
    test_case_summary_v1_prefers_opening_final_and_distinct_documents()
    test_case_summary_v1_does_not_require_model_written_citations()
    test_grounding_rejects_uncited_claims_single_source_summary_and_invented_roles()
    test_grounding_accepts_markdown_summary_headings_without_citations()
    test_archive_case_ai_requires_complete_physical_case_and_builds_isolated_index()
    test_archive_registry_orphan_files_do_not_inflate_logical_case_completeness()
    test_workspace_archive_case_ai_contract_is_independent_from_active_session()
    test_ai_summary_archive_persists_and_deduplicates_locally()
    test_ai_workspace_has_readable_result_history_and_pdf_export_contract()
    test_provider_gateway_is_secret_safe_citation_gated_and_local_capable()
    test_workspace_message_contract_exposes_ai_context_and_status()
    print("AI context contract: PASS")
