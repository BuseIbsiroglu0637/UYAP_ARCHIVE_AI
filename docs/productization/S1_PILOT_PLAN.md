# S1-07 | Pilot Kullanıcı ve Ürün Keşfi Planı

**Tarih:** 15 Ekim 2026  
**Hedef segment:** Bireysel avukatlar ve 2–5 kişilik küçük hukuk büroları  
**Bağlayıcı kapsam:** DEC-S1-001 — Yerel arşiv çekirdeği + opsiyonel, ayrı kabul koşullu AI.  
**Durum:** TASLAK / GÖRÜŞMELER BAŞLATILMADI / DIŞ BETA BLOCKED.  
**Bağlantılar:** [S1-01](S1_PRODUCT_BRIEF.md), [S1-02](S1_MVP_SCOPE.md), [S1-03](S1_PORTAL_COMPLIANCE.md), [S1-04](S1_DATA_PRIVACY_MAP.md), [S1-06](S1_BETA_ACCEPTANCE.md).

## 1. Pilotun amacı ve ayrılması gereken iki aşama

**Aşama A — Güvenli ürün keşfi (Sprint 1 sonrası başlayabilir):** Üç ila beş gönüllü avukat veya küçük büro çalışanıyla, **hiçbir gerçek dava belgesi toplamadan** 25–35 dakikalık ihtiyaç görüşmeleri ve mümkünse sentetik dosyalarla görev gözlemi. Ürünü müşterinin UYAP oturumuna bağlama, otomatik dosya çekme, gerçek kişisel veri toplama veya dış veri aktarımı **yapılmaz**.

**Aşama B — Kontrollü teknik beta (henüz başlamaz):** [S1-06](S1_BETA_ACCEPTANCE.md) P0/P1 tüm kapıları kapatıldıktan, ilgili ürün/hukuk/gizlilik onayları verildikten, imzalı beta artefaktı doğrulandıktan ve gerekli katılımcı sözleşmeleri tamamlandıktan sonra gerçek cihazda sınırlı test. UYAP Portal otomasyonu bu betada ayrı hukuki/teknik GO olmadan **kapalı** kalır.

**Aşama A görüşmesinden olumlu geri bildirim almak, Aşama B dağıtım izni değildir.** Kullanıcının mevcut iş akışı hakkında görüşme yapabiliriz; test için kullanıcıyı gerçek dava dosyasını yüklemeye veya paylaşmaya yönlendirmeyiz.

## 2. Örneklem ve katılımcı profili

| Kod | Profil | Hedef | Neden |
| --- | --- | --- | --- |
| P-01 | Tek başına çalışan, farklı hukuk dosyaları olan avukat | 1–2 | İlk kurulum, arşiv ve arama ihtiyaçlarını görmek |
| P-02 | 2–5 kişilik büroda çalışan avukat | 1–2 | Bilgisayar değişimi, dosya devralma, yedekleme ve eğitim ihtiyacını görmek |
| P-03 | Büro operasyonunu takip eden yetkili kişi | 0–1 | Kurulum/destek yükünü ve arşiv düzenini anlamak |

**Toplam hedef:** 3–5 gönüllü katılımcı; küçük örneklem nicel pazar temsilini garanti etmez. Katılımcı seçimi yakın çevreye aşırı bağımlı olmamalı; farklı kıdem ve dava hacmi dahil edilmeye çalışılmalı.

**Ön koşullar:** Katılım gönüllüdür; randevu açık izinle planlanır; ses/video/ses kaydı varsayılan kapalıdır. İç notlarda avukat ve müvekkil kimlikleri anonim kodla gösterilir; bir müvekkilin dosyası veya ekranı görüntülenmez. Katılımcıya bu yazılımın UYAP/Adalet Bakanlığı resmî ürünü olmadığı açıklanır.

## 3. Ürün keşfi için standart 30 dakikalık görüşme

| Dakika | Bölüm | Sorular ve etkinlik |
| --- | --- | --- |
| 0–3 | Bilgilendirme | Araştırmanın amacı, veri kaydı yoksa kapsam, gönüllü bırakma hakkı ve bağımsız ürün notu |
| 3–8 | Mevcut alışkanlık | Evrakları nasıl düzenliyor? Bir dava belgesini aramak ne kadar sürüyor? Kopyalar nerede tutuluyor? |
| 8–14 | En acil sorun | Son bir ayda yaşanan yeniden indirme, belge kaybı, yanlış sürüm veya klasör karmaşası örneği (müvekkil bilgisi olmadan) |
| 14–23 | Sentetik görevler | Yerel arşive örnek belge ekle; listede bul; fiziksel doğrulama uyarısını yorumla; dosya aç |
| 23–27 | Opsiyonel AI | Kaynaklı özet örneği / belirsizlik ve 192/520 gibi metin kapsamı etiketi anlaşılır mı? AI kapalıyken değer yeterli mi? |
| 27–30 | Satın alma davranışı | Ürün denemeye engel olan faktör, alternatifler, fiyat algısı ve pilotta geri bildirim isteği |

**Görüşmeci kuralı:** “Bu ürün senin için harika değil mi?” gibi yönlendirme yok; önce gerçek problem ve bugünkü çözüm gözlemlenir. Görüşme esnasında gerçek dava bilgisi söylenirse notlara aktarılmaz.

## 4. Beş temel ihtiyaç sorusu

1. **Son 30 günde** aradığın bir dava evrakını bulmak için yaklaşık ne kadar zaman harcadın? Hangi yöntemle buldun?
2. Bilgisayarın değiştiğinde veya tarayıcı arşivi kaybolduğunda evraklarını nasıl geri getiriyorsun? En son ne zaman gerekli oldu?
3. Bir dosyanın gerçekten bilgisayarında bulunduğunu ve bozulmadığını nasıl doğruluyorsun?
4. Yerel arşiv ve arama için yapay zekâ **olmadan** para öder miydin? Hangi belirgin sonuç bu kararı değiştirirdi?
5. Kaynak gösteren AI özetine hangi şartlarda güvenir, hangi belirsizlikte mutlaka orijinal evrakı açarsın?

## 5. Görev protokolü ve kabul ölçütleri (sentetik / düşük risk)

| Görev | Sentetik koşul | Ölçüm | Beklenen kullanıcı davranışı |
| --- | --- | --- | --- |
| PILOT-A01 | Örnek 20 evraklı klasörden bir davayı içe al | Tamamlama / yardım / hata / süre | Kaynağı silmeden içe alma ve doğrulamayı görme |
| PILOT-A02 | 20 evraktan istenen dilekçeyi bul ve aç | Bulma süresi, yanlış seçim | Dava–evrak eşleştirme anlaşılır |
| PILOT-A03 | Bir evrakın boyutu/hash'i değişmiş | Hata farkındalığı / yorum | “Mevcut kayıt” ile “doğrulanmış” farkını anlar |
| PILOT-A04 | LM Studio ve internet erişimi yok | Görev tamamlama | Yerel arşiv ve arama çalışır |
| PILOT-A05 | Özet ekranında metni indeksli / toplam farklı | Kapsamın doğru anlaşılması | “Tüm evrak okundu” sanmaz |
| PILOT-A06 | Belge tarihinden farklı işlem tarihi | İddia ve kaynağı kontrol etme | Belirsiz son gün nedeniyle hukuki karara otomatik gitmez |

**Ölçüm:** Her görev için `SUCCESS_UNASSISTED / SUCCESS_ASSISTED / FAILURE / NOT_TESTED`, görevin sürümü, yaklaşık süre, yardım sayısı ve hatanın türü kaydedilir. Görevler her cihazda aynı sentetik veri ve aynı talimatlarla yapılmalıdır.

**Başarı hedefi (öngörü):** Pilot katılımcılarının en az %80'inin **dört çekirdek görevi** yardım almadan yapabilmesi. Örneklem 3–5 olduğu için n/N gerçek sayısı mutlaka raporlanır; örneğin 4/5 = %80. Ürün faydasını kanıtladığı iddia edilmez.

## 6. Gözlem ve anonim skor kartı

```yaml
participant_code: PILOT-001  # gerçek ad, telefon veya baro sicili içermez
cohort: solo_lawyer      # solo_lawyer | small_firm_lawyer | office_ops
session_phase: DISCOVERY # DISCOVERY | CONTROLLED_BETA
session_date: null
consent_for_anonymous_notes: false
audio_video_recording: false
synthetic_only: true
device_test_authorized: false # Aşama B için ayrıca
tasks:
  PILOT-A01: NOT_TESTED
  PILOT-A02: NOT_TESTED
  PILOT-A03: NOT_TESTED
  PILOT-A04: NOT_TESTED
time_to_find_minutes: null
most_valuable_feature_code: null
largest_obstacle_code: null
local_archive_without_ai_value_1_to_5: null
source_traceability_clarity_1_to_5: null
would_try_private_beta: null
price_feedback_band_optional: null
pii_or_client_content_recorded: false
evidence_reference_synthetic_only: null
```

Her katılımcı için ek yorum yalnız genel davranış biçiminde: “arşiv seçimini bulamadı”, “dosya kanıtını anladı” vb. İşlenen gerçek kişi/dava bilgisi, ses kaydı, e-posta ve yasal tanımlayıcı eklenmez. İletişim listesi gerekiyorsa araştırma notlarından ayrı erişim kontrollü tutulur; saklama ve silme politikası S1-04 üzerinden onaylanır.

## 7. Pilot araştırma sonuç raporu şablonu

- Toplam görüşülen kişi sayısı ve profil dağılımı (örn. 2 bireysel, 2 küçük büro).
- Mevcut çözüm yolları ve öne çıkan **ilk üç doğrulanmış problem**.
- Görev bazında n/N yardım almadan tamamlanma, medyan süre, sık karıştırılan terimler.
- AI isteğe bağlı olduğunda algılanan değer; *olmadan* da kullanma niyeti.
- Satın alma itirazları: veri güveni, kolay kurulum, fiyat, OCR, destek, resmî ürün görünümü.
- Her bulgudan bir ürün kararı: `KEEP / CHANGE / DEFER`, sahibi, iş ID ve sonraki test.
- Yalnız sentetik veya maskeli kanıt; sınırlı örneklem ve bilinmeyenler açıkça belirtilir.

## 8. Aşama B'ye geçiş için gerekli kapılar

- [ ] S1-06 beta testlerinin ilgili tüm P0/P1 koşulları PASS, imzalı kurulum ve rollback başarılı.
- [ ] S1-03 portal uygunluğu: otomasyon müşteri beta ortamında teknik olarak kapalı, yetkiyi aşan iş yok.
- [ ] S1-04 KVKK ve meslek sırrı incelemesi: dış pilot/veri işleme koşulları, destek ve silme süreçleri onaylı.
- [ ] Müşteri veri sınırı: sentetik veya yetkili/maskeli gerçek dosya, neyin kaydedildiği açık.
- [ ] Pilottaki her AI modülü bağımsız hukukçu ve kaynak doğruluk kabulünden geçti ya da gizli/kapalı.
- [ ] Beta katılımcısı kurulum, gizlilik, yazılımın bağımsızlığı, destek ve geri çekme koşulları hakkında bilgilendirildi.
- [ ] P0/P1 açık hata 0; yanlış dava bağlama 0; sessiz veri kaybı 0.

**Karar:** Aşama A **PLANLI / henüz uygulanmadı**; Aşama B **BLOCKED / dağıtım onayı yok**. S1-07 dokümanının hazırlanması katılımcı bulunması veya gerçek kullanım testi yapılması anlamına gelmez.

## 9. Sorumlu roller ve takvim

- **Ürün sorumlusu:** Katılımcı seçimi, soruların son onayı, görüşme sonuçları ve ürün kapsamı kararı.
- **Teknik sorumlu:** Sentetik demo hazırlığı; hiçbir portal/gerçek hukuk evrakını aktarmayan gösterim.
- **Hukuk/gizlilik danışmanı:** Araştırma veri işleme ve ilerideki beta katılım koşulları.
- **Katılımcı:** Gönüllü görüşme ve istenirse sentetik görev denemesi.

Önerilen uygulama: Sprint 1 kapanışında görüşme protokolü onaylanır; Sprint 2'de ilk 3–5 keşif görüşmesi planlanır. Beta cihaz kurulumu yalnız kapılar geçildikten sonra ayrı tarih ve ayrı kayıtla yapılır.

## 10. Referanslar

- [S1-06 Beta Kabul Matrisi](S1_BETA_ACCEPTANCE.md)
- [S1-04 Veri Güvenliği Haritası](S1_DATA_PRIVACY_MAP.md)
- [S1-03 Portal Uygunluğu](S1_PORTAL_COMPLIANCE.md)
- [GitHub Core Certification #2](https://github.com/BuseIbsiroglu0637/UYAP_ARCHIVE_AI/issues/2)
