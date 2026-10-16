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

## Arşiv-dosya merkezli AI kapsamı

Workspace içindeki ana AI deneyimi artık aktif indirme oturumuna bağlı değildir. AI kaynağı, yerel `UYAP_Arsiv` içindeki dava/iş dosyasıdır.

Akış:

1. Native Agent yerel arşivdeki dava dosyalarını `caseIndex` üzerinden listeler.
2. Kullanıcı AI panelinde dava dosyasını seçer.
3. Bir dava dosyası yalnız tüm mantıksal evrakları fiziksel olarak hazırsa AI'ya açılır.
4. İndeks oluşturulurken Native Agent her evrak için dosya varlığı, beklenen boyut ve SHA-256 değerini yeniden doğrular.
5. Seçilen dava dosyası için oturumdan bağımsız, izole bir yerel indeks oluşturulur.
6. Arama ve AI soruları yalnız bu seçili dava dosyasının indeksini kullanır.

Fail-closed hazır olma koşulu:

- `documentCount > 0`
- `physicalReadyCount == documentCount`
- eksik kayıt yok,
- problemli kayıt yok,
- boyut uyuşmazlığı yok,
- eski/stale metadata yok,
- indeksleme anındaki SHA-256 doğrulaması başarılı.

Tamamlanmamış dava dosyaları seçim listesinde görünür ancak AI sorgusu kapalı kalır. Böylece yarım bir indirme oturumu, `SWITCHED_TO_OTHER_JOB` durumu veya yeni bir tarayıcı oturumu dosyanın AI kapsamını daraltmaz.

Her dava dosyası ayrı bir `_ai/case-<scope>-v1.sqlite` indeksinde tutulur; böylece `D000001` gibi kaynak referansları farklı dava dosyaları arasında çakışmaz.

## Provider katmanı

Provider gateway Native Agent içinde çalışır. Chrome uzantısı API anahtarını hiçbir zaman almaz.

Desteklenen sağlayıcılar:

### OpenAI

- sağlayıcı seçimi: `UYAP_AI_PROVIDER=openai` — yalnız bu ayar açıkça yapılınca kullanılır;
  ayar yoksa, boşsa veya silinirse `OPENAI_API_KEY` tanımlı olsa bile yerel model kullanılır,
- anahtar kaynağı: `OPENAI_API_KEY` kullanıcı ortam değişkeni,
- model: `UYAP_AI_MODEL` (varsayılan `gpt-5.6-terra`),
- endpoint: OpenAI Responses API,
- provider request: `store=false`,
- ham dosya upload: kapalı.

### LM Studio (yerel, varsayılan)

- sağlayıcı seçimi: `UYAP_AI_PROVIDER=lmstudio` veya hiç ayar yapılmaması
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
9. Aktif oturumdan bağımsız dava dosyası seçimi ✅
10. Tam fiziksel arşiv + SHA-256 kapısı ✅
11. Dava dosyası başına izole AI indeksi ✅
