from pathlib import Path
import json
from ai_local_index import assess_text_quality, PDF_EXTRACTOR_METHOD, _quality_gate_pdf_chunks
from ai_provider import _validate_citations

ROOT = Path(__file__).resolve().parents[1]


def test_ai_policy_defaults_fail_closed():
    policy = json.loads((ROOT / "config" / "ai-policy.json").read_text(encoding="utf-8"))
    assert policy["enabledByDefault"] is False
    assert policy["provider"]["mode"] == "local-native-gateway"
    assert policy["provider"]["name"] == "openai"
    assert policy["provider"]["enabledByDefault"] is False
    assert policy["provider"]["credentialSource"].startswith("OPENAI_API_KEY")
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
    bad_result = assess_text_quality(bad)
    fragmented_result = assess_text_quality(fragmented)
    assert good_result["accepted"] is True, good_result
    assert good_result["score"] >= 0.72, good_result
    assert bad_result["accepted"] is False, bad_result
    assert bad_result["reason"] in {"suspicious_cid_glyphs", "low_quality_score"}, bad_result
    assert fragmented_result["accepted"] is False, fragmented_result
    assert fragmented_result["reason"] in {"fragmented_glyph_spacing", "low_quality_score"}, fragmented_result
    assert PDF_EXTRACTOR_METHOD == "pdf-content-stream-v4-chunk-quality-gated"

 

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

def test_provider_gateway_is_secret_safe_and_citation_gated():
    source = (ROOT / "native" / "ai_provider.py").read_text(encoding="utf-8")
    assert 'OPENAI_API_KEY' in source
    assert 'https://api.openai.com/v1/responses' in source
    assert '"store": False' in source
    assert '"apiKeyExposedToExtension": False' in source
    assert '"rawDocumentsUploaded": False' in source
    assert "_source_prompt(results)" in source
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
    test_provider_gateway_is_secret_safe_and_citation_gated()
    test_workspace_message_contract_exposes_ai_context_and_status()
    print("AI context contract: PASS")
