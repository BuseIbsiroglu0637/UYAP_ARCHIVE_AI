# Phase 1 | DB Migration ve Rollback Sözleşmesi

## Amaç

IndexedDB şeması artık tek bir `onupgradeneeded` bloğunda dağınık kurallar yerine versioned migration registry üzerinden yönetilir. Bu değişiklik mevcut DB sürümünü **6'da tutar** ve çalışan arşiv verisinin biçimini değiştirmez.

## Mevcut durum

- Database: `uyap-profesyonel-arsiv-v1`
- Current DB version: `6`
- Migration framework: `1`
- Bugünkü migration'lar additive/idempotent kabul edilir.
- Kaynak kodda doğrulanabilen tek veri dönüşümü: `oldVersion < 5` kayıtlarında `warningFlag` ve `retryPriority` türetilmesi.
- Mevcut source, version 6'nın tarihsel olarak hangi tek değişiklikle geldiğini açıklamıyor; bu nedenle belge bunu uydurmaz.

## Upgrade kuralı

`background/db.js`, IndexedDB upgrade olayını `background/db-migrations.js` içindeki `applyDatabaseUpgrade()` fonksiyonuna devreder.

Migration registry:

1. hedef DB sürümünü doğrular,
2. bilinen store/index setini idempotent olarak sağlar,
3. yalnız kaynakta kanıtlanan legacy dönüşümü uygular,
4. bilinmeyen target version veya downgrade isteğinde fail-closed davranır.

## Rollback kuralı

IndexedDB üzerinde in-place downgrade yapılmaz.

Şu an güvenli rollback yalnız şu durumda kabul edilir:

- önceki uygulama sürümü aynı DB version 6 ile uyumluysa.

Gelecekte store/index/field silen veya yeniden adlandıran **destructive migration** yapılacaksa release kapısı kapanmadan önce:

- pre-migration backup,
- restore testi,
- N-1 -> N upgrade testi,
- N -> önceki uygulama rollback compatibility testi

zorunludur.

Bugün destructive migration için backup/restore mekanizması **henüz uygulanmış değildir**. Bu eksik açıkça `config/storage-migrations.json` içinde kayıtlıdır; dolayısıyla gelecek destructive migration CI/release review'da engellenmelidir.

## Archive Engine sınırı

Bu refactor download/correlation/validation, SHA-256, VERIFIED veya fiziksel arşiv kararlarını değiştirmez. Yalnız IndexedDB şema upgrade kodunun yönetim biçimini standartlaştırır.
