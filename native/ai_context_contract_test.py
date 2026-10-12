from pathlib import Path
import json
import tempfile
import zipfile
from ai_local_index import (
    assess_text_quality,
    PDF_EXTRACTOR_METHOD,
    XLSX_EXTRACTOR_METHOD,
    _quality_gate_pdf_chunks,
    _xlsx_text,
)
from ai_provider import _validate_citations, _is_local_http_url, _task_instructions

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
    assert "MESSAGE.GET_AI_CONTEXT" in workspace
    assert "MESSAGE.GET_AI_SERVICE_STATUS" in workspace
    assert "MESSAGE.SEARCH_AI_INDEX" in workspace
    assert "MESSAGE.AI_ASK" in workspace
    assert 'aiRetrieve' in workspace
    assert "BUILD_AI_INDEX" in constants
    assert "GET_AI_INDEX_STATUS" in constants
    assert "SEARCH_AI_INDEX" in constants
    assert "AI_ASK" in constants
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
    test_provider_gateway_is_secret_safe_citation_gated_and_local_capable()
    test_workspace_message_contract_exposes_ai_context_and_status()
    print("AI context contract: PASS")
