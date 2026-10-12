# Phase 1 | Structured Logging ve Privacy-Light Diagnostics

## Amaç

Tanılama, Archive Engine'i veya kullanıcı arşivini değiştiren bir çalışma zamanı bağımlılığı değildir. Hata araştırması için yeterli operasyonel sinyal üretir; ham hukuk verisini log veya destek paketine taşımaz.

## Olay şeması v2

Her yeni yapılandırılmış olay aşağıdaki alanları kullanır:

| Alan | Açıklama |
| --- | --- |
| `schemaVersion` | Diagnostic sözleşmesi sürümü; bugün `2` |
| `timestamp` | ISO-8601 zaman |
| `level` | `debug / info / warn / error` |
| `component` | Olayı üreten ürün katmanı |
| `event` | Kararlı, makine-okunabilir olay adı |
| `release` | Ürün release kimliği |
| `sessionRef` | Ham session kimliği değil; gerekiyorsa yerel referans |
| `errorCode` | Kararlı hata kodu |
| `durationMs` | Ölçülebilir operasyon süresi |
| `retryCount` | Retry sayısı |
| `metadata` | Sınırlı ve redakte edilmiş operasyonel metadata |

## Kesin gizlilik kuralları

Aşağıdaki veriler structured log'a doğrudan yazılmaz:

- dava numarası ve mahkeme/kurum adı,
- gerçek evrak / source document kimliği,
- dosya adı ve yerel mutlak yol,
- ham URL, query string ve auth token,
- belge içeriği, HTML, OCR/text payload,
- Authorization/Cookie veya secret/config anahtarı.

Belge seviyesinde korelasyon gerektiğinde ham kimlik yerine oturuma özgü kısaltılmış `documentRef` kullanılır. Eski diagnostic event'lerde kalmış `documentId` alanları dışa aktarım sırasında da referansa çevrilmeye devam eder.

## Depolama ve aktarım

- Varsayılan depolama yereldir.
- Cloud upload varsayılan olarak kapalıdır.
- Diagnostic kayıt hatası ana indirme kuyruğunu durduramaz.
- Diagnostic paket yalnız açık kullanıcı/destek akışı ile dışa aktarılmalıdır.
- `dev`, `stage` ve `prod` profillerinin tamamında `includeRawLegalData=false` ve `cloudUploadByDefault=false` zorunludur.

## Uygulama

- `shared/diagnostic-policy.js`: redaction ve event normalization.
- `background/operational-logger.js`: service worker operasyonel log adapter'ı.
- `background/download-manager.js`: session diagnostic event'lerini schema v2 ile yerel store'a yazar.
- `native/phase1_diagnostics_policy_test.mjs`: redaction ve contract CI testi.

## Archive Engine sınırı

Bu çalışma yalnız gözlemlenebilirlik katmanını değiştirir. Download, correlation, validation, SHA-256, VERIFIED kararı, arşiv yerleşimi veya final integrity davranışı değiştirilmez.
