from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]


def test_ai_policy_defaults_fail_closed():
    policy = json.loads((ROOT / "config" / "ai-policy.json").read_text(encoding="utf-8"))
    assert policy["enabledByDefault"] is False
    assert policy["provider"]["mode"] == "disabled"
    assert policy["provider"]["apiKeyInExtensionAllowed"] is False
    assert policy["archiveBoundary"]["verifiedOnly"] is True
    assert policy["cloud"]["rawDocumentUploadByDefault"] is False
    assert policy["cloud"]["explicitUserConsentRequiredForRawDocumentUpload"] is True
    assert policy["answers"]["citationsRequired"] is True
    assert policy["context"]["includeAbsolutePaths"] is False
    assert policy["context"]["includeUyapDocumentIds"] is False
    assert policy["context"]["includeTokensOrUrls"] is False


def test_ai_context_builder_has_no_path_or_raw_content_fields():
    source = (ROOT / "background" / "ai-context.js").read_text(encoding="utf-8")
    assert 'status === "verified"' in source
    assert 'status === "skipped"' in source
    assert "hasSha256PhysicalProof" in source
    assert "rawDocumentContentIncluded: false" in source
    assert "absolutePathsIncluded: false" in source
    assert "uyapDocumentIdsIncluded: false" in source
    assert "citationsRequired: true" in source
    assert re.search(r'D\\\$\\\{String\\\(index \+ 1\\\)\.padStart', source) is None


def test_workspace_message_contract_exposes_ai_context_only():
    constants = (ROOT / "shared" / "constants.js").read_text(encoding="utf-8")
    worker = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
    assert "GET_AI_CONTEXT" in constants
    assert "GET_AI_SERVICE_STATUS" in constants
    assert "buildAiContext" in worker
    assert "getAiServiceStatus" in worker
