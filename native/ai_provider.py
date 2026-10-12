from __future__ import annotations

"""Privacy-minimized AI provider gateway for UYAP Archive AI.

The browser extension never receives provider credentials. The native host
supports both OpenAI and a localhost-only LM Studio provider and sends only
locally retrieved, quality-gated snippets to a Responses-compatible API.
"""

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit

from ai_local_index import search_index

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
LMSTUDIO_RESPONSES_URL = "http://127.0.0.1:1234/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"
DEFAULT_LMSTUDIO_MODEL = "qwen2.5-3b-instruct"
SUPPORTED_PROVIDERS = {"openai", "lmstudio"}
MAX_QUERY_CHARS = 1200
MAX_SOURCE_SNIPPET_CHARS = 2600
MAX_SOURCES = 8
MAX_OUTPUT_TOKENS = 1800
SOURCE_REF_RE = re.compile(r"\[(D\d{6})\]")


def _read_user_setting(name, default=""):
    value = str(os.environ.get(name) or "").strip()
    if value:
        return value, "process_environment"
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                value, _ = winreg.QueryValueEx(key, name)
            value = str(value or "").strip()
            if value:
                return value, "windows_user_environment"
        except (OSError, ImportError, ValueError):
            pass
    return str(default or "").strip(), None


def _is_local_http_url(value):
    try:
        parsed = urlsplit(str(value or "").strip())
    except Exception:
        return False
    return (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and bool(parsed.port)
    )


def _models_url(responses_url):
    parsed = urlsplit(responses_url)
    path = parsed.path or ""
    if path.endswith("/responses"):
        path = path[:-len("responses")] + "models"
    else:
        path = "/v1/models"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _provider_config():
    provider, provider_source = _read_user_setting("UYAP_AI_PROVIDER", "openai")
    provider = (provider or "openai").strip().lower()

    if provider == "lmstudio":
        endpoint, endpoint_source = _read_user_setting("UYAP_AI_BASE_URL", LMSTUDIO_RESPONSES_URL)
        model, model_source = _read_user_setting("UYAP_AI_MODEL", DEFAULT_LMSTUDIO_MODEL)
        return {
            "provider": provider,
            "providerSource": provider_source or "default",
            "endpoint": endpoint or LMSTUDIO_RESPONSES_URL,
            "endpointSource": endpoint_source or "default",
            "model": model or DEFAULT_LMSTUDIO_MODEL,
            "modelSource": model_source or "default",
            "requiresApiKey": False,
            "localOnly": True,
        }

    if provider == "openai":
        model, model_source = _read_user_setting("UYAP_AI_MODEL", DEFAULT_OPENAI_MODEL)
        return {
            "provider": provider,
            "providerSource": provider_source or "default",
            "endpoint": OPENAI_RESPONSES_URL,
            "endpointSource": "built-in",
            "model": model or DEFAULT_OPENAI_MODEL,
            "modelSource": model_source or "default",
            "requiresApiKey": True,
            "localOnly": False,
        }

    return {
        "provider": provider,
        "providerSource": provider_source or "default",
        "endpoint": "",
        "endpointSource": None,
        "model": "",
        "modelSource": None,
        "requiresApiKey": False,
        "localOnly": False,
        "unsupported": True,
    }


def _probe_lmstudio(endpoint, model):
    if not _is_local_http_url(endpoint):
        return False, "LMSTUDIO_LOCAL_ENDPOINT_REQUIRED"
    try:
        request = urllib.request.Request(
            _models_url(endpoint),
            headers={"User-Agent": "UYAP-Archive-AI-Native/2.7"},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return False, "LMSTUDIO_UNREACHABLE"

    available = {
        str(item.get("id") or "").strip()
        for item in (payload.get("data") or [])
        if isinstance(item, dict)
    }
    if model and model not in available:
        return False, "LMSTUDIO_MODEL_NOT_AVAILABLE"
    return True, None


_PROVIDER_INSTRUCTIONS = """Sen UYAP Archive AI içinde çalışan kaynak-kısıtlı bir hukuki dosya analiz yardımcısısın.
Yalnız sana verilen VERIFIED yerel kaynak parçalarını kullan.
Kaynaklarda bulunmayan olguları uydurma; gerekiyorsa 'Doğrulanmış yerel kaynaklarda bulunamadı' de.
Her maddi/olgusal iddianın sonunda ilgili kaynak referansını köşeli parantezle yaz: [D000001].
Sadece verilen Dxxxxxx referanslarını kullan.
Hukuki tavsiye verdiğini iddia etme; dosya içeriğini özetle ve açıkça kaynaklandır.
Kısa, net Türkçe kullan.
"""


def provider_status():
    config = _provider_config()

    if config.get("unsupported"):
        return {
            "ok": True,
            "schemaVersion": 2,
            "kind": "UYAP_AI_SERVICE_STATUS",
            "configured": False,
            "enabled": False,
            "provider": config["provider"],
            "model": "",
            "mode": "local-native-gateway",
            "reason": "AI_PROVIDER_UNSUPPORTED",
            "providerSource": config.get("providerSource"),
            "apiKeyExposedToExtension": False,
            "rawDocumentUploadByDefault": False,
            "retrievedSnippetsOnly": True,
            "store": False,
            "citationsRequired": True,
        }

    if config["provider"] == "lmstudio":
        available, reason = _probe_lmstudio(config["endpoint"], config["model"])
        return {
            "ok": True,
            "schemaVersion": 2,
            "kind": "UYAP_AI_SERVICE_STATUS",
            "configured": available,
            "enabled": available,
            "provider": "lmstudio",
            "model": config["model"],
            "mode": "local-native-gateway",
            "reason": reason,
            "providerSource": config.get("providerSource"),
            "modelSource": config.get("modelSource"),
            "endpointSource": config.get("endpointSource"),
            "localEndpointOnly": True,
            "apiKeyExposedToExtension": False,
            "rawDocumentUploadByDefault": False,
            "retrievedSnippetsOnly": True,
            "store": False,
            "citationsRequired": True,
        }

    key, key_source = _read_user_setting("OPENAI_API_KEY")
    return {
        "ok": True,
        "schemaVersion": 2,
        "kind": "UYAP_AI_SERVICE_STATUS",
        "configured": bool(key),
        "enabled": bool(key),
        "provider": "openai",
        "model": config["model"],
        "mode": "local-native-gateway",
        "reason": None if key else "OPENAI_API_KEY_NOT_CONFIGURED",
        "providerSource": config.get("providerSource"),
        "keySource": key_source if key else None,
        "modelSource": config.get("modelSource"),
        "apiKeyExposedToExtension": False,
        "rawDocumentUploadByDefault": False,
        "retrievedSnippetsOnly": True,
        "store": False,
        "citationsRequired": True,
    }


def _source_prompt(results):
    blocks = []
    for row in results[:MAX_SOURCES]:
        source_ref = str(row.get("sourceRef") or "").strip()
        title = str(row.get("title") or "Adsız evrak").strip()[:240]
        document_type = str(row.get("documentType") or "DOSYA").strip()[:32]
        document_date = str(row.get("documentDate") or "").strip()[:64]
        snippet = str(row.get("snippet") or "").strip()[:MAX_SOURCE_SNIPPET_CHARS]
        blocks.append(
            f"[{source_ref}]\n"
            f"Başlık: {title}\n"
            f"Tür: {document_type}\n"
            f"Tarih: {document_date or '-'}\n"
            f"Metin:\n{snippet}"
        )
    return "\n\n---\n\n".join(blocks)


def _extract_output_text(payload):
    pieces = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = str(content.get("text") or "").strip()
                if text:
                    pieces.append(text)
    return "\n".join(pieces).strip()


def _validate_citations(answer, allowed_refs):
    cited = SOURCE_REF_RE.findall(answer or "")
    invalid = sorted({ref for ref in cited if ref not in allowed_refs})
    if invalid:
        return False, "AI_RESPONSE_UNKNOWN_CITATION", invalid
    if allowed_refs and not cited:
        return False, "AI_RESPONSE_MISSING_CITATIONS", []
    return True, None, []


def answer_from_local_index(downloads_root, session_id, query, limit=MAX_SOURCES):
    status = provider_status()
    if not status["configured"]:
        provider = status.get("provider")
        reason = status.get("reason")
        if provider == "lmstudio":
            detail = {
                "LMSTUDIO_UNREACHABLE": "LM Studio yerel sunucusuna bağlanılamadı. Model Server'ı başlatın.",
                "LMSTUDIO_MODEL_NOT_AVAILABLE": "Seçili LM Studio modeli yerel sunucuda bulunamadı veya yüklenmedi.",
                "LMSTUDIO_LOCAL_ENDPOINT_REQUIRED": "LM Studio sağlayıcısı yalnız localhost/127.0.0.1 adresine bağlanabilir.",
            }.get(reason, "LM Studio yerel AI sağlayıcısı hazır değil.")
        elif reason == "AI_PROVIDER_UNSUPPORTED":
            detail = "UYAP_AI_PROVIDER değeri desteklenmiyor."
        else:
            detail = "OpenAI API anahtarı yerel Native Agent ortamında yapılandırılmamış."
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_NOT_CONFIGURED",
            "error": detail,
            "service": status,
        }

    query = str(query or "").strip()[:MAX_QUERY_CHARS]
    if not query:
        return {"ok": False, "errorCode": "AI_QUERY_REQUIRED", "error": "AI sorusu boş olamaz."}

    retrieval = search_index(downloads_root, session_id, query, min(MAX_SOURCES, max(1, int(limit or MAX_SOURCES))))
    results = list(retrieval.get("results") or [])
    if not results:
        return {
            "ok": True,
            "kind": "UYAP_AI_ANSWER",
            "schemaVersion": 1,
            "answer": "Doğrulanmış yerel kaynaklarda bu soruyu yanıtlayacak eşleşen içerik bulunamadı.",
            "sourceRefs": [],
            "sources": [],
            "model": status["model"],
            "provider": status["provider"],
            "providerCalled": False,
            "rawDocumentsUploaded": False,
            "store": False,
        }

    allowed_refs = {str(row.get("sourceRef") or "") for row in results if row.get("sourceRef")}
    source_text = _source_prompt(results)
    body = {
        "model": status["model"],
        "store": False,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "instructions": _PROVIDER_INSTRUCTIONS,
        "input": (
            "Kullanıcı sorusu:\n"
            + query
            + "\n\nYalnız aşağıdaki VERIFIED yerel kaynak parçalarını kullan:\n\n"
            + source_text
        ),
    }

    config = _provider_config()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "UYAP-Archive-AI-Native/2.7",
    }
    if status["provider"] == "openai":
        api_key, _ = _read_user_setting("OPENAI_API_KEY")
        headers["Authorization"] = "Bearer " + api_key
    elif status["provider"] == "lmstudio" and not _is_local_http_url(config["endpoint"]):
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_LOCAL_ENDPOINT_REQUIRED",
            "error": "LM Studio sağlayıcısı yalnız localhost/127.0.0.1 adresine bağlanabilir.",
        }

    request = urllib.request.Request(
        config["endpoint"],
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = ""
        try:
            detail_payload = json.loads(error.read().decode("utf-8", errors="replace"))
            detail = str((detail_payload.get("error") or {}).get("message") or "")
        except Exception:
            detail = ""
        provider_label = "LM Studio" if status["provider"] == "lmstudio" else "OpenAI"
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_HTTP_ERROR",
            "httpStatus": int(getattr(error, "code", 0) or 0),
            "error": detail or f"{provider_label} HTTP {getattr(error, 'code', 'hatası')}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        provider_label = "LM Studio yerel sunucusu" if status["provider"] == "lmstudio" else "AI sağlayıcısı"
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_NETWORK_ERROR",
            "error": f"{provider_label} ile bağlantı kurulamadı: {error}",
        }

    answer = _extract_output_text(payload)
    if not answer:
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_EMPTY_RESPONSE",
            "error": "AI sağlayıcısı metin yanıtı döndürmedi.",
        }

    valid, citation_error, invalid_refs = _validate_citations(answer, allowed_refs)
    if not valid:
        return {
            "ok": False,
            "errorCode": citation_error,
            "error": "AI yanıtı zorunlu kaynak doğrulamasını geçemedi.",
            "invalidSourceRefs": invalid_refs,
        }

    cited_refs = list(dict.fromkeys(SOURCE_REF_RE.findall(answer)))
    sources = [
        {
            "sourceRef": row.get("sourceRef"),
            "title": row.get("title"),
            "documentType": row.get("documentType"),
            "documentDate": row.get("documentDate"),
        }
        for row in results
        if row.get("sourceRef") in cited_refs
    ]
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else None
    return {
        "ok": True,
        "kind": "UYAP_AI_ANSWER",
        "schemaVersion": 1,
        "answer": answer,
        "sourceRefs": cited_refs,
        "sources": sources,
        "model": status["model"],
        "provider": status["provider"],
        "providerResponseId": payload.get("id"),
        "providerCalled": True,
        "rawDocumentsUploaded": False,
        "retrievedSnippetsOnly": True,
        "store": False,
        "usage": usage,
    }
