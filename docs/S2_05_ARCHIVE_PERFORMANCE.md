# S2-05 | Yerel Arşiv Performansı — Kısa Ömürlü Salt Okunur Snapshot

**Tarih:** 15.10.2026
**Durum:** Ayrı performans dalı; CI geçti, gerçek müşteri arayüzünde saha ölçümü bekleniyor.
**Bağımlılık:** `fix/s2-04-paged-archive-registry` (taslak PR #18).
**Kapsam:** Yalnız yerel arşiv görüntüleme (özet + sayfa). Yeni indirme, arşiv veritabanı yazımı, AI kaynak güvenliği veya lisanslama yok.

## Sahada gözlenen performans problemi

İzole Windows/Chrome testinde 12.784 SQLite satırı, 4.192 benzersiz fiziksel evrak, 3 dava grubu olan arşivde, `list_archive_registry` sorguları yaklaşık 35–40 saniye sürdü. Çünkü S2-04 yanıtı 100 kayıtla sınırlar, fakat Native Agent her sayfa/dava değişiminde **SQLite satırlarını, fiziksel dosya varlığını ve disk klasörlerini yeniden tarar**.

## Teknik çözüm

- Native Agent `list_archive_registry`, yalnız `browseCache:true` için **process memory içinde** en çok 120 saniyelik, okuma amaçlı snapshot paylaşır.
- Tam disk taraması özet+evrak listesi olarak *yalnız Native Agent belleğinde* kalır. Her Native Messaging yanıtına yalnız sunucu filtreli, en fazla 100 kayıt eklenir.
- Ayrı `registry` Native Messaging lane'i bağlantısı 3 dakika boş kalana dek açık kalabildiği için izleyen isteklerde snapshot yeniden kullanılabilir; süreç kapanırsa cache doğal olarak kaybolur.
- `forceRefresh:true` eski snapshot'ı **kullanmaz**, taze disk taraması başlatır.
- Fingerprint veritabanı `_archive.db`, `_archive.db-wal`, `_archive.db-shm`, arşiv kökü ve `_incoming` mtime/size/inode alanlarını kontrol eder. DB/WAL veya üst düzey klasör değişimi cache'i düşürür.
- Alt klasörler ve tekil evrak dosyalarının sessizce değiştirilmesi bu hızlı imzayla anında yakalanamayabilir. Bu durumda **en geç 120 saniye sonra** yeniden disk taranır veya kullanıcı açık yenilemeyle bypass eder. Cached veriler hiçbir zaman download/AI bütünlük kararı olarak kabul edilmez.
- Bridge'de `browseCache` açıkça parametre olarak taşınır ve **varsayılanı false**. Yalnız Workspace özet/sayfa ve popup arşiv ön izlemesi `true` gönderir. Diğer sorgular her seferinde taze taranır.
- Sonuçta `snapshotCached` ve `snapshotAgeSeconds` metrikleri var; dava ve evrak bilgileri loglanmaz.

## CI regresyonları

`native/s2_05_archive_snapshot_cache_regression_test.py` şunları kontrol eder:
- 4.192 sentetik dokümanda özet ve 1./2. sayfa arka arkaya **tek disk taraması**, 100'lü sayfalama ve arama,
- `forceRefresh`, SQLite/WAL fingerprint değişimi, TTL sona ermesi ve arşiv kökü değişimiyle yeniden tarama,
- önbelleğe opt-in vermeyen kritik işlemlerin her zaman taze sorgu kullanması,
- hatalı sayfa parametrelerinin reddi ve cache'nin yalnız süreç içinde kalması,
- Workspace / Service Worker / download-manager parametre zincirinin bozulmaması.

S2-04 sayfalama regresyonu ve mevcut diğer testler de CI'da çalışır.

## Geliştirme ve güvenlik kapıları

1. Linux/Windows CI PASS (tüm commitler güncel olmalı).
2. Kaynak kod ve sentetik testler kullanıcı makinesinde; kurulu normal Agent ve uzantı değişmeden.
3. Yeni test EXE derlenir; **yalnız izole test host** `local.uyap_archive_ai.s204_test` üzerinden çalıştırılır. Normal `tr.gov.uyap.archive_agent` kaydı değiştirilmez.
4. Native Messaging test uzantısı S2-05 koduna alınır, portal izinleri yeniden eklenmez.
5. İzole Chrome DevTools'ta arşiv özetinden sonra sayfa 1/2 ve dava değişimi süresi ölçülür; `snapshotCached:true` beklenir. İlk soğuk tarama hâlâ 35–40 saniye sürebilir.
6. S2-04 dosya kimliği / mantıksal sayım farkı (3.457 / 3.496) ayrı soruşturma, önbellek ile maskelenmez.
7. Eski test Agent'a geri dönüş ve SQLite yedeği doğrulanır, main/develop/release kurulumu yapılmaz.

## Kısıtlar

- İlk disk taramasının hızlandırılması bu değişikliğin kapsamında değildir; hedef yalnız **ardışık sayfa/dava sorgularını** hızlandırmaktır.
- 2–5 saniyelik hedef **saha testi yapılmadan kabul edilmiş başarı olarak raporlanamaz**.
- Arşiv içeriğine ilişkin cache yanıtları her zaman `snapshotAgeSeconds` kadar eski olabilir. Yeniden indirme/avukat kararları için hiçbir zaman tek başına dayanak olarak kullanılmamalıdır.
