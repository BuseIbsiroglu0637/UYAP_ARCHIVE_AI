# UYAP Archive AI

UYAP ve UYAP Kurum arşivlerini doğrulanabilir biçimde yerel olarak arşivlemek, yönetmek ve daha sonra kaynaklı AI özellikleriyle genişletmek için geliştirilen ürün.

## Baseline

Aktarım baz sürümü: **RC15.9.38 + Native Agent 2.7.64**.

## Branch modeli

- `main`: doğrulanmış ana ürün hattı
- `release/rc15.9.38`: RC15.9.38 değişmez referans dalı
- `develop`: aktif entegrasyon dalı
- `feature/phase-1-product-foundation`: sonraki ürünleştirme geliştirmeleri

## Mimari ilke

Archive Engine; AI, üyelik ve cloud katmanlarından bağımsız tutulur. AI yalnızca doğrulanmış (`VERIFIED`) arşiv içeriğinden beslenir.

## Sonraki aşama

1. RC15.9.38 sürüm/test tutarlılığı
2. Native Agent 2.7.64 temiz kurulum, upgrade ve rollback doğrulaması
3. Core Certification / canlı UYAP + UYAP Kurum kabulü
4. Ürün modül sınırları ve canonical metadata şeması
5. Installer, signing ve licensing POC
6. Archive Workspace
7. Kaynaklı AI foundation
