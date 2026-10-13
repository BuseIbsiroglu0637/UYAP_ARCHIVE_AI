# İç test tabanı | 24 Eylül 2026

Bu belge gerçek UYAP ortamında bir sertifikasyon raporu değildir.

## Kaynak baz durumu

- Kullanıcının RC15.9.38 / Native 2.7.68 ZIP'inden 180 dosya, `main`, `release/rc15.9.38`, `develop` ve `feature/phase-1-product-foundation` dallarına kaynak dosyalar halinde aktarıldı.
- Tam ZIP SHA-256: `b8e45caea3cbcd7834f5f5932a9ebb5754e381472dd8ed91ea6fb7da34ef5437`.
- RC paketinde 11 geçmiş regresyon testi `RC15.9.37 QUEUE-RECOVERY` adını sabit bekliyordu; bunlar yeni RC paketinde sahte sürüm hatası üretiyordu. Bu branch'te sürüm ailesi kontrolüne çevrildi; ayrıca tek kesin `current_release_identity_test.py` eklendi.

## Yerel doğrulama

- Kaynak bazda 61 Python dosyasının derleme/sözdizimi kontrolü: geçti.
- Kaynak bazda 35 JS/MJS dosyasının `node --check` kontrolü: geçti.
- Sürüm kontrolü düzeltilmiş yerel çalışma kopyasında 18 ilgili RC regresyon testi: geçti.
- Tüm regresyonların baştan sona yeşil olduğu **iddia edilmiyor**; geniş çaplı ilk çalıştırma bu ortamın zaman sınırını aştı.

## Devam eden işler

1. CI üzerindeki çevrimdışı smoke kontrollerinin sonucunu izle.
2. Windows 10/11 gerçek Native Agent kurulum, yükseltme, rollback ve Chrome/Edge bağlantı testlerini kayıt altına al.
3. UYAP ve UYAP Kurum için ayrı canlı canary ve 12.000+ evrak bütünlük kabul raporlarını tamamla.
4. Açık P0/P1 hata kalmadığında Core 1.0 freeze kararı ver.


## 13 Ekim 2026 güncellemesi

- RC15.9.38 kaynak ağacı Native Agent 2.7.68 ile hizalandı.
- SOURCE_FILE_MISSING için 100/250/500/1000 ms sınırlı dosya görünürlük polling'i ve üç farklı evrakta sistemik devre kesici eklendi.
- sourcePathRecoveryChecks Native Agent yanıtına taşınarak path kurtarma tanılaması görünür hale getirildi.
- AI dava özeti, yerel özet geçmişi ve A4/PDF çıktı akışı kaynak ağacında korunuyor.
- Otomatik CI smoke kontrolleri kaynak/sözdizimi/regresyon ve Windows installer yaşam döngüsü sözleşmesini doğrular; gerçek UYAP/Windows canlı acceptance ayrıca yapılmalıdır.
