# UYAP Archive AI — Ürünleştirme Sprint 01 (plan taslağı)

**Dönem:** 14–21 Ekim 2026. Beş iş günlük üretim: 14, 15, 16, 19, 20 Ekim; 21 Ekim karar/onay günü.  
**Hedef kitle:** Bireysel avukatlar ve 2–5 kişilik küçük hukuk büroları.  
**Sprint tipi:** Karar, gereksinim, risk ve kabul tasarımı; üretime çıkış sprinti **değildir**.  
**Durum:** DEVAM EDİYOR — DEC-S1-001 ürün kapsamı onaylandı; S1-03/S1-04 inceleme taslakları hazır, hukukî/gizlilik/saha sertifikasyonu tamamlanmadı.

## 1. Ürün hedefi ve kapsam sınırı

**İlk ürün vaadi:** Avukatın hukuka uygun olarak edinilmiş dava evraklarını kendi Windows cihazında içe aktarıp doğrulayabildiği, dosya bazında düzenleyip arayabildiği, mevcut kaynaklı analiz özelliklerini yalnız doğrulandıkları ölçüde kullanabildiği bağımsız arşiv ürünü.

**Önerilen v1 beta çekirdeği:**
- Yerel dosya içe aktarma / klasör seçimi, dava–evrak ilişkilendirme ve dosya listesi.
- SHA-256 fiziksel doğrulama; tutarsız/eksik/mükerrer dosya statüleri.
- Arama, zaman çizelgesi, anlaşılır hata/sorun ekranı.
- Yerel çalışma, güvenli tanılama, kurulum/onarım/yedekten geri dönüş.
- Çalışması doğrulanmış kaynaklı özet; diğer analitik akışlar bağımsız kabul kapısına bağlı.

**Şarta bağlı:** UYAP Avukat/Kurum Portal'da otomatik sorgulama veya toplu indirme. Resmî sözleşme, güvenlik politikası ve gerekiyorsa yazılı izin/onay değerlendirmesi tamamlanmadan **ticari beta kapsamına alınmaz**. Kapasite kısıtını aşmamaya çalışmak, açık robot-yazılım kısıtını tek başına ortadan kaldırmaz.

**V1 dışında:** Mobil uygulama, ortak bulut belge arşivi, otomatik hukukî tavsiye/karar, doğrulanmamış son gün hesabı, büyük ofis yetkilendirme, açık beta satış, otomatik model güncelleme.

**İsimlendirme notu:** Bağımsız ürünün kamu kurumu tarafından geliştirilmiş/onaylanmış olduğu izlenimini doğurmayacak ürün adı, açıklama ve Native Messaging kimliği geçiş planı gerekir. Mevcut teknik ad `tr.gov.uyap.archive_agent` olduğu için kırmadan yeniden adlandırma ayrıca incelenmelidir.

## 2. Kaynak tabanı (14 Ekim 2026)

- Geliştirme: `feature/ai-six-workflows`, son gözlenen commit `041dd9a`; draft PR #10, **henüz develop'a birleşmedi**.
- Aktif entegrasyon dalı: `develop`, PR #1 Product Foundation taslak.
- Mevcut sürüm sözleşmesi: Chrome uzantısı `2.9.0 RC15.9.38`, Native Agent `2.7.68`.
- Son commit için offline CI ve Windows installer smoke yeşil; bunlar gerçek saha kabulünün yerine geçmez.
- `docs/WINDOWS_INSTALLER_ACCEPTANCE.md`: gerçek Win10/11 kurulum, upgrade/rollback ve arşiv korunumu bekliyor.
- `docs/RELEASE_CHANNELS.md`: beta/stable imzalı paket ister; code signing, auto-updater, rollout backend henüz tamamlanmamış.
- `docs/AI_SIX_WORKFLOWS.md`: bazı tarih/tebligat/rol akışları yerel kurallara taşınmış, OCR açık eksik ve canlı uzman kabulü bekliyor.
- Daha eski `README.md` sürüm bilgisi 2.7.64; ürünleştirmede tek sürüm doğruluğu sağlanmalıdır.

## 2A. Gerçekleşen kararlar ve doküman durumu — 15 Ekim 2026

| Teslim | Durum | Kaynak |
| --- | --- | --- |
| **DEC-S1-001** Yerel arşiv + opsiyonel AI ürün kapsamı | **ONAYLANDI** (ürün sahibi kararı) | [S1-01](S1_PRODUCT_BRIEF.md), [S1-02](S1_MVP_SCOPE.md) |
| **S1-01** Ürün tanımı | Kapsam ONAYLI; pazarlama metni/teknik ve hukukî kapanış açık | [S1_PRODUCT_BRIEF.md](S1_PRODUCT_BRIEF.md) |
| **S1-02** MVP kapsamı | Ürün kapsamı ONAYLI; U1-U4 E2E doğrulama açık | [S1_MVP_SCOPE.md](S1_MVP_SCOPE.md) |
| **S1-03** UYAP kullanım uygunluğu | İnceleme taslağı hazır; **portal otomasyonu ticari betada BLOCKED** | [S1_PORTAL_COMPLIANCE.md](S1_PORTAL_COMPLIANCE.md) |
| **S1-04** KVKK ve veri güvenliği haritası | İlk kod temelli envanter hazır; hukukçu ve teknik güvenlik kabulü AÇIK | [S1_DATA_PRIVACY_MAP.md](S1_DATA_PRIVACY_MAP.md) |
| S1-05–S1-08 | PLANLANDI | Henüz üretilmedi |

**İlk P0 bulgular:** AI tanılama JSON'unda model yanıtından 1.200 karaktere kadar önizleme ve kaynak başlıkları bulunabilmesi; ticari yapılandırmada portal otomasyonunu kapatmanın henüz kanıtlanmamış olması; Windows/Chrome veri erişimi ve bulut AI aktarımına dair hukukî/teknik kapılar. Bunlar giderilmeden dış kullanıcı betası onaylanamaz.

## 3. Sprint teslimleri ve kabul ölçütleri

| Kod | Teslim | Dosya / kanıt | Kabul ölçütü | Öncelik |
| --- | --- | --- | --- | --- |
| S1-01 | Ürün tanımı ve kullanıcı profili | `S1_PRODUCT_BRIEF.md` | Hedef kişi, 3 temel kullanım işi, tek cümle değer vaadi, sınırlar ve ölçülebilir sonuçlar yazılı | P0 |
| S1-02 | MVP sınırları ve kullanıcı akışları | `S1_MVP_SCOPE.md` | İçeri / dışarı / onay şartlı tablo, 4 uçtan uca senaryo, alternatif içe aktarma akışı | P0 |
| S1-03 | Portal kullanım uygunluğu karar kaydı | `S1_PORTAL_COMPLIANCE.md` | UYAP Avukat+Kurum sözleşmesi / resmî görüş gereksinimleri, hukuk danışmanı inceleme sonucu, özellik bazlı GO / BLOCKED | P0 |
| S1-04 | KVKK/gizlilik & veri akış haritası | `S1_DATA_PRIVACY_MAP.md` | Evrak, kullanıcı kimliği, indeks, log, AI snippet, lisans ve telemetri ayrı satırlarda; amaç/konum/erişim/saklama/silme ve veri rolü | P0 |
| S1-05 | Mimari / sürüm / marka kararları | `S1_ARCHITECTURE_ADR.md` | Archive Engine bağımsızlığı, beta imza, geri alma, fail-closed, sürüm matrisi ve kimlik geçiş kararı | P1 |
| S1-06 | Beta kabul matrisi | `S1_BETA_ACCEPTANCE.md` | Win10+11 temiz kurulum, Chrome+Edge, repair, upgrade, rollback, uninstall, hash, hata senaryoları için PASS/FAIL kanıt alanları | P0 |
| S1-07 | Pilot müşteri araştırması | `S1_PILOT_PLAN.md` | 3–5 gönüllü avukat için davet/izin, 30dk görüşme şablonu, 5 soruluk ihtiyaç testi ve anonim skor kartı | P1 |
| S1-08 | Sprint 2 icra backlog'u ve karar günlüğü | `S1_BACKLOG_AND_DECISIONS.md` | P0/P1/P2, bağımlılık, tahmin, sorumlu rol, açık karar, kabul tanımı; izin dışı özellikler BLOCKED | P0 |

**Not:** Bu sekiz dosya sprintin **planlanan somut çıktılarıdır**; oluşturulup gözden geçirilmeden “tamamlandı” işaretlenmez. Sprint plan dokümanı, onların yerine geçmez.

## 4. Günlük iş planı

| Gün | Ana çalışma | Gün sonu kanıt |
| --- | --- | --- |
| **14 Ekim Çarşamba — Gün 1** | Ürün vaadi, 1 ana persona + ikincil büro profili, MVP v1 sınırları ve mevcut GitHub farklarının dökümü | S1-01 ilk taslak; S1-02 kapsam tablosu |
| **15 Ekim Perşembe — Gün 2** | Dört kullanıcı akışı: ilk kurulum; yerel arşivi içe aktarma; sorun/eksik onarımı; kaynaklı analiz. Portal kullanım matrisi | S1-02 kullanıcı akışları; S1-03 resmî inceleme soruları |
| **16 Ekim Cuma — Gün 3** | Veri akışı ve tehdit modeli; uygulama/Native Agent/log/AI/opsiyonel bulut sınırı; KVKK ve mağaza izinleri | S1-04 taslağı; P0 risk listesi |
| **19 Ekim Pazartesi — Gün 4** | Sürüm ve dağıtım mimarisi; gerçek Windows kabul matrisi; verinin yükleme/geri alma/kurulumda korunumu | S1-05, S1-06 taslağı; #4/#6 bağlantıları |
| **20 Ekim Salı — Gün 5** | Pilot görüşme tasarımı, Sprint 2 öncelikleri, gerekli roller/bütçe/takvim ve bağımlılıkları | S1-07, S1-08 taslakları; demo senaryosu |
| **21 Ekim Çarşamba — Karar** | Ürün sorumlusu ile kapsam onayı; hukukî/teknik blokajlar ayrıştırılır, dokümanlar revize edilir | Sprint Review; yazılı GO / CONDITIONAL GO / NO-GO |

## 5. Ölçütler — Sprint ile üretim kabulünü karıştırma

**Sprint 1 kapanışı:** Sekiz teslim dosyası incelenmiş; her P0 riskin sahibi, sonraki eylemi ve karar durumu belli; MVP kapsamı karara bağlanmış; Sprint 2'de yapılacak ilk 5 iş net.

**Yalnız geliştirmeye koşullu GO:** Portal otomasyonu BLOCKED kalabilir; yerel içe aktarma ürünü üzerindeki çalışma sürdürülebilir.

**Beta dış kullanıcıya dağıtım için ayrıca şart:** Portal otomasyonu kapsamındaki kullanımlar için yazılı hukukî/kurumsal uygunluk; imzalı paket; Win10/11 ve Chrome/Edge gerçek testleri; güvenli upgrade/rollback/uninstall; arşiv veri kaybı yok; P0/P1 sıfır; gizlilik ve izin bildirimi; canlı kullanımın ayrı açık rızası/uygun hukukî dayanağı; hukuki tarih/tebligat sonuçları için uzman kabulü.

**Başarı göstergeleri (pilot hedefi, gerçekleşmiş değil):** 3–5 pilot katılımcı; ≥%80 pilot görevi yardım almadan tamamlama (küçük örneklemde yön gösterici); 0 yanlış dosya eşleştirme; 0 sessiz veri kaybı; arşiv bütünlüğü testlerinin tamamı PASS. AI doğruluk oranları test seti oluşturulmadan yüzdesel vaat edilmez.

## 6. Risk kaydı ve blokajlar

| Risk | Önlem / karar | Sprint 1 durumu |
| --- | --- | --- |
| UYAP robot yazılım / portal kullanım sınırı | Resmî sözleşme ve uzman hukukî inceleme; ürünün yerel içe aktarım varyantı; otomasyon ticari yayında kapalı | BLOCKED |
| Kamuya ait ürün izlenimi / marka | Ticari marka, ürün açıklaması, kurumsal bağımsızlık notu ve Native host adını bozmadan geçiş kararı | AÇIK |
| Yanlış hukuki tarih/tebligat önerisi | Kaynak kanıtı, belirsiz tarihlerde kesin sonuç üretmeme, uzman kabulü, uyarı dili | AÇIK |
| Kişisel veri sızıntısı | Yerel-öncelikli; hassas log maskesi; cloud sağlayıcı için açık opt-in ve hukukî veri aktarımı incelemesi | AÇIK |
| İmzasız kurulum / antivirüs uyarıları | İmza çözümü, paketin bütünlük kanıtı, sıfır veri kaybıyla roll-back | AÇIK |
| 520 fiziksel / 192 metin indeksli gibi kapsam farkı | Evrak bazlı kapsama durumu; PDF/OCR planı; "tüm evrakları okudum" ifadesini engelleme | AÇIK |
| Branch/doküman sürüm kayması | `develop` ← PR #1, PR #10 bağımlılık haritası; tek kaynaktan sürüm çıkarımı | AÇIK |

## 7. GitHub çalışma kuralları

- Ürün çekirdeğine Sprint 1'de doğrudan commit yapılmaz.
- Sprint belgeleri docs-only dalında taslak PR ile tartışılır; teknik entegrasyon sonrası hazırlanır.
- Aynı işleri ikinci kez issue olarak açma: [#2](../../issues/2) Core Certification, [#3](../../issues/3) Product Foundation, [#4](../../issues/4) Windows installer, [#6](../../issues/6) redaction, [#9](../../issues/9) PDF kalite.
- AI akışları için [taslak PR #10](../../pull/10) ayrı kalır; canlı kabul olmadan release'a eklenmez.
- Kararlar geçmiş zaman veya üretim sertifikası olarak yazılmaz. Her maddede `PLAN / TESTED / LEGAL-REVIEW / BLOCKED / APPROVED` statüsü tutulur.

## 8. Sprint 2 için aday ilk beş geliştirme (onaydan sonra)

1. **P0:** Bağımsız yerel dosya içe aktarma/onaylı erişim akışı; portal robot otomasyonundan ayrıştırma.
2. **P0:** Installer yaşam döngüsü gerçek Win10/11 sahada kabul ve veri korunumu.
3. **P0:** Log/destek tanı verisi maskesi ve kullanıcı kontrollü paylaşım.
4. **P1:** Beta kod imzalama ve güvenilir güncelleme/geri alma planı.
5. **P1:** AI indeks kapsam raporu ve sadece doğrulanmış kaynaklı analiz kabul paketi.

## 9. Resmî kaynaklar / inceleme başlangıç noktaları

- UYAP Avukat Portal Kullanım Sözleşmesi ve portal yardım açıklaması:
  https://avukat.uyap.gov.tr/ ve https://portalyonetim.uyap.gov.tr/login.uyap
- KVKK Veri Güvenliğine İlişkin Yükümlülükler:
  https://www.kvkk.gov.tr/Icerik/2040/Veri-Guvenligine-Iliskin-Yukumlulukler
- Chrome Web Store User Data Policy:
  https://developer.chrome.com/docs/webstore/user_data

Bu kaynaklar hukukî veya teknik uygunluk **onayı** değildir. Güncel sözleşmeler ve gerçek dağıtım modeli yetkili uzmanlar tarafından doğrulanmalıdır.
