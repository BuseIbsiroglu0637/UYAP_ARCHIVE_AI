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

İlk sürümde provider kapalıdır. API anahtarı uzantı bundle'ına gömülmez.

Bir sonraki aşama provider gateway olacaktır. Gateway bağlandıktan sonra:

1. kullanıcı sorusu alınır,
2. VERIFIED yerel arşivden uygun kaynaklar bulunur,
3. yalnız gerekli bağlam sağlayıcıya gönderilir,
4. cevap `sourceRef` citation'larıyla döner,
5. citation olmayan hukuki iddialar UI'da güvenilir cevap olarak gösterilmez.

## Hedef iş akışları

- Dosya özeti
- Önemli tarihler ve süreler
- Son gelişmeler
- Taraflar ve roller
- Akıllı belge sınıflandırma
- Kronoloji
- Dosyada soru-cevap

## Sonraki teknik adımlar

1. Yerel metin çıkarma
2. Chunking
3. Yerel indeks
4. Retrieval
5. Provider gateway
6. Citation doğrulama
7. Workspace chat akışı
