from pathlib import Path
import hashlib
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
    archive_case_scope_id,
    build_archive_case_index,
    index_status,
    inspect_archive_case,
)
from ai_provider import (
    _validate_citations,
    _validate_grounding,
    _is_local_http_url,
    _task_instructions,
    _is_summary_query,
)

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
    assert 'action:"ai_build_archive_case_index"' in worker
    assert "resolveAiArchiveCase" in worker
    assert "MESSAGE.GET_AI_ARCHIVE_CASES" in workspace
    assert "MESSAGE.BUILD_AI_ARCHIVE_CASE_INDEX" in workspace
    assert "MESSAGE.AI_ASK_ARCHIVE_CASE" in workspace
    assert 'id="aiArchiveCasePicker"' in html
    assert "Tam dava indeksini hazırla" in workspace
    assert "Oturumdan bağımsız" in workspace or "oturumdan bağımsız" in workspace


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
    assert "LMSTUDIO_MAX_SOURCES = 4" in source
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
    local_index = (ROOT / "native" / "ai_local_index.py").read_text(encoding="utf-8")
    assert "ai_local_index_v1" in native
    assert 'action == "ai_build_index"' in native
    assert "rawDocumentsUploaded" in local_index
    assert "networkUsed" in local_index


if __name__ == "__main__":
    test_ai_policy_defaults_fail_closed()
    test_ai_context_builder_is_verified_only_and_privacy_light()
    test_pdf_text_quality_gate_accepts_turkish_and_rejects_mojibake()
    test_pdf_chunk_quality_gate_filters_mixed_documents()
    test_xlsx_semantic_extractor_drops_numeric_only_rows()
    test_summary_prompt_is_structured_and_source_grounded()
    test_summary_prompt_forbids_invented_roles_and_detects_summary_request()
    test_summary_context_diversifies_sources_and_avoids_numeric_noise()
    test_grounding_rejects_uncited_claims_single_source_summary_and_invented_roles()
    test_archive_case_ai_requires_complete_physical_case_and_builds_isolated_index()
    test_workspace_archive_case_ai_contract_is_independent_from_active_session()
    test_provider_gateway_is_secret_safe_citation_gated_and_local_capable()
    test_workspace_message_contract_exposes_ai_context_and_status()
    print("AI context contract: PASS")
