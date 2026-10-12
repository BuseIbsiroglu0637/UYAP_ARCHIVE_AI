# UYAP Archive AI | AI Entegrasyonu

## Amaç

AI katmanı, Archive Engine'in üzerine eklenen bağımsız bir analiz katmanıdır. Arşivleme, correlation, doğrulama, SHA-256 ve final integrity kararları AI'dan etkilenmez.

## Güvenlik sınırı

AI bağlamına yalnız:

- `verified` durumundaki evraklar,
- veya `skipped` olup güncel SHA-256 fiziksel kanıtı bulunan evraklar

girebilir.

`queued`, `retry_wait`, `review_required`, `failed` ve fiziksel kanıtı olmayan kayıtlar AI bağlamına alınmaz.

## Context v1

`background/ai-context.js` ham dosya içeriği göndermeden güvenli bir kaynak manifesti üretir.

Her kaynak, kullanıcıya gösterilebilen ve cevaplarda kullanılacak bir referans alır:

- `D000001`
- `D000002`
- ...

Context v1 şunları içerir:

- belge başlığı,
- belge türü,
- belge tarihi,
- kaynak sistemi,
- arşiv doğrulama durumu,
- citation referansı.

Şunları içermez:

- mutlak Windows yolları,
- UYAP belge kimlikleri,
- token / cookie / URL sorguları,
- ham hukuki belge içeriği.

## Provider katmanı

Provider gateway Native Agent içinde çalışır. Chrome uzantısı API anahtarını hiçbir zaman almaz.

Desteklenen sağlayıcılar:

### OpenAI

- sağlayıcı seçimi: `UYAP_AI_PROVIDER=openai`
- anahtar kaynağı: `OPENAI_API_KEY` kullanıcı ortam değişkeni,
- model: `UYAP_AI_MODEL` (varsayılan `gpt-5.6-terra`),
- endpoint: OpenAI Responses API,
- provider request: `store=false`,
- ham dosya upload: kapalı.

### LM Studio (yerel)

- sağlayıcı seçimi: `UYAP_AI_PROVIDER=lmstudio`
- model: `UYAP_AI_MODEL` (varsayılan `qwen2.5-3b-instruct`),
- endpoint: `UYAP_AI_BASE_URL` (varsayılan `http://127.0.0.1:1234/v1/responses`),
- API anahtarı gerekmez,
- güvenlik nedeniyle yalnız `localhost`, `127.0.0.1` veya `::1` kabul edilir,
- ham evrak buluta gönderilmez; yalnız yerel retrieval'ın seçtiği kalite-kapılı snippet'ler yerel modele verilir.

Gateway akışı:

1. kullanıcı sorusu alınır,
2. VERIFIED yerel indeksten en ilgili kaynak parçaları bulunur,
3. sadece `D000xxx` sourceRef + başlık/tür/tarih + snippet sağlayıcıya gönderilir,
4. yanıtın yalnız sağlanan `D000xxx` referanslarını kullandığı doğrulanır,
5. citation yoksa veya bilinmeyen citation varsa yanıt UI'a verilmez.

## Windows'ta LM Studio geliştirme ayarı

PowerShell:

```powershell
[Environment]::SetEnvironmentVariable("UYAP_AI_PROVIDER", "lmstudio", "User")
[Environment]::SetEnvironmentVariable("UYAP_AI_MODEL", "qwen2.5-3b-instruct", "User")
[Environment]::SetEnvironmentVariable("UYAP_AI_BASE_URL", "http://127.0.0.1:1234/v1/responses", "User")
```

LM Studio Model Server portu `1234` üzerinde çalışmalı ve model `/v1/models` içinde görünmelidir.

## Hedef iş akışları

- Dosya özeti
- Önemli tarihler ve süreler
- Son gelişmeler
- Taraflar ve roller
- Akıllı belge sınıflandırma
- Kronoloji
- Dosyada soru-cevap

## Sonraki teknik adımlar

1. Yerel metin çıkarma ✅
2. Chunking ✅
3. Yerel indeks ✅
4. Retrieval ✅
5. Provider gateway ✅
6. Citation doğrulama ✅
7. Workspace chat akışı ✅ (ilk tek-soru akışı)
8. Çoklu provider seçimi ✅ (OpenAI + LM Studio)
