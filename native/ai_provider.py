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
import unicodedata
from urllib.parse import urlsplit, urlunsplit

from ai_court_orders import court_orders_answer
from ai_important_dates import important_dates_answer
from ai_parties import parties_answer
from ai_workflows import (
    ALL_WORKFLOW_IDS, WORKFLOW_LABELS, workflow_context, EVENT_HINTS,
    DATE_IN_TEXT, _normalize_event_date,
)
from ai_case_facts import (
    SECTION_COVERAGE,
    SECTION_DEVELOPMENTS,
    SECTION_KUNYE,
    SECTION_LAST_HEARING,
    SECTION_NEXT,
    SECTION_OVERVIEW,
    SECTION_TIMELINE,
    find_dates,
    format_date,
    mask_identifiers,
    render_coverage_lines,
    render_fact_sections,
    tr_fold,
)
from ai_local_index import case_summary_facts, fit_snippet, search_index, summary_context

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
LMSTUDIO_RESPONSES_URL = "http://127.0.0.1:1234/v1/responses"
DEFAULT_OPENAI_MODEL = "gpt-5.6-terra"
DEFAULT_LMSTUDIO_MODEL = "qwen2.5-3b-instruct"
SUPPORTED_PROVIDERS = {"openai", "lmstudio"}
MAX_QUERY_CHARS = 1200
MAX_SOURCE_SNIPPET_CHARS = 2600
MAX_SOURCES = 8
MAX_OUTPUT_TOKENS = 1800
LMSTUDIO_MAX_SOURCES = 6
LMSTUDIO_MAX_SOURCE_SNIPPET_CHARS = 1400
LMSTUDIO_MAX_OUTPUT_TOKENS = 600
OPENAI_TIMEOUT_SECONDS = 120
LMSTUDIO_TIMEOUT_SECONDS = 300
SOURCE_REF_RE = re.compile(r"\[(D\d{6})\]")
SUMMARY_SECTION_HEADINGS = {
    "genel durum",
    "öne çıkan gelişmeler",
    "one cikan gelismeler",
    "önemli tarihler / süreler",
    "onemli tarihler / sureler",
    "taraflar, kurumlar ve roller",
    "belirsiz veya eksik kalan noktalar",
}


RISKY_ROLE_TERMS = (
    "davacı", "davalı", "sanık", "şüpheli", "müşteki", "mağdur",
    "bilirkişi", "avukat", "vekil", "hâkim", "hakim", "savcı",
    "soruşturma mühendisi", "yüksek mahkeme",
)


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


def _read_int_setting(name, default, minimum, maximum):
    raw, source = _read_user_setting(name, str(default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
        source = None
    return max(minimum, min(value, maximum)), source or "default"


def _provider_limits(provider):
    if provider == "lmstudio":
        timeout, timeout_source = _read_int_setting("UYAP_AI_TIMEOUT_SECONDS", LMSTUDIO_TIMEOUT_SECONDS, 30, 900)
        max_sources, sources_source = _read_int_setting("UYAP_AI_MAX_SOURCES", LMSTUDIO_MAX_SOURCES, 1, MAX_SOURCES)
        snippet_chars, snippet_source = _read_int_setting(
            "UYAP_AI_MAX_SOURCE_SNIPPET_CHARS", LMSTUDIO_MAX_SOURCE_SNIPPET_CHARS, 400, MAX_SOURCE_SNIPPET_CHARS
        )
        output_tokens, output_source = _read_int_setting(
            "UYAP_AI_MAX_OUTPUT_TOKENS", LMSTUDIO_MAX_OUTPUT_TOKENS, 128, MAX_OUTPUT_TOKENS
        )
    else:
        timeout, timeout_source = _read_int_setting("UYAP_AI_TIMEOUT_SECONDS", OPENAI_TIMEOUT_SECONDS, 30, 900)
        max_sources, sources_source = _read_int_setting("UYAP_AI_MAX_SOURCES", MAX_SOURCES, 1, MAX_SOURCES)
        snippet_chars, snippet_source = _read_int_setting(
            "UYAP_AI_MAX_SOURCE_SNIPPET_CHARS", MAX_SOURCE_SNIPPET_CHARS, 400, MAX_SOURCE_SNIPPET_CHARS
        )
        output_tokens, output_source = _read_int_setting(
            "UYAP_AI_MAX_OUTPUT_TOKENS", MAX_OUTPUT_TOKENS, 128, MAX_OUTPUT_TOKENS
        )
    return {
        "timeoutSeconds": timeout,
        "maxSources": max_sources,
        "maxSourceSnippetChars": snippet_chars,
        "maxOutputTokens": output_tokens,
        "sources": {
            "timeoutSeconds": timeout_source,
            "maxSources": sources_source,
            "maxSourceSnippetChars": snippet_source,
            "maxOutputTokens": output_source,
        },
    }


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
Kısa, doğal ve profesyonel Türkçe kullan. Bozuk, tekrar eden veya anlamsız kaynak parçalarını yorumlamaya çalışma.
Aynı olguyu farklı cümlelerle tekrarlama. Kaynakta olmayan hukuki sonuç, niyet veya kesinlik üretme.
"""


_SUMMARY_V1_INSTRUCTIONS = """UYAP Archive AI için kısa bir dava dosyası özeti hazırlıyorsun.
Yalnız sana verilen, uygulama tarafından deterministik kurallarla seçilmiş VERIFIED kaynak parçalarını kullan.
Kaynaklarda açıkça bulunmayan hiçbir olgu, taraf rolü, hukuki sonuç, niyet veya kesinlik üretme.
Kaynak etiketlerini metne yazma; kullanılan kaynakları uygulama ayrıca gösterecek.
Hukuki tavsiye verme ve kararın hukuki doğruluğunu değerlendirme.

Yanıt yalnız şu dört bölümden oluşsun:
DOSYANIN KONUSU
En fazla 2 kısa cümle.

SON DURUM
En fazla 2 kısa cümle; en güncel veya sonuç niteliğindeki kaynak açıkça desteklemiyorsa "Kaynaklardan kesin son durum çıkarılamadı." yaz.

ÖNE ÇIKAN GELİŞMELER
En fazla 5 kısa madde. Her madde tek somut gelişme olsun.

ÖNEMLİ TARİHLER
En fazla 5 satır. Yalnız kaynakta açıkça görülen tarih + kısa olay yaz.

Taraflar/roller bölümü oluşturma. Kaynakta olmayan kişi sıfatları ekleme. Tekrar yapma."""


def _is_summary_query(query):
    normalized = re.sub(r"\s+", " ", str(query or "").strip().casefold())
    return (
        "özet" in normalized
        or "ozet" in normalized
        or normalized in {"bu dosya", "dosya özeti", "dosya ozeti"}
    )


def _task_instructions(query):
    normalized = re.sub(r"\s+", " ", str(query or "").strip().casefold())
    summary_request = _is_summary_query(query)
    if not summary_request:
        return """Kullanıcının sorusunu doğrudan yanıtla.
Önce kesin kaynaklı bulguları ver; kaynakta olmayan veya belirsiz kalan kısmı ayrıca belirt.
Her olgusal cümleyi uygun [Dxxxxxx] referansıyla bitir."""

    return """Görev: Dosyanın yönetici düzeyinde, kaynaklı ve tekrarsız özetini hazırla.

Yanıtı şu başlıklarla ver; kaynaklarda veri yoksa ilgili başlığı atla:
1. Genel durum
2. Öne çıkan gelişmeler
3. Önemli tarihler / süreler
4. Taraflar, kurumlar ve roller
5. Belirsiz veya eksik kalan noktalar

Kurallar:
- Her madde tek bir somut olgu veya gelişme içersin.
- Her maddeyi, aynı satırın sonunda en az bir geçerli [Dxxxxxx] kaynağıyla bitir.
- Kaynağı olmayan maddeyi hiç yazma.
- Evrak başlığını tekrar ederek yer doldurma; içeriğin anlamını özetle.
- Bir kaynak yalnız teknik kayıt, sayı dizisi veya anlamsız veri içeriyorsa onu sonuç üretmek için kullanma.
- Taraf/rol bölümünde yalnız kaynakta açıkça adı veya unvanı geçen gerçek kişi ve kurumları yaz.
- Belge türlerini veya işlem adlarını kişi/rol gibi yorumlama. Örneğin 'müzekkere' bir taraf veya rol değildir.
- Kaynakta geçmeyen 'davacı', 'davalı', 'soruşturma mühendisi' gibi sıfat ve unvanları üretme.
- Bir kişi yalnız imza satırında görünüyorsa rolünü metinde açıkça belirtilen unvanla sınırla; unvan yoksa rol uydurma.
- 'Dosya kesin olarak şu aşamadadır' gibi sonuçları yalnız kaynak bunu açıkça destekliyorsa yaz; aksi halde aşama hakkında hüküm kurma.
- En fazla 8 kısa madde kullan; uzun giriş ve kapanış yazma."""


# Independent task contracts: one workflow may not silently fall back to another.
_WORKFLOW_TASKS = {
    "important_dates": """Görev: YALNIZ aşağıda açık olay ve tarihle ilişkilendirilmiş kanıtları listele.
Her kaynakta "Olay tarihi" ve "Olay türü" önceden seçilmiştir.
Cevap yalnız şu biçimdeki kısa satırlardan oluşsun:
- GG.AA.YYYY — Olay türü: kanıtlanan kısa açıklama [D000001]
Her satırdaki tarih, olay türü ve D kaynağı aynı kaynak bloğuna ait olmalı.
"Dava tarihi" ya da "dosya tarihi" genel alanını duruşma veya tebligat yerine yazma.
Hukuki süre sonu hesaplama; eksik tebliğ tarihi veya başlangıç varsa sonuç uydurma.
Kaynakta yer almayan karar, sonuç, taraf veya kesinlik üretme.
En fazla 6 kısa satır. Her satırın sonunda geçerli [Dxxxxxx] referansı.""",
    # Answered by rules (ai_court_orders) before any model call; kept so every
    # visible workflow has a task text.
    "court_orders": """Yalnız kaynakta yazan ara karar maddelerini, kimden ne istendiğini ve
verilen süreyi listele. Yapıldı/yapılmadı hükmü kurma. Her olguyu [Dxxxxxx] ile bitir.""",
    "parties": """Yalnız kaynakta açıkça yazan kişi/kurum ve rolünü çıkar.
Davacı/davalı/vekil/sanık gibi rolleri yalnız belge açıkça destekliyorsa yaz.
Sırf benzer isimlerden aynı kişi sonucu çıkarma. Her olguyu [Dxxxxxx] ile bitir.""",
}
# Workflows no longer shown as a card but still accepted from older builds.
# Akıllı Sınıflandırma and Kronoloji saw at most a handful of snippets: UYAP
# already gives every document's type, and the Zaman Çizelgesi page lists all
# documents by date, so the cards repeated those views with less coverage.
_LEGACY_WORKFLOW_TASKS = {
    "new_updates": """Yalnız yerel ilk karşılaştırma kaydından sonra yeni veya
değişmiş olduğu hash karşılaştırmasıyla belirlenen sağlanan evrakları incele.
Kaynak yoksa gelişme uydurma; silindiği veya sonuçlandığı gibi hükümler kurma.
Her olguyu [Dxxxxxx] ile bitir.""",
    "classification": """Yalnız sağlanan doğrulanmış evrak parçalarını belge türü
ve gerekçesiyle sınıflandır. Emin değilsen Belirsiz yaz. Belge başlığı ile
içerik çelişiyorsa bunu belirt. Tüm dosyanın sınıflandırıldığını iddia etme.
Her olguyu [Dxxxxxx] ile bitir.""",
    "chronology": """Kaynaklara göre olay kronolojisi çıkar. Belge tarihi ve
belgede anlatılan olay tarihi farklı olabilir; ayrı yaz.
Tarihsiz olaya tarih uydurma, olay sırası kesin değilse belirsiz de.
Her maddi olguyu [Dxxxxxx] ile bitir.""",
}

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
            "requestProfile": _provider_limits("lmstudio"),
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
        "requestProfile": _provider_limits("openai"),
    }


def _source_prompt(results, max_sources=MAX_SOURCES, max_snippet_chars=MAX_SOURCE_SNIPPET_CHARS):
    blocks = []
    for row in results[:max_sources]:
        source_ref = str(row.get("sourceRef") or "").strip()
        title = str(row.get("title") or "Adsız evrak").strip()[:240]
        document_type = str(row.get("documentType") or "DOSYA").strip()[:32]
        document_date = str(row.get("documentDate") or "").strip()[:64]
        # Decision-aware shortening: a hearing record's interim decisions and
        # next hearing date sit at its end, so never cut from the start only.
        snippet = fit_snippet(str(row.get("snippet") or ""), max_snippet_chars)
        blocks.append(
            f"[{source_ref}]\n"
            f"Başlık: {title}\n"
            f"Tür: {document_type}\n"
            f"Tarih: {document_date or '-'}\n"
            f"Metin:\n{snippet}"
        )
    return "\n\n---\n\n".join(blocks)



def _important_dates_source_prompt(results):
    """Pass only selected event-date context; archive date is not event proof."""
    blocks = []
    for row in results:
        blocks.append(
            f"[{row.get('sourceRef')}]\n"
            f"Evrak: {str(row.get('title') or 'Adsız evrak')[:180]}\n"
            f"Olay tarihi: {row.get('eventDate') or '-'}\n"
            f"Olay türü: {row.get('eventType') or '-'}\n"
            f"Olay-tarih kanıtı: {str(row.get('snippet') or '')[:500]}"
        )
    return "\n\n---\n\n".join(blocks)


def _validate_important_dates_answer(answer, selected):
    """Reject metadata-only 'Dava tarihi' and misattributed source dates."""
    allowed = {
        str(row.get("sourceRef") or ""): row
        for row in selected if row.get("eventDate") and row.get("eventType")
    }
    event_keywords = {label: words for label, words in EVENT_HINTS}
    checked = 0
    for raw in str(answer or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        refs = SOURCE_REF_RE.findall(line)
        dates = list(DATE_IN_TEXT.finditer(line))
        if not refs or not dates:
            return False, "AI_DATES_STRUCTURE_INVALID", {"reason": "Date and citation required per event."}
        normalized = [_normalize_event_date(match.group()) for match in dates]
        if not all(normalized):
            return False, "AI_DATES_INVALID_CALENDAR", {}
        supported = False
        for ref in refs:
            item = allowed.get(ref)
            if not item or any(value != item["eventDate"] for value in normalized):
                continue
            if (
                item["eventType"].casefold() in line.casefold()
                or any(term in line.casefold() for term in event_keywords.get(item["eventType"], ()))
            ):
                supported = True
                break
        if not supported:
            return False, "AI_DATES_UNSUPPORTED_EVENT", {"reason": "Date/event pair not supported by cited candidate."}
        checked += 1
    if not checked:
        return False, "AI_DATES_NO_GROUNDED_EVENTS", {}
    return True, None, {"validatedEvents": checked}


def _deterministic_date_candidates(results):
    """Fail-safe display for the dates workflow when the model fails grounding.

    These are explicitly *candidates* taken only from the selected local
    event-date extraction. Never add model claims or infer statutory deadlines.
    """
    lines = []
    seen = set()
    for row in results:
        ref = str(row.get("sourceRef") or "")
        event = str(row.get("eventDate") or "")
        event_type = str(row.get("eventType") or "")
        if not SOURCE_REF_RE.fullmatch("[" + ref + "]") or not event_type:
            continue
        if _normalize_event_date(event) != event:
            continue
        identity = (ref, event, event_type)
        if identity in seen:
            continue
        seen.add(identity)
        year, month, day = event.split("-")
        lines.append(
            f"- {day}.{month}.{year} — {event_type} adayı ["
            + ref + "]"
        )
    return "\n".join(lines)


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


def _markdown_plain_line(value):
    line = str(value or "").strip()
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^>\s*", "", line)
    # Strip full-line bold after heading/blockquote markers. Do not consume a
    # leading '*' here because that would turn '**Heading**' into '*Heading**'.
    line = re.sub(r"^(?:\*\*|__)(.*?)(?:\*\*|__)$", r"\1", line)
    return line.strip()


def _is_summary_heading(line):
    plain = _markdown_plain_line(line)
    plain = re.sub(r"^\d+[.)]\s*", "", plain).strip()
    plain = plain.rstrip(":").strip()
    normalized = re.sub(r"\s+", " ", plain).casefold()
    return normalized in SUMMARY_SECTION_HEADINGS


def _claim_units(answer):
    units = []
    for raw_line in str(answer or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Section headings are formatting, not factual claims. Qwen often emits
        # them with Markdown (**2. ...**, ### 2. ...), so normalize before the
        # citation gate rather than rejecting a correctly grounded answer.
        if _is_summary_heading(line):
            continue
        plain = _markdown_plain_line(line)
        numbered = re.match(r"^\d+[.)]\s*[^:]{1,90}:\s*(.+)$", plain)
        if numbered:
            plain = numbered.group(1).strip()
        plain = re.sub(r"^[-•*]\s*", "", plain).strip()
        words = re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]{2,}", plain)
        if len(words) >= 4:
            units.append(plain)
    return units


def _unsupported_roles(answer, source_text):
    """Role words in the answer that no source uses.

    Turkish-aware: "SANIK".casefold() is "sanik", never "sanık", so a plain
    casefold rejected every answer whose source wrote the role in capitals.
    """
    folded_answer, folded_sources = tr_fold(answer), tr_fold(source_text)
    return sorted({
        term
        for term in RISKY_ROLE_TERMS
        if tr_fold(term) in folded_answer and tr_fold(term) not in folded_sources
    })


def _validate_summary_v1(answer, source_text):
    text = str(answer or "").strip()
    if not text:
        return False, "AI_SUMMARY_EMPTY", {}
    unsupported_roles = _unsupported_roles(text, source_text)
    if unsupported_roles:
        return False, "AI_SUMMARY_UNSUPPORTED_ROLE", {"unsupportedRoles": unsupported_roles}
    normalized = unicodedata.normalize("NFKD", text.casefold().replace("ı", "i").replace("i̇", "i"))
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    required = ("dosyanin konusu", "son durum", "one cikan gelismeler", "onemli tarihler")
    missing_sections = [label for label in required if label not in normalized]
    if missing_sections:
        return False, "AI_SUMMARY_STRUCTURE_INVALID", {"missingSections": missing_sections}
    return True, None, {}


def _validate_grounding(answer, allowed_refs, source_text, summary_request=False):
    valid, error, invalid_refs = _validate_citations(answer, allowed_refs)
    if not valid:
        return False, error, {"invalidSourceRefs": invalid_refs}

    missing_claims = [
        unit[:220]
        for unit in _claim_units(answer)
        if not SOURCE_REF_RE.search(unit)
    ]
    if missing_claims:
        return False, "AI_RESPONSE_UNCITED_CLAIMS", {"uncitedClaims": missing_claims[:8]}

    cited_refs = list(dict.fromkeys(SOURCE_REF_RE.findall(answer or "")))
    if summary_request and len(allowed_refs) >= 2 and len(set(cited_refs)) < 2:
        return False, "AI_RESPONSE_INSUFFICIENT_SOURCE_DIVERSITY", {
            "citedSourceRefs": cited_refs,
            "minimumDistinctSources": 2,
        }

    unsupported_roles = _unsupported_roles(answer, source_text)
    if unsupported_roles:
        return False, "AI_RESPONSE_UNSUPPORTED_ROLE", {"unsupportedRoles": unsupported_roles}

    return True, None, {"citedSourceRefs": cited_refs}


# --------------------------------------------------------------------------
# Case summary v2: deterministic facts + a schema-bound, validated narrative.
#
# Künye, son celse ve ara kararlar, sonraki duruşma, süreler, akış ve kapsam
# are read from the sources by ai_case_facts without any model. The model only
# writes a short overview and up to five developments as JSON. Every narrative
# item is checked against the exact source text it cites and dropped if it
# fails; identifiers are masked before anything leaves this function.
# --------------------------------------------------------------------------
SUMMARY_SCHEMA_NAME = "case_summary_v2"
SUMMARY_SELECTION_POLICY = "deterministic_case_summary_v2"
SUMMARY_GROUNDING_MODE = "deterministic_facts_plus_validated_narrative_v1"
MAX_NARRATIVE_ITEMS = 5
MAX_NARRATIVE_ITEM_CHARS = 320

_NARRATIVE_ITEM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["metin", "kaynaklar"],
    "properties": {
        "metin": {"type": "string"},
        "kaynaklar": {"type": "array", "items": {"type": "string"}},
    },
}
NARRATIVE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["durum", "gelismeler"],
    "properties": {
        "durum": _NARRATIVE_ITEM_SCHEMA,
        "gelismeler": {"type": "array", "items": _NARRATIVE_ITEM_SCHEMA},
    },
}

_SUMMARY_V2_INSTRUCTIONS = """UYAP Archive AI için dava dosyası özetinin yalnız anlatım kısmını yazıyorsun.
Künye, son celse kararları, sonraki duruşma, süreler ve akış uygulama tarafından kaynaktan kuralla çıkarıldı; bunları tekrar etme ve onlarla çelişme.
Yalnız verilen VERIFIED kaynak parçalarını kullan. Kaynakta açıkça yazmayan hiçbir olgu, tarih, kişi, rol veya sonuç yazma.
Kişi adı yazma; kişilerden yalnız kaynaktaki sıfatıyla bahset (örneğin "sanık müdafii", "müşteki vekili").
"Belirtilmemiştir", "bilinmiyor" gibi bilgi yokluğu cümleleri kurma; bilgi yoksa o maddeyi hiç yazma.
Hukuki değerlendirme veya tavsiye yapma. Aynı gelişmeyi farklı sözcüklerle ikinci kez yazma.
Her maddenin "kaynaklar" alanına yalnız o maddeyi doğrudan destekleyen [Dxxxxxx] referanslarını yaz (köşeli parantez olmadan, örn. "D000075").
Yanıt yalnız şu JSON nesnesi olsun:
- durum: dosyanın genel durumunu anlatan en fazla 2 kısa cümle (metin + kaynaklar).
- gelismeler: en fazla 5 madde; her madde tek somut gelişme ve tarihini içersin (metin + kaynaklar)."""


def _chat_completions_url(responses_url):
    parsed = urlsplit(responses_url)
    path = parsed.path or ""
    path = path[: -len("responses")] + "chat/completions" if path.endswith("/responses") else "/v1/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _post_json(url, body, headers, timeout):
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _parse_json_object(text):
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip())
    for candidate in (raw, raw[raw.find("{"): raw.rfind("}") + 1] if "{" in raw else ""):
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _structured_narrative(status, config, headers, profile, instructions, user_input):
    """Request the narrative as schema-bound JSON, retrying once without the schema.

    LM Studio enforces json_schema on /v1/chat/completions (its /v1/responses
    endpoint ignores text.format); OpenAI enforces text.format on Responses.
    """
    schema_format = {"name": "uyap_case_summary_narrative", "strict": True, "schema": NARRATIVE_SCHEMA}
    if status["provider"] == "lmstudio":
        url = _chat_completions_url(config["endpoint"])
        if not _is_local_http_url(url):
            return {"parsed": None, "text": "", "errorCode": "AI_PROVIDER_LOCAL_ENDPOINT_REQUIRED"}
        base = {
            "model": status["model"],
            "temperature": 0.1,
            "max_tokens": profile["maxOutputTokens"],
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": user_input}],
        }
        attempts = [(url, {**base, "response_format": {"type": "json_schema", "json_schema": schema_format}}, "chat"),
                    (url, base, "chat")]
    else:
        base = {
            "model": status["model"],
            "store": False,
            "max_output_tokens": profile["maxOutputTokens"],
            "instructions": instructions,
            "input": user_input,
        }
        attempts = [(config["endpoint"], {**base, "text": {"format": {"type": "json_schema", **schema_format}}}, "responses"),
                    (config["endpoint"], base, "responses")]

    last_error = ("AI_PROVIDER_EMPTY_RESPONSE", None)
    for index, (url, body, kind) in enumerate(attempts):
        try:
            payload = _post_json(url, body, headers, profile["timeoutSeconds"])
        except urllib.error.HTTPError as error:
            last_error = ("AI_PROVIDER_HTTP_ERROR", f"HTTP {getattr(error, 'code', '')}")
            if index == 0 and int(getattr(error, "code", 0) or 0) in (400, 404, 422):
                continue
            break
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            last_error = ("AI_PROVIDER_NETWORK_ERROR", str(error)[:200])
            break
        if kind == "chat":
            choices = payload.get("choices") if isinstance(payload.get("choices"), list) else []
            message = choices[0].get("message") if choices and isinstance(choices[0], dict) else {}
            text = str((message or {}).get("content") or "")
        else:
            text = _extract_output_text(payload)
        parsed = _parse_json_object(text)
        return {
            "parsed": parsed,
            "text": text,
            "responseId": payload.get("id"),
            "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
            "schemaEnforced": index == 0,
            "errorCode": None if parsed else "AI_NARRATIVE_INVALID_JSON",
        }
    return {"parsed": None, "text": "", "errorCode": last_error[0], "error": last_error[1]}


_NO_INFORMATION = re.compile(
    r"(?i)(belirtilme(?:miş|mektedir)|bilinme(?:mekte|miş|yen)|bilinmiyor|emin olunma|tespit edileme|anlaşılama|yer almamakta)")
_HEARING_DENIAL = re.compile(
    r"(?i)duruşma\w*\s+(?:\w+\s+){0,3}?(?:yapılmadı|yapılmamış|gerçekleşmedi|gerçekleşmemiş|gerçekleştirilmemiş|açılmadı)")
# Sentences about the sources or the app ("bilgiler kaynaktan alınmıştır",
# "VERIFIED kaynaklar bulunmuştur") say nothing about the case. Kept narrow:
# "gelir kaynağı" or "HAGB uygulaması" are case content, not meta statements.
_META_STATEMENT = re.compile(
    r"(?i)\bverified\b|\bözet\w*|\bkaynak(?:tan|lar|lardan|larda|larına)\b|\bkuralla\b|\buygulaman[ıi]n\b"
    r"|\bbilgi\w*\s+(?:\w+\s+){0,4}?(?:alınmıştır|çıkarılmıştır|elde edilmiştir)")
# Last hearing / next hearing are rendered from the record itself; a model
# sentence repeating them adds nothing and can only drift from the source.
_RESTATES_RULE_SECTION = re.compile(r"(?i)\bson celse|sonraki duruşma|celse tarihi|duruşma (?:tarihi|günü) ve saat")
_ROLE_TOKENS = ("musteki", "sanik", "davaci", "davali", "supheli", "katilan", "magdur", "sikayetci",
                "alacakli", "borclu", "vekil", "mudafi")
_STOPWORDS = {"olarak", "ancak", "ayrıca", "üzere", "tarafından", "itibaren", "kadar", "sonra", "önce", "dosya",
              "dosyanın", "dosyada", "edilmiştir", "edildi", "olduğu", "bulunmaktadır", "yapılmıştır", "ilgili"}


def _content_prefixes(text):
    words = re.findall(r"[0-9A-Za-zÇĞİÖŞÜçğıöşüÂâÎîÛû]{5,}", str(text or ""))
    return {tr_fold(word)[:5] for word in words if tr_fold(word) not in {tr_fold(s) for s in _STOPWORDS}}


def _people_from_facts(facts):
    """(folded name, expected role tokens, label) for every person in the künye."""
    people = []
    for party in (facts or {}).get("parties") or []:
        role = tr_fold(party["role"])
        if party.get("name"):
            people.append((tr_fold(party["name"]), {role}, party["role"]))
        for counsel in party.get("counsel") or []:
            name = re.sub(r"^av\.?\s+", "", tr_fold(counsel["name"]))
            counsel_token = "mudafi" if "mudafi" in tr_fold(counsel["label"]) else "vekil"
            people.append((name, {role, counsel_token}, f"{party['role']} {counsel['label']}"))
    return people


def _role_mismatch(text, people):
    """Name mentioned with a role word that contradicts the künye (e.g. defence counsel as complainant's lawyer)."""
    folded = tr_fold(text)
    for name, expected, label in people:
        if len(name) < 5:
            continue
        for match in re.finditer(re.escape(name), folded):
            window = folded[max(0, match.start() - 45): match.start()]
            observed = {token for token in _ROLE_TOKENS if token in window}
            if observed and not observed <= expected:
                return label
    return None

def _validate_narrative_item(item, allowed_refs, snippets, facts, people, development=False):
    """Return (clean_item, None) or (None, reason)."""
    if not isinstance(item, dict):
        return None, "invalid_shape"
    text = re.sub(r"\s+", " ", str(item.get("metin") or "")).strip()
    refs = [str(ref).strip().strip("[]") for ref in (item.get("kaynaklar") or []) if str(ref).strip()]
    refs += SOURCE_REF_RE.findall(text)
    text = SOURCE_REF_RE.sub("", text).strip(" .;,") + "."
    refs = [ref for ref in dict.fromkeys(refs)]
    if len(text) < 12:
        return None, "too_short"
    if len(text) > MAX_NARRATIVE_ITEM_CHARS:
        text = text[: MAX_NARRATIVE_ITEM_CHARS - 1].rstrip() + "…"
    if not refs:
        return None, "missing_citation"
    if any(ref not in allowed_refs for ref in refs):
        return None, "unknown_citation"
    cited = "\n".join(snippets.get(ref, "") for ref in refs)
    if _NO_INFORMATION.search(text):
        return None, "no_information_claim"
    if _META_STATEMENT.search(text):
        return None, "meta_statement"
    if facts.get("lastHearing") and _HEARING_DENIAL.search(text):
        return None, "contradicts_hearing_record"
    missing_dates = find_dates(text) - find_dates(cited)
    if missing_dates:
        return None, "date_not_in_cited_source"
    if _unsupported_roles(text, cited):
        return None, "role_not_in_cited_source"
    if _role_mismatch(text, people):
        return None, "person_role_mismatch"
    if development and _RESTATES_RULE_SECTION.search(text):
        return None, "duplicates_rule_based_section"
    prefixes = _content_prefixes(text)
    if len(prefixes) >= 3:
        grounded = len(prefixes & _content_prefixes(cited)) / len(prefixes)
        if grounded < 0.5:
            return None, "weak_grounding"
        # Keep only references that support the item on their own. Procedural
        # records share a lot of vocabulary, so a reference must be close to the
        # best-supporting one; one carrying a date the item states always stays.
        item_dates = find_dates(text)
        support = {ref: len(prefixes & _content_prefixes(snippets.get(ref, ""))) / len(prefixes) for ref in refs}
        floor = max(0.25, 0.8 * max(support.values()))
        supporting = [
            ref for ref in refs
            if support[ref] >= floor or (item_dates & find_dates(snippets.get(ref, "")))
        ]
        kept_dates = set()
        for ref in supporting:
            kept_dates |= find_dates(snippets.get(ref, ""))
        if supporting and item_dates <= kept_dates:
            refs = supporting
    return {"text": text, "refs": refs, "prefixes": prefixes}, None


def _validate_narrative(parsed, allowed_refs, snippets, facts):
    people = _people_from_facts(facts)
    dropped = {}
    overview, developments = None, []
    if isinstance(parsed, dict):
        candidate, reason = _validate_narrative_item(parsed.get("durum"), allowed_refs, snippets, facts, people)
        if candidate:
            overview = candidate
        elif parsed.get("durum"):
            dropped[reason] = dropped.get(reason, 0) + 1
        for raw in (parsed.get("gelismeler") or [])[: MAX_NARRATIVE_ITEMS * 2]:
            candidate, reason = _validate_narrative_item(raw, allowed_refs, snippets, facts, people, development=True)
            if candidate is None:
                dropped[reason] = dropped.get(reason, 0) + 1
                continue
            if any(_near_duplicate(candidate, kept) for kept in developments + ([overview] if overview else [])):
                dropped["near_duplicate"] = dropped.get("near_duplicate", 0) + 1
                continue
            developments.append(candidate)
            if len(developments) >= MAX_NARRATIVE_ITEMS:
                break
    for item in developments + ([overview] if overview else []):
        item.pop("prefixes", None)
    return overview, developments, dropped


def _near_duplicate(first, second):
    a, b = first.get("prefixes") or set(), second.get("prefixes") or set()
    if not a or not b:
        return tr_fold(first["text"]) == tr_fold(second["text"])
    return len(a & b) / len(a | b) >= 0.6


def _known_facts_prompt(facts):
    lines = []
    if facts.get("court") or facts.get("caseNo"):
        lines.append("Mahkeme / dosya: " + " · ".join(p for p in (facts.get("court"), facts.get("caseNo")) if p))
    if facts.get("subject"):
        lines.append(f"{facts['subject']['label']}: {facts['subject']['text']}")
    if facts.get("lastHearing") and facts["lastHearing"].get("date"):
        lines.append("Son celse tarihi: " + format_date(facts["lastHearing"]["date"]))
    if facts.get("nextHearing"):
        time_text = f" saat {facts['nextHearing']['time']}" if facts["nextHearing"].get("time") else ""
        lines.append("Sonraki duruşma: " + format_date(facts["nextHearing"]["date"]) + time_text)
    return "\n".join(f"- {line}" for line in lines) or "- (kuralla çıkarılabilen olgu yok)"


def _compose_case_summary(fact_sections, overview, developments, coverage_lines):
    def tagged(item):
        return item["text"].rstrip(".") + "." + "".join(f" [{ref}]" for ref in item["refs"])

    order = (
        (SECTION_KUNYE, fact_sections.get(SECTION_KUNYE)),
        (SECTION_LAST_HEARING, fact_sections.get(SECTION_LAST_HEARING)),
        (SECTION_NEXT, fact_sections.get(SECTION_NEXT)),
        (SECTION_OVERVIEW, [tagged(overview)] if overview else None),
        (SECTION_DEVELOPMENTS, [tagged(item) for item in developments] or None),
        (SECTION_TIMELINE, fact_sections.get(SECTION_TIMELINE)),
        (SECTION_COVERAGE, coverage_lines),
    )
    blocks = []
    for heading, lines in order:
        if not lines:
            continue
        if heading == SECTION_OVERVIEW:
            blocks.append(heading + "\n" + "\n".join(lines))
        else:
            blocks.append(heading + "\n" + "\n".join(f"- {line}" for line in lines))
    return "\n\n".join(blocks)


def _answer_case_summary(status, profile, downloads_root, session_id, limit, case_context):
    context = case_context or {}
    facts_payload = case_summary_facts(
        downloads_root, session_id, context.get("institutionName"), context.get("caseId"))
    facts = facts_payload["facts"]
    coverage = dict(facts_payload["coverage"])
    if coverage.get("totalDocuments") is None and isinstance(context.get("totalDocuments"), int):
        coverage["totalDocuments"] = context["totalDocuments"]

    retrieval = summary_context(downloads_root, session_id, limit, snippet_chars=profile["maxSourceSnippetChars"])
    results = list(retrieval.get("results") or [])
    coverage["sentToAi"] = len(results)
    if not results and not facts.get("hearings"):
        return {
            "ok": True, "kind": "UYAP_AI_ANSWER", "schemaVersion": 1,
            "answer": "Doğrulanmış yerel kaynaklarda özetlenecek okunabilir içerik bulunamadı.",
            "sourceRefs": [], "sources": [], "model": status["model"], "provider": status["provider"],
            "providerCalled": False, "rawDocumentsUploaded": False, "store": False,
        }

    allowed_refs = {str(row.get("sourceRef")) for row in results if row.get("sourceRef")}
    snippets = {str(row.get("sourceRef")): fit_snippet(row.get("snippet"), profile["maxSourceSnippetChars"]) for row in results}
    user_input = (
        "Uygulamanın kaynaktan kuralla çıkardığı olgular (doğru kabul et, tekrar etme, çelişme):\n"
        + _known_facts_prompt(facts)
        + "\n\nUygulama tarafından seçilmiş VERIFIED yerel kaynaklar:\n\n"
        + _source_prompt(results, max_sources=profile["maxSources"], max_snippet_chars=profile["maxSourceSnippetChars"])
    )
    config = _provider_config()
    headers = {"Content-Type": "application/json", "User-Agent": "UYAP-Archive-AI-Native/2.7"}
    if status["provider"] == "openai":
        api_key, _ = _read_user_setting("OPENAI_API_KEY")
        headers["Authorization"] = "Bearer " + api_key

    narrative = {"parsed": None, "text": "", "errorCode": "AI_PROVIDER_NOT_CALLED"}
    if results:
        narrative = _structured_narrative(status, config, headers, profile, _SUMMARY_V2_INSTRUCTIONS, user_input)
    overview, developments, dropped = _validate_narrative(narrative.get("parsed"), allowed_refs, snippets, facts)

    coverage_lines = render_coverage_lines(coverage)
    kept = (1 if overview else 0) + len(developments)
    removed = sum(dropped.values())
    if narrative.get("errorCode") and not narrative.get("parsed"):
        coverage_lines.append(
            "AI anlatımı alınamadı (" + str(narrative["errorCode"]) + "); yukarıdaki bölümler kaynaktan kuralla çıkarıldı.")
    elif removed:
        coverage_lines.append(
            f"AI anlatımı: {kept} madde kaynakla doğrulandı, {removed} madde doğrulanamadığı için çıkarıldı.")
    fact_sections = render_fact_sections(facts)
    answer, masked_count = mask_identifiers(_compose_case_summary(fact_sections, overview, developments, coverage_lines))
    if masked_count:
        answer += f"\n- Gizlilik: {masked_count} kimlik / tebligat numarası maskelendi."

    cited = list(dict.fromkeys(SOURCE_REF_RE.findall(answer)))
    by_ref = {str(row.get("sourceRef")): row for row in results}
    titles = facts_payload.get("documents") or {}
    sources = []
    for ref in sorted(cited, key=lambda ref: (ref not in by_ref, ref)):
        row = by_ref.get(ref) or {}
        meta = titles.get(ref) or {}
        sources.append({
            "sourceRef": ref,
            "title": row.get("title") or meta.get("title"),
            "documentType": row.get("documentType") or meta.get("documentType"),
            "documentDate": row.get("documentDate") or meta.get("documentDate"),
            "selectionReason": row.get("selectionReason") or "Kural tabanlı künye / akış",
            "summaryRole": row.get("summaryRole") or "rule_based_fact",
        })

    result = {
        "ok": True,
        "kind": "UYAP_AI_ANSWER",
        "schemaVersion": 1,
        "answer": answer,
        "sourceRefs": cited,
        "sources": sources,
        "model": status["model"],
        "provider": status["provider"],
        "providerResponseId": narrative.get("responseId"),
        "providerCalled": bool(results),
        "rawDocumentsUploaded": False,
        "retrievedSnippetsOnly": True,
        "retrievalMode": "case_summary_v1",
        "summarySchema": SUMMARY_SCHEMA_NAME,
        "groundingMode": SUMMARY_GROUNDING_MODE,
        "selectionPolicy": SUMMARY_SELECTION_POLICY,
        "store": False,
        "usage": narrative.get("usage"),
        "narrative": {
            "status": "ok" if kept and not removed else ("partial" if kept else "unavailable"),
            "errorCode": narrative.get("errorCode"),
            "schemaEnforced": narrative.get("schemaEnforced"),
            "kept": kept,
            "dropped": removed,
            "droppedReasons": dropped,
        },
        "coverage": {key: coverage.get(key) for key in ("totalDocuments", "indexedDocuments", "sentToAi", "metaAvailable")},
        "maskedIdentifiers": masked_count,
    }
    if removed or narrative.get("errorCode"):
        preview, _ = mask_identifiers(str(narrative.get("text") or "")[:1200])
        result["diagnostic"] = {
            "provider": status["provider"],
            "model": status["model"],
            "summaryRequest": True,
            "retrievalMode": "case_summary_v1",
            "groundingMode": SUMMARY_GROUNDING_MODE,
            "selectionPolicy": SUMMARY_SELECTION_POLICY,
            "selectedSources": [
                {key: row.get(key) for key in ("sourceRef", "title", "documentType", "documentDate", "selectionReason", "summaryRole")}
                for row in results
            ],
            "droppedReasons": dropped,
            "narrativeErrorCode": narrative.get("errorCode"),
            "providerOutputPreview": preview,
            "providerResponseId": narrative.get("responseId"),
            "usage": narrative.get("usage"),
            "rawSourceSnippetsStored": False,
            "rawDocumentsStored": False,
        }
    return result


def answer_from_local_index(downloads_root, session_id, query, limit=MAX_SOURCES, workflow_id=None, case_context=None):
    # Legacy callers remain compatible; explicit Workspace IDs never inferred.
    if workflow_id is None:
        workflow_id = "case_summary" if _is_summary_query(query) else "ask"
    if workflow_id not in ALL_WORKFLOW_IDS:
        return {"ok": False, "errorCode": "AI_WORKFLOW_UNSUPPORTED",
                "error": "Tanımlanmamış AI iş akışı."}
    if workflow_id == "important_dates":
        # Dates are read from the records by rules; a model can only add
        # errors here, so none is called (and LM Studio need not run). The
        # model-based dates path below is kept only for legacy callers/tests.
        return important_dates_answer(downloads_root, session_id)
    if workflow_id == "court_orders":
        return court_orders_answer(downloads_root, session_id)
    if workflow_id == "parties":
        # Sides and counsel come from the record headers; no model needed.
        return parties_answer(downloads_root, session_id, case_context)
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

    profile = _provider_limits(status["provider"])
    requested_limit = max(1, int(limit or profile["maxSources"]))
    effective_limit = min(profile["maxSources"], requested_limit)
    summary_request = workflow_id == "case_summary"
    if summary_request:
        # Case summary v2: rule-based facts plus a validated model narrative.
        summary = _answer_case_summary(status, profile, downloads_root, session_id, effective_limit, case_context)
        return {**summary, "workflowId": "case_summary", "workflowTitle": WORKFLOW_LABELS["case_summary"]}
    try:
        retrieval = workflow_context(downloads_root, session_id, workflow_id, query, effective_limit)
    except ValueError as error:
        return {"ok": False, "errorCode": str(error), "error": "AI kaynak seçimi yapılamadı."}
    results = list(retrieval.get("results") or [])
    if not results:
        return {
            "ok": True,
            "kind": "UYAP_AI_ANSWER",
            "schemaVersion": 1,
            "answer": retrieval.get("notice") or "Doğrulanmış yerel kaynaklarda bu soruyu yanıtlayacak eşleşen içerik bulunamadı.",
            "workflowId": workflow_id,
            "workflowTitle": WORKFLOW_LABELS[workflow_id],
            "workflowNotice": retrieval.get("notice"),
            "workflowNoticeCode": retrieval.get("noticeCode"),
            "sourceRefs": [],
            "sources": [],
            "model": status["model"],
            "provider": status["provider"],
            "providerCalled": False,
            "rawDocumentsUploaded": False,
            "store": False,
        }

    allowed_refs = {str(row.get("sourceRef") or "") for row in results if row.get("sourceRef")}
    source_text = (
        _important_dates_source_prompt(results)
        if workflow_id == "important_dates"
        else _source_prompt(
            results,
            max_sources=profile["maxSources"],
            max_snippet_chars=profile["maxSourceSnippetChars"],
        )
    )
    body = {
        "model": status["model"],
        "store": False,
        "max_output_tokens": profile["maxOutputTokens"],
        "instructions": _SUMMARY_V1_INSTRUCTIONS if summary_request else (
            _PROVIDER_INSTRUCTIONS + "\n\n" + (
                _task_instructions(query) if workflow_id == "ask"
                else _WORKFLOW_TASKS.get(workflow_id) or _LEGACY_WORKFLOW_TASKS[workflow_id]
            )
        ),
        "input": (
            "Kullanıcı sorusu:\n"
            + query
            + "\n\nUygulama tarafından seçilmiş VERIFIED yerel kaynaklar:\n\n"
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
        with urllib.request.urlopen(request, timeout=profile["timeoutSeconds"]) as response:
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

    if summary_request:
        valid, grounding_error, grounding_detail = _validate_summary_v1(answer, source_text)
    else:
        valid, grounding_error, grounding_detail = _validate_grounding(
            answer,
            allowed_refs,
            source_text,
            summary_request=False,
        )
    if valid and workflow_id == "important_dates":
        valid, grounding_error, grounding_detail = _validate_important_dates_answer(answer, results)

    # A small local model may write an uncited preamble or unmatched event.
    # Do NOT paste citations onto its claims or disable grounding. Discard the
    # unsafe generated answer; show only deterministic event-date candidates.
    dates_fallback_used = False
    if not valid and workflow_id == "important_dates":
        safe_answer = _deterministic_date_candidates(results)
        if safe_answer:
            safe_valid, _, _ = _validate_grounding(
                safe_answer, allowed_refs, source_text, summary_request=False
            )
            if safe_valid:
                safe_valid, _, _ = _validate_important_dates_answer(safe_answer, results)
            if safe_valid:
                answer = safe_answer
                valid = True
                grounding_error = None
                grounding_detail = {}
                dates_fallback_used = True
    if not valid:
        return {
            "ok": False,
            "errorCode": grounding_error,
            "error": "AI yanıtı kaynak güvenlik kontrolünü geçemedi.",
            **grounding_detail,
            "diagnostic": {
                "provider": status["provider"],
                "model": status["model"],
                "summaryRequest": summary_request,
                "workflowId": workflow_id,
                "retrievalMode": "case_summary_v1" if summary_request else (
                    "lexical_search" if workflow_id == "ask" else f"workflow_{workflow_id}_v1"
                ),
                "groundingMode": "selected_verified_sources_v1" if summary_request else "claim_citations_v1",
                "selectionPolicy": "deterministic_case_summary_v1" if summary_request else None,
                "selectedSources": [
                    {
                        "sourceRef": row.get("sourceRef"),
                        "title": row.get("title"),
                        "documentType": row.get("documentType"),
                        "documentDate": row.get("documentDate"),
                        "selectionReason": row.get("selectionReason"),
                        "summaryRole": row.get("summaryRole"),
                    }
                    for row in results
                ],
                "providerOutputPreview": answer[:1200],
                "providerResponseId": payload.get("id"),
                "usage": payload.get("usage") if isinstance(payload.get("usage"), dict) else None,
                "rawSourceSnippetsStored": False,
                "rawDocumentsStored": False,
            },
        }

    cited_refs = (
        [str(row.get("sourceRef")) for row in results if row.get("sourceRef")]
        if summary_request
        else list(dict.fromkeys(SOURCE_REF_RE.findall(answer)))
    )
    sources = [
        {
            "sourceRef": row.get("sourceRef"),
            "title": row.get("title"),
            "documentType": row.get("documentType"),
            "documentDate": row.get("documentDate"),
            "selectionReason": row.get("selectionReason"),
            "summaryRole": row.get("summaryRole"),
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
        "workflowId": workflow_id,
        "workflowTitle": WORKFLOW_LABELS[workflow_id],
        "workflowNotice": (
            "LM Studio yanıtı kaynak denetiminden geçmedi. Gösterilen satırlar "
            "doğrulanmış yerel metinlerden kurallı çıkarılmış olay-tarih adaylarıdır; "
            "kesin hukuki süre hesabı veya olay doğrulaması değildir."
            if dates_fallback_used else retrieval.get("notice")
        ),
        "responseRecovery": "verified_date_candidates_v1" if dates_fallback_used else None,
        "modelAnswerAccepted": not dates_fallback_used,
        "sourceRefs": cited_refs,
        "sources": sources,
        "model": status["model"],
        "provider": status["provider"],
        "providerResponseId": payload.get("id"),
        "providerCalled": True,
        "rawDocumentsUploaded": False,
        "retrievedSnippetsOnly": True,
        "retrievalMode": "case_summary_v1" if summary_request else (
                    "lexical_search" if workflow_id == "ask" else f"workflow_{workflow_id}_v1"
                ),
        "groundingMode": "selected_verified_sources_v1" if summary_request else "claim_citations_v1",
        "selectionPolicy": "deterministic_case_summary_v1" if summary_request else None,
        "store": False,
        "usage": usage,
    }
