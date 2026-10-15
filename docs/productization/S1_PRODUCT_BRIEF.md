# S1-01 | Ürün Tanım Belgesi — UYAP Archive AI

**Belge durumu:** KAPSAM KARARI ONAYLANDI / HUKUKÎ VE TEKNİK KABUL BEKLİYOR  
**Sprint:** Ürünleştirme Sprint 01 (14–21 Ekim 2026)  
**Tarih:** 15 Ekim 2026  
**Hedef segment:** Türkiye'de bireysel çalışan avukatlar ve 2–5 kişilik küçük hukuk büroları  
**Kaynak:** [Sprint 01 Planı](SPRINT_01_PLAN.md)  
**Not:** Bu metin planlanan ürün davranışını açıklar; bugün üretimde çalıştığını veya hukuki uygunluk onayını göstermez.

## 1. Ürün konumlandırması

**Çalışma adı:** UYAP Archive AI (ticari marka ve resmî kurumla karışma riski bakımından ayrıca incelenecek).

**Bir cümlelik değer önerisi (taslak; metnin nihai onayı bekliyor):**  
“Yetkili biçimde edinilen dava evraklarını bilgisayarında güvenle düzenle, dosya bütünlüğünü doğrula, aradığını hızla bul ve yalnız kaynağı görülebilen analizlerle incele.”

**Ürün türü:** Windows üzerinde çalışan yerel-öncelikli hukuk dosyası arşivleme ve belge inceleme aracı. Chrome/Edge uzantısı ile Native Agent mimarisine dayanır; mevcut arşiv motoru yapay zekâ sunucusuna ve lisans sunucusuna bağımlı olmamalıdır.

**Temel sınır:** Ürün resmî Adalet Bakanlığı veya UYAP ürünü/entegrasyonu olarak sunulmaz. Portalda otomatik işlem kabiliyeti, kullanım şartları ve gerekiyorsa yetkili onay değerlendirilmeden ticari müşteriye açılmaz. Ürünün adı, uzantı açıklaması ve `tr.gov.uyap.archive_agent` kimliği için markalama/uyumluluk kararı beklenir.

## 2. Birincil müşteri profili

### Persona P1 — Bireysel avukat (ana hedef)

- Windows dizüstü/masaüstü bilgisayar ve tarayıcı kullanır.
- Çok sayıda dosya ve ek belgeyi takip eder; farklı tarihlerdeki evrakları tekrar bulmak ister.
- Zaman kaybı: tekrar indirme, dağınık klasör yapısı, belge sürümlerini ayırt etme, zaman çizelgesi derleme.
- Önceliği: kendi verisini kontrol edebilmek, kayıp/yanlış dosya riskini azaltmak, anlaşılır kurulum ve kolay geri dönüş.
- Başarı: teknik uzmanlık gerektirmeden dosya içe aktarma, güvenli doğrulama, belgeyi açma ve sorunu anlama.

### Persona P2 — 2–5 kişilik küçük hukuk bürosu (ikincil hedef)

- Her çalışan kendi yetkili cihazı ve yerel dosya setinde çalışabilir.
- Kurulum/yeniden kurulum, bilgisayar değiştirme, yedekleme ve devralma kolaylığı önemlidir.
- Ortak bulut arşiv ve çok kullanıcılı yetki yönetimi ilk beta kapsamı dışındadır; ortak büro kullanımı için gereksinim keşfi gerekir.

## 3. Kullanıcının üç temel işi

**İş JTBD-01 — Evrakları güvenilir arşive alma:** “Yetkili olarak edindiğim PDF, UDF ve diğer desteklenen evrakları dava bazlı yerel arşivime almak; eksik/hatalı/mükerrer kayıtları görmek istiyorum.”

**İş JTBD-02 — Bir belgeyi hızlı bulma ve doğrulama:** “Dava numarası, evrak adı veya tarihle doğru belgeye ulaşmak; dosyanın gerçekte bilgisayarımda bulunduğunu ve kayıtlı kanıtıyla tutarlı olduğunu görmek istiyorum.”

**İş JTBD-03 — Belgelerden kontrollü bilgi edinme:** “Dosyada olanları ve önemli işlemleri kaynağına giderek görmek istiyorum. Yapay zekâ bir iddia üretiyorsa hangi evrakın desteklediği belli olsun; emin olmadığı konuda kesin hüküm kurmasın.”

## 4. Ürünün temel vaadi / vaat etmediği şeyler

**Sunduğu değer (kabul testinden sonra):**
- Yerel dava/evrak düzeni ve fiziksel bütünlük kanıtı.
- İnceleme, arama ve zaman çizelgesi ile erişim kolaylığı.
- Kaynaklı analizlerin, belge kapsamını ve belirsizliklerini açık gösteren kullanımı.

**Vaat edilmeyenler:**
- Resmî UYAP yetkilendirmesi veya Adalet Bakanlığı onayı.
- Her UYAP evrak türünde %100 metin çıkarımı; OCR henüz tamamlanmadı.
- Kullanıcı müdahalesi olmadan hukuki karar veya süre kesinliği.
- Mevcut kapsam dışındaki dosyalardan otomatik sonuç çıkarmak.
- Portal kullanım koşullarına aykırı otomatik toplu sorgulama/indirme.
- İlk sürümde bulut yedekleme, mobil uygulama, ekip içi senkronizasyon.

## 5. MVP V1 modül çerçevesi

| Özellik | Sprint 1 kararı | Kabul yaklaşımı |
| --- | --- | --- |
| Yerel arşiv içe alma ve dava klasörleri | KAPSAMDA ONAYLANDI / UYGULAMA KABULÜ BEKLİYOR | Portal erişimi gerekmeden sentetik veya kullanıcı izniyle edinilmiş belgelerde E2E |
| SHA-256 ve dosya eşleşme kanıtı | KAPSAMDA ONAYLANDI / UYGULAMA KABULÜ BEKLİYOR | Yanlış dosya/eksik dosya için açık hata; yanlış dava bağlanması = 0 |
| Evrak arama, filtreleme ve zaman çizelgesi | KAPSAMDA ONAYLANDI / UYGULAMA KABULÜ BEKLİYOR | Temel kullanıcı görevleri yardım almadan tamamlanır |
| Kurulum, onarım ve güvenli kaldırma | BETA ÖN KOŞUL | Gerçek Win10/11, Chrome/Edge, arşiv veri kaybı 0 |
| Kaynaklı Dosya Özeti | KOŞULLU | Kapsam/atıf açık; uzman onayı ve canlı kalite testi |
| Önemli Tarihler, Ara Kararlar, Taraflar, Tebligatlar | KOŞULLU / AYRI KABUL | Hukuk uzmanı testleri, uydurma kesin tarih ve rol yok |
| Otomatik UYAP / Kurum UYAP işlemleri | TİCARİ DAĞITIMA BLOKE | Resmî kullanım sözleşmesi, güvenlik ve gerekli izin kararı |
| Bulut veri senkronizasyonu ve takım yönetimi | V1 DIŞI | Sonraki keşif |

## 6. İlk kullanım senaryosu (altın yol)

1. Kullanıcı bağımsız yazılım kimliğini, veri işleme ve yerel saklama sınırlarını görür.
2. İmzalı beta paketiyle uygulamayı kurar; Native Agent bağlantı testi geçer.
3. Kendi yetkisiyle edinilmiş dosyaları / mevcut taşınabilir arşivini seçer.
4. Uygulama dosyaları dava bazında eşler, fiziksel dosya ve SHA-256 kontrolünü yapar.
5. Kullanıcı tam/eksik/hatalı/sınırlı metin kapsamını farklı durumlarla görür.
6. Kullanıcı bir belge arar ve açar; kaynak dosyaya erişebilir.
7. Analiz açıksa yalnız metni doğrulanmış belgelerden kaynaklı çıktı alır; değilse temel arşiv işlevi devam eder.

## 7. Teknik tasarım ilkeleri (karar önerileri)

- **Local-first:** Hukuk evrakı varsayılan olarak yerel cihazda; lisans ve sürüm servisi hukuk evrakını görmez.
- **Archive-first:** AI veya ağ sağlayıcısı çevrimdışıyken arşivleme/arama fonksiyonları mümkün olduğu ölçüde çalışır.
- **Verified-only:** İçerik-temelli AI, doğrulanmış dosyaların okunabilir ve kabul edilmiş metnine dayanır.
- **Fail-closed:** Yetki, kaynak, imza, hash ve karışan dosya koşullarında işi güvenli durdur.
- **Reversible upgrades:** Güncelleme/onarım/kaldırma arşivleri silmemeli; geri alma ve şema uyumluluğu kanıtlanmalı.
- **Data minimization:** Tanı ve destek loglarında belge içeriği, kişi kimliği ve dava adları varsayılan olarak bulunmamalı.
- **Clear provenance:** AI raporunda dayanak kaynak, indeks kapsamı ve belirsizlik açıkça görülebilmeli.

## 8. Ölçülebilir pilot başarı ölçütleri (hedef; henüz ölçülmedi)

| Ölçüt | Hedef / kabul |
| --- | --- |
| Yanlış dava ile ilişkilendirilmiş evrak | 0 (pilot kabulünde kritik P0) |
| Sessiz dosya veri kaybı | 0 |
| Fiziksel arşiv bütünlük doğrulaması | Tüm kabul vakaları PASS |
| 4 ana görev akışının tamamlanması | Pilot kullanıcıların en az %80'i yardımsız (küçük örneklem; yorum için n belirtilir) |
| Kurulum/onarım/kaldırma | Win10/11 + Chrome/Edge matrisindeki kritik senaryolar PASS |
| Kritik açık kusurlar | P0=0, P1=0 beta yayını öncesinde |
| AI hukuki kaynak/rol/tarih doğruluğu | Önce uzman onaylı örnek set + ölçüm yöntemi kurulacak; doğrulanmamış yüzde vaat edilmeyecek |
| Pilot katılımcı | İzinli 3–5 avukat veya büro katılımcısı (hedef) |

## 9. Ürün kararları ve açık sorular

| Karar | Durum | Sorumlu rol | Sonraki adım |
| --- | --- | --- | --- |
| Bireysel avukat + 2–5 kişilik büro hedefi | KABUL EDİLDİ (ürün sahibi kullanıcı tercihi) | Ürün sahibi | İlk görüşme senaryosuna aktar |
| Yerel arşiv öncelikli konumlandırma | ONAYLANDI (15.10.2026) | Ürün sahibi | Yerel çekirdek beta kabul planı |
| Windows + Chrome/Edge beta | ÖNERİ | Ürün sahibi / teknik | İlk pilot ortamını seç |
| Portal otomasyonu ticari betada kapalı | GÜVENLİK İÇİN BLOKE / UYGUNLUK İNCELEMESİ | Hukuk ve ürün | Güncel UYAP sözleşmesi / resmî görüş |
| AI opsiyonel modül olarak ayrı kabul şartına tabi | ONAYLANDI (15.10.2026), hukukî çıktı kabulü BEKLİYOR | Hukuk uzmanı | Sentetik + izinli vaka doğrulama |
| Ticari marka / Native Host kimliği | AÇIK | Ürün/marka/teknik | Resmî ürün izlenimi riskini gider |
| Lisans türü ve fiyat | AÇIK | Ürün sahibi | Pilot ödeme isteği araştırması |
| Lisans sunucusu & bulut AI | AÇIK | Teknik/KVKK | Ayrı veri akışı ve izin gereksinimleri |

## 10. S1-01 tamamlandı sayılma şartı

- [x] Persona P1/P2 tanımlandı.
- [x] Üç temel müşteri işi belirlendi.
- [x] Tek cümle değer önerisi taslağı yazıldı.
- [x] V1 çekirdeği ile koşullu/harici özellikler ayrıldı.
- [x] Altın yol ve ölçülebilir pilot hedefleri yazıldı.
- [x] Ürün sahibi **yerel arşiv + opsiyonel AI** kapsamını 15 Ekim 2026'da onayladı; değer vaadinin nihai pazarlama metni ayrıca gözden geçirilecek.
- [ ] Portal uygunluğu için hukukçu/ilgili kurum karar yolu netleştirildi.

**Statü:** Ürün kapsamı ONAYLANDI; marka/değer vaadi metni, teknik gerçekleştirim ve portal hukukî uygunluğu açık. Sprint S1-01'in bütün kapanış şartları henüz bitmedi.

## İlişkili işler

- [Sprint 01 Planı](SPRINT_01_PLAN.md)
- [Product Foundation #3](../../issues/3)
- [Core Certification #2](../../issues/2)
- [Windows installer kabulü #4](../../issues/4)
- [Tanılama veri maskesi #6](../../issues/6)
- [AI geliştirme PR #10](../../pull/10)


## 11. Karar kaydı — DEC-S1-001

- **15 Ekim 2026, ürün sahibi onayı:** İlk beta çekirdeği yerel içe aktarma + arşiv doğrulama + arama; kaynaklı AI isteğe bağlı, kendi kabul testine bağlı.
- **Ticari sınır:** UYAP/Kurum UYAP otomasyonunun ticari açılması, resmî kullanım sözleşmesi/izin ve hukukî değerlendirme tamamlanmadan BLOKE.
- **Değişmeyenler:** İmzalı dağıtım, KVKK veri haritası, uzman AI incelemesi, pilot ve saha kabulü açık. Bu karar yayınlama yetkisi vermez.
- **Bağlantılar:** [S1-02](S1_MVP_SCOPE.md), [S1-03](S1_PORTAL_COMPLIANCE.md), [S1-04](S1_DATA_PRIVACY_MAP.md).
