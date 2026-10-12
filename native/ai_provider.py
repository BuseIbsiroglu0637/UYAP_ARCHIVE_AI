from __future__ import annotations

"""Privacy-minimized OpenAI provider gateway for UYAP Archive AI.

The browser extension never receives the API key. The native host reads
OPENAI_API_KEY from the operating-system environment and sends only locally
retrieved, quality-gated snippets to the Responses API.
"""

import json
import os
import re
import urllib.error
import urllib.request

from ai_local_index import search_index

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6-terra"
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


_PROVIDER_INSTRUCTIONS = """Sen UYAP Archive AI içinde çalışan kaynak-kısıtlı bir hukuki dosya analiz yardımcısısın.
Yalnız sana verilen VERIFIED yerel kaynak parçalarını kullan.
Kaynaklarda bulunmayan olguları uydurma; gerekiyorsa 'Doğrulanmış yerel kaynaklarda bulunamadı' de.
Her maddi/olgusal iddianın sonunda ilgili kaynak referansını köşeli parantezle yaz: [D000001].
Sadece verilen Dxxxxxx referanslarını kullan.
Hukuki tavsiye verdiğini iddia etme; dosya içeriğini özetle ve açıkça kaynaklandır.
Kısa, net Türkçe kullan.
"""


def provider_status():
    key, key_source = _read_user_setting("OPENAI_API_KEY")
    model, model_source = _read_user_setting("UYAP_AI_MODEL", DEFAULT_MODEL)
    model = model or DEFAULT_MODEL
    return {
        "ok": True,
        "schemaVersion": 1,
        "kind": "UYAP_AI_SERVICE_STATUS",
        "configured": bool(key),
        "enabled": bool(key),
        "provider": "openai",
        "model": model,
        "mode": "local-native-gateway",
        "reason": None if key else "OPENAI_API_KEY_NOT_CONFIGURED",
        "keySource": key_source if key else None,
        "modelSource": model_source or "default",
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
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_NOT_CONFIGURED",
            "error": "OpenAI API anahtarı yerel Native Agent ortamında yapılandırılmamış.",
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
            "provider": "openai",
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

    api_key, _ = _read_user_setting("OPENAI_API_KEY")
    request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json",
            "User-Agent": "UYAP-Archive-AI-Native/2.7",
        },
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
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_HTTP_ERROR",
            "httpStatus": int(getattr(error, "code", 0) or 0),
            "error": detail or f"OpenAI API HTTP {getattr(error, 'code', 'hatası')}",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        return {
            "ok": False,
            "errorCode": "AI_PROVIDER_NETWORK_ERROR",
            "error": f"AI sağlayıcısına bağlanılamadı: {error}",
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
        "provider": "openai",
        "providerResponseId": payload.get("id"),
        "providerCalled": True,
        "rawDocumentsUploaded": False,
        "retrievedSnippetsOnly": True,
        "store": False,
        "usage": usage,
    }
