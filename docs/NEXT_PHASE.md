# RC15.9.38 Sonrası Yol Haritası

## Baz sürüm

- UYAP Archive AI RC15.9.38
- Native Agent 2.7.64
- Kaynak paket dosya sayısı: 180
- Kaynak ZIP SHA-256: `b8e45caea3cbcd7834f5f5932a9ebb5754e381472dd8ed91ea6fb7da34ef5437`

## 12 Ekim 2026 durumu

- 12.000+ evrak hacmi indirilebilir durumda; Phase 1 geliştirmesi başlayabilir.
- Core Certification kapanışı ayrı acceptance kanıtıyla sürdürülecek: silent loss / wrong binding, final integrity, recovery ve P0/P1 kapısı.
- Archive Engine download/correlation/validation davranışı ürünleştirme çalışması uğruna değiştirilmez.

## Bu branch'in amacı

`feature/phase-1-product-foundation` ürünleştirme çalışmalarının ilk geliştirme dalıdır. Archive Engine çekirdeği korunurken ürün kabuğu, sürüm/config sözleşmeleri ve modül sınırları sabitlenir.

## İş sırası

1. [x] RC15.9.38 test ve sürüm belgelerini güncel bazla eşleştir.
2. [ ] Native Agent 2.7.64 clean install / upgrade / rollback kabulünü tamamla. Windows CI lifecycle smoke hazır; gerçek imzalı makine kabulü bekliyor.
3. [x] Extension-Agent-DB sürüm uyumluluk sözleşmesini makine okunabilir hale getir.
4. [x] Connector / Core / Native / Workspace / Control Plane modül sınırlarını tanımla ve CI ile koru.
5. [x] Config ve environment yönetimi için `dev/stage/prod` politika sözleşmesini oluştur.
6. [x] Structured logging ve privacy-light diagnostic paketini standartlaştır. Diagnostic schema v2 + redaction policy + CI.
7. [x] DB migration ve rollback iskeletini sabitle. Versioned migration registry + downgrade gate + backup-required policy.
8. [x] Alpha / beta / RC / stable release kanallarını dağıtım kimliğine bağla. Packaging artık release-contract + release-channels okur.
9. [ ] Core Certification için canlı UYAP ve UYAP Kurum kabul paketini tamamla.

## Çıkış kapısı

Bu branch tamamlandığında test edilebilir, semantik sürümlenmiş ve installer/licensing fazına hazır bir ürün çekirdeği bulunmalıdır.

## Değişiklik kuralı

Archive Engine 1.0 sınırına giren download / correlation / validation değişiklikleri regression, migration ve canary doğrulaması olmadan birleştirilmeyecektir.
