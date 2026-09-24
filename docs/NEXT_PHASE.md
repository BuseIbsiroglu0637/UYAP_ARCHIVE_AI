# RC15.9.38 Sonrası Yol Haritası

## Baz sürüm

- UYAP Archive AI RC15.9.38
- Native Agent 2.7.64
- Kaynak paket dosya sayısı: 180
- Kaynak ZIP SHA-256: `b8e45caea3cbcd7834f5f5932a9ebb5754e381472dd8ed91ea6fb7da34ef5437`

## Bu branch'in amacı

`feature/phase-1-product-foundation` bundan sonraki ürünleştirme çalışmalarının ilk geliştirme dalıdır. Archive Engine çekirdeğinin davranışı gereksiz yere değiştirilmeden ürün kabuğu, sürüm sözleşmeleri ve dağıtım temeli hazırlanacaktır.

## İş sırası

1. RC15.9.38 test ve sürüm belgelerini güncel bazla eşleştir.
2. Native Agent 2.7.64 için clean install / upgrade / rollback matrisi oluştur.
3. Extension-Agent-DB sürüm uyumluluk sözleşmesini sabitle.
4. Connector / Core / Native / Workspace / Control Plane sınırlarını kod seviyesinde görünür hale getir.
5. Config ve environment yönetimini dev/stage/prod olarak ayır.
6. Structured logging ve privacy-light diagnostic paketini standartlaştır.
7. DB migration ve rollback iskeletini sabitle.
8. Alpha / beta / stable release kanallarını tanımla.
9. Core Certification için canlı UYAP ve UYAP Kurum kabul paketini tamamla.

## Çıkış kapısı

Bu branch tamamlandığında test edilebilir, semantik sürümlenmiş ve installer/licensing fazına hazır bir ürün çekirdeği bulunmalıdır.

## Değişiklik kuralı

Archive Engine 1.0 sınırına giren download / correlation / validation değişiklikleri regression, migration ve canary doğrulaması olmadan birleştirilmeyecektir.
