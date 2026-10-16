# S2-06 | Yerel Arşiv Taraması - Satır Başına Disk Sorgusu Kaldırıldı

**Tarih:** 16.10.2026
**Bağımlılık:** `perf/s2-05-native-archive-snapshot-cache` (S2-05 önbelleği aynen kalır).
**Kapsam:** Yalnız `_build_archive_registry_snapshot` (salt okunur arşiv listesi). İndirme, arşiv yazımı, AI ve `safe_target()` kullanan yazma yolları değişmedi.

## Sorun nereden geliyordu

S2-05 ardışık sayfaları önbellekle hızlandırdı; ilk tarama ve 120 saniyelik önbellek süresi dolduktan sonraki tarama hâlâ yavaştı. Gerçek arşivde (16.325 SQLite satırı, 4.279 evrak) profil:

- Taramanın yaklaşık yarısı `Path.resolve()` içindeki Windows `GetFinalPathNameByHandle` çağrısıydı: **131.322 çağrı**, her biri dosyaya ayrı tanıtıcı açıyor (OneDrive klasöründe daha da pahalı).
- Her satır için 3 aday yol çözülüyor, ardından `is_file()` + `stat()` iki kez yapılıyordu; aynı dosyaya bağlı 16 bin satırın her biri için baştan.
- Fiziksel dosya keşfi `rglob` + dosya başına yeniden `resolve()` + `stat()` yapıyordu.

## Teknik çözüm

- `_ArchiveFileProbe`: her arşiv için **tek bir `os.scandir` gezintisi**; dosya boyutu/tarihi dizin listesinden gelir. Gezintide bulunmayan bir yol (arşiv dışı `evrak_<id>` dosyası, gezintiden sonra gelen indirme, farklı yazım) eskisi gibi `os.stat` ile sorulur ve önbelleğe alınır; yani cevap eski koddakiyle aynıdır.
- `_lexical_archive_target`: `safe_target()` ile aynı doğrulama (`..`, mutlak yol, sürücü harfi, ters bölü reddi + kök içinde kalma), ama diske dokunmadan. Yalnız salt okunur listede kullanılır; yazan her yol `safe_target()` kullanmaya devam eder.
- `_registry_row_path(..., probe)` ve `_portable_row_paths(..., lexical=True)`: yalnız tarama bu modu açar; parametresiz çağıranlar (`sync_inventory` vb.) birebir eski davranıştadır.
- Aynı `(kök, local_path, target_path)` bir taramada bir kez çözülür (16.325 satır → 4.204 çözüm).

## Gerçek arşivde doğrulama (salt okunur, aynı makine, aynı oturum)

| | Eski | Yeni |
|---|---|---|
| Tarama süresi | 67,5 sn | 4,3-4,5 sn |
| Evrak sayısı | 4.279 | 4.279 |
| Üst düzey sayaçlar (`presentFiles`, `missingFiles`, ...) | | birebir aynı |
| `caseIndex` | | birebir aynı |
| Evrak kayıtları (tüm alanlar) | | birebir aynı küme |

Sıralamada 6 konum farklı; hepsi **aynı saniyede değiştirilmiş** dosyalar arasında (sıralama anahtarı eşit). Eski sıra `rglob` iç sırasına bağlıydı, yenisi ada göre belirlenimci.

S2-05 saha ölçümünde ilk tarama ~35 sn idi; bu ölçümde eski kod 67 sn sürdü (makine yükü). Oran aynı koşulda ~15 kat.

## CI regresyonu

`native/s2_06_registry_fast_scan_regression_test.py` (sentetik arşiv, 2.006 satır):
- `_lexical_archive_target` geçerli yolda `safe_target` ile aynı sonucu verir, geçersiz yolların hepsini reddeder,
- her satır türünde (mevcut, boyut farkı, eksik, başka PC'den kopya DB, doğrulanmamış) hızlı ve eski yol çözümü aynı dosyayı, aynı varlık/boyut cevabını verir,
- tarama `Path.resolve()` çağrısını satır başına yapmaz (yeni kod: 1 çağrı; eski kod aynı veride 12.039 çağrı ile testi geçemez),
- durumlar (`archived`, `review`, `missing`, fiziksel-kayıtsız, `partial`) ve dava sayaçları doğru.

## Kısıtlar

- Arşiv içindeki bir bağlantı klasörü (junction) üzerinden gelen dosya, eski kodda `resolve()` ile kök dışına çıkıp listeden düşebiliyordu; yeni kodda yol yazıldığı gibi değerlendirilir. Listeleme salt okunur olduğundan güvenlik etkisi yok.
- Saha kabulü (izole Chrome'da ilk tarama süresi) ayrıca yapılmalıdır.
