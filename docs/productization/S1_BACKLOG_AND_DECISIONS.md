# S1-08 | Sprint 2 Geliştirme Backlog'u ve Ürün Karar Kaydı

**Hazırlanma tarihi:** 15.10.2026  
**Durum:** TASLAK / Sprint 1 Review onayı bekleniyor. Kod geliştirmesi henüz başlamadı.  
**Ürün karar kaydı:** DEC-S1-001 — yerel arşiv çekirdeği + opsiyonel, ayrı kabul şartlı AI.  
**Sprint 2 öneri dönemi:** 22 Ekim – 4 Kasım 2026 (10 iş günü; ekip kapasitesi/ön şartlar onaylanırsa).  
**Ana amaç:** Yerel arşiv beta hazırlığında ilk P0 güvenlik/dağıtım blokajlarını azaltmak; ürün kodu ile belge stratejisini birbirine bağlamak. **Sprint 2 bitince otomatik beta yayını yok.**

## 1. Sprint 1 kayıt özeti / gerçekten ne hazır?

| Teslim | Durum 15.10.2026 | Sonraki işlem |
| --- | --- | --- |
| S1-01 Ürün tanımı | Yerel çekirdek + opsiyonel AI ürün kararı ONAYLI; pazarlama/marka onayı açık | Kullanıcı değeri pilot görüşmesinde test |
| S1-02 MVP kapsamı | Ürün kapsamı ONAYLI; U1–U4 E2E kanıtı açık | Ürün çekirdeği ve portal dışı yerel içe alma doğrulaması |
| S1-03 UYAP uygunluğu | Hukukî inceleme taslağı var; otomatik portal işleri müşteri beta kullanımında BLOCKED | Hukukçu/sözleşme kanıtı |
| S1-04 Veri haritası | Koddan ilk harita var; KVKK ve ağ/redaction testleri açık | P0 data minimization |
| S1-05 Mimari ADR | Mimari öneri taslağı var, uygulanmadı | Modül sınırları ve beta enforcement |
| S1-06 Beta kabul matrisi | Test planı/kanıt şablonu hazır; **44 test NOT RUN** | P0'larla saha testleri |
| S1-07 Pilot planı | 3–5 gönüllü keşif görüşmesi taslağı hazır, katılımcı/test yok | Kullanıcı görüşmelerini planla |
| S1-08 Sprint 2 backlog | Bu doküman taslak | Sprint Review / kapasite kararı |

**Gerçek kaynak kanıtı:** GitHub [#2](https://github.com/BuseIbsiroglu0637/UYAP_ARCHIVE_AI/issues/2) klasik UYAP'da 259 evraklık tamamlanmış tekil canary ve bazı recovery davranışları bildiriyor; ancak **12.000+ evrak ve Kurum UYAP bütünlük kabulü hâlâ açık**. [#4](https://github.com/BuseIbsiroglu0637/UYAP_ARCHIVE_AI/issues/4) imzalı Win10/11 clean install, upgrade, rollback, uninstall kanıtlarının açık olduğunu belirtiyor. CI smoke'u saha kabulü olarak sayma.

## 2. Önceliklendirme ilkesi

- **P0 — Güvenlik/veri bütünlüğü/yasal dağıtım blokajı:** çözülmeden beta yayımlanmaz.
- **P1 — Pilot kullanılabilirlik/operasyon:** çok ciddi risk azaltır ama dışa dağıtım koşullarıyla yönetilir.
- **P2 — Sonraki değer:** temel ürünü bloke etmeyen geliştirmeler.
- Ürün kapsamını koru: Archive Engine değişikliği regression + migration + gerçek kanıt gerektirir; AI ayrı PR #10 içinde, uzman kabulü olmadan release ile birleştirilmez.
- Bir bağımlılık/izin kapanmadan onun üzerinde dış pilot gerçek dosya ile çalıştırılmaz.

## 3. Sprint 2 öncelikli uygulama iş listesi

| ID | Öncelik | İş paketi | Sorumlu rol | Tahmini efor* | Bağımlılıklar / mevcut issue |
| --- | --- | --- | --- | --- | --- |
| **S2-01** | P0 | **Ticari beta ürün profilinde portal otomasyonunu teknik olarak kapatma:** UI + service worker + native fail-closed ve regresyon. Yerel arşivleme aynı kalır. | Teknik / güvenlik | 2–4 dev-gün | [S1-03](S1_PORTAL_COMPLIANCE.md); #3; hukukî GO yok |
| **S2-02** | P0 | **Kurulum paketini allowlist ve temiz build köküne çevirme:** `.git`, test verisi, geliştirme logu, secret ve kullanıcı belgesi yayımlanamaz; paket içeriği CI'da taranır. | Teknik / release | 2–3 dev-gün | [S1-05](S1_ARCHITECTURE_ADR.md); [#4](../../issues/4), release contract |
| **S2-03** | P0 | **Paylaşılabilir tanılama / log redaction:** AI yanıtı preview, Windows kullanıcı yolu, dava/kişi bilgisi, token ve eski loglar varsayılan destek paketine çıkmaz; `archiveDataTouched` kanıtı doğru hesaplanır. | Teknik / gizlilik | 3–5 dev-gün | [#6](../../issues/6), [S1-04](S1_DATA_PRIVACY_MAP.md) |
| **S2-04** | P0 | **Portal bağımsız yerel içe alma akışının teknik gap analizi + ilk E2E:** kullanıcı yetkili yerel PDF/UDF veya eski arşivini seçer; dosya–dava eşleşme, hash/format ve kaynak koruması. Genelleştirilmiş dosya importunun eksikleri kayıt edilir. | Teknik / QA | 2–4 dev-gün | #2/#3; [S1-02](S1_MVP_SCOPE.md); migration/test |
| **S2-05** | P0 | **Gerçek Windows 11 iç laboratuvar kurulum/onarım/geri alma testlerini kanıtla:** Chrome + Edge, Windows sürümü, registry/ping ve sha manifest raporu. İmzalı beta ayrı kapıdır. | QA / release | 2–4 dev-gün** | [#4](../../issues/4), S2-02/03; [S1-06](S1_BETA_ACCEPTANCE.md) |

\* Dev-gün tahminleri **ilk kestirimdir**; keşif/uygulama esnasında revize edilmeli. Tek geliştiricinin 10 günde tüm P0'ları kapatacağı vaadi değildir.  
\** Test sahası ve gerçekten imzalı farklı iki ajan sürümü erişilemezse bu işin upgrade/rollback kısmı **BLOCKED** kalır.

### Kapasite planı (varsayım: 1 geliştirici + kısıtlı QA)

- 10 iş gününde gerçekçi kullanılabilir kapasite **yaklaşık 7–8 odak gün**; toplantı ve hata payı dahil.
- Sprint 2 için **taahhüt önerisi:** S2-01 + S2-02 ve S2-03'ün gizlilik sızıntısını önleyen ilk parçası. Bitiş ve ölçüm planı onaya bağlıdır.
- **Stretch / takip sprintine taşınabilir:** S2-03 kalan kapsam, S2-04, S2-05. Ön koşullar yoksa bunları zorla tamamlandı sayma.
- Öncelikler çakışırsa **veri güvenliği ve portal fail-closed**, performans veya yeni AI özelliği önünde gelir.
- Teknik sorumlu ve test cihazı gerçekten hazır olduğunda gün/sorumlu ataması Sprint Review'da netleştirilecek.

## 4. İş başına teknik kabul ölçütleri (Definition of Done)

### S2-01 — Portal beta kapısı

- [ ] Beta profilinde portal otomasyonunu başlatan UI aksiyonları gizli/devre dışı; eski istemci mesajlarıyla aşma mümkün değil.
- [ ] Service worker ve native düzeyde bilinmeyen/yetkisiz portal otomasyon aksiyonları fail-closed.
- [ ] Erişim engeli / güvenlik kontrolü bypass yolu geliştirilmez.
- [ ] Sadece yerel arşiv seçimi, arama ve doğrulama çevrimdışı çalışır.
- [ ] Negatif test: Chrome UI, runtime message, Native action veya eski kurulumdan otomasyon başlatılamaz.
- [ ] Gerçek portal sisteminde yetkisiz otomatik test yapılmaz; sentetik/izole test.

### S2-02 — İmzaya uygun temiz paket

- [ ] Build temiz workspace'te allowlist ile yapılır; kaynak kökü topluca kopyalanmaz.
- [ ] Yalnız çalıştırma için gereken extension, agent, vendor ve belirtilen runtime bileşenleri pakete girer.
- [ ] Test fixture, `.git`, `.env`, ham JSON/log, yerel kullanıcı dosyası, token/secret ve `private` test belgeleri sıfır.
- [ ] Artefakt manifest: dosya adı, byte, SHA-256, sürüm ve build commit'i.
- [ ] Yapay örnek gizli dosya eklenirse CI **FAIL**; temiz paket açıldığında host/uzantı temel işlevleri PASS.
- [ ] Beta imzalama pipeline'ı bitmedi ise artefakt **dev-only** olarak işaretlenir.

### S2-03 — Güvenli tanılama

- [ ] Native installer logu ve AI tanılama `providerOutputPreview` / `uncitedClaims` / kaynak başlıkları için PII tehdidi giderilir.
- [ ] Destek dışa aktarımı yalnız kullanıcı başlatınca gerçekleşir; göndermeden önce içerik önizlemesi ve maskeleme.
- [ ] Windows kullanıcı adı ve mutlak yollar, TC kimlik/UETS, token, case ID, belge metni şablonları sentetik fixture'da sızmaz.
- [ ] `archiveDataTouched` sahada ölçülen kanıttır, sadece sabit `false` yazılarak üretilmez.
- [ ] Kümülatif log eski kayıtları otomatik toplamaz; per-run veya bounded rotation.
- [ ] `native` ve portable smoke testleri, veri onarım regressionları PASS.

### S2-04 — Yerel import

- [ ] Portal açmadan, kullanıcının açık seçimiyle sentetik 20–50 belge klasörü içe alınır.
- [ ] Yanlış dava numarası/evrak kimliği emin olunamıyorsa REVIEW_REQUIRED.
- [ ] Kaynak verisi korunur, sessiz overwite yok, hash+boyut kanıtı.
- [ ] Varsa UDF/PDF kalite sınırı, metni çıkarılamayan evrakın AI kapsamından hariç tutulması.
- [ ] Mevcut taşınabilir arşiv ve rastgele klasör importunun farkı belgelenir; ürün vaadi kodun gerçek yeteneğiyle eşleşir.

### S2-05 — Windows laboratuvar kabulü

- [ ] Desteklenen Windows 11 güncel build + Chrome stable ve Edge stable üzerinde gerçek browser-level 2/2 ping.
- [ ] Kişisel arşiv gerçek veri yerine sentetik / izinli ve maskeli tutulur.
- [ ] Farklı N-1 ve N ajan sürümleri ile imzalı upgrade/rollback veya BLOCKED + ihtiyaç kaydı.
- [ ] Kurulum/onarım/uninstall'da arşiv hash manifesti değişmez; `archiveDataTouched` kanıtı.
- [ ] Her sonuç [S1-06 şablonuna](S1_BETA_TEST_RUN_TEMPLATE.yaml) kaydedilir; NOT RUN→PASS/FAIL ancak kanıtla değişir.

## 5. Ayrı onay ve sonraki sprint adayları (şimdilik taahhüt değil)

| ID | Öncelik | Konu | Ön koşul / karar |
| --- | --- | --- | --- |
| S2-06 | P1 | Kod imzalama sertifikası / güvenli anahtar saklama ve publisher kimliği | Hukukî marka; release onayı; S2-02 |
| S2-07 | P1 | Lisans metadata-only PoC, yerel arşivi kilitlemeyen offline entitlement | [S1-05](S1_ARCHITECTURE_ADR.md), S1-04 hukukî rol analizi |
| S2-08 | P1 | AI indeks kapsam raporu, OCR açığı / bozuk PDF kaynak-kalite kapısı | [#7](../../issues/7), [#9](../../issues/9), taslak PR #10 |
| S2-09 | P1 | Workspace `Yerele indirildi` → `Doğrulanmış` UI semantiği | [#5](../../issues/5), regresyon |
| S2-10 | P1 | Native Messaging `tr.gov.uyap.archive_agent` ticari host adı/kimlik ve migration planı | S1-03 hukukî marka / imza; Chrome/Edge extension ID testleri |
| S2-11 | P1 | Core Certification 12K uzun çalışma / Kurum UYAP gerçek kabulü | [#2](../../issues/2); uygunluk koşullarına bağlı güvenli saha |
| S2-12 | P2 | Güncelleme kanalı updater uygulaması / staged rollout | Önce S2-02/S2-05/S2-06, imza ve rollout |
| S2-13 | P2 | Çok kullanıcılı büro senaryoları ve bulut senkronizasyon keşfi | Pilot ihtiyaç araştırması; KVKK/mimari |
| S2-14 | P2 | AI hukukî uzman benchmarkı, kurallı tebligat/tarih kabulü | S1-07, PR #10, hukuk uzmanı veri seti |

**Kullanıcıdan veri alınması veya otomatik UYAP taraması**, mevcut ürün kapsam kararının dışına çıkamaz. Yerel içe alma ve sentetik testlerin dışında portal işlemine geçiş için S1-03'te ayrıca karar gerekir.

## 6. Branch / release entegrasyon planı

1. Sprint 1 docs-only dalı `docs/sprint-01-productization` → taslak [PR #16](../../pull/16) → teknik/ürün/hukuk notları incelendikten sonra `develop` hedefli merge kararı.
2. AI değişiklikleri `feature/ai-six-workflows` üzerinde taslak [PR #10](../../pull/10), taban `feature/archive-case-ai`. **Kritik:** PR #10'u doğrudan docs PR'ına veya stable sürüme birleştirme; önce entegrasyon zinciri ve gerçek test kontrolü.
3. Sprint 2 işleri için `develop` üzerinden küçük özellik dalı ve ayrı PR aç; doğrudan `main` veya `release/rc15.9.38` içine geliştirme commit'i yok.
4. Mevcut müşterinin `UYAP_Arsiv`, `_archive.db`, uzantı IndexedDB ve AI summaries verisini bozabilecek her değişiklik için regression + migration/rollback + test yedeği.
5. Her release'de tek kanonik sürüm matrisi, imzalı artefakt ve test setinin aynı commit'e ait olması gerekli. Sürüm numarasını CI yeşil diye `stable` yapma.

## 7. Sprint 2 faaliyetleri ve bağımlılık sırası

```text
DEC-S1-001 (kapsam onaylı)
    |--> S1-03 portal hukukî karar --> S2-01 beta fail-closed gate
    |--> S1-04 veri haritası -------> S2-03 güvenli log/destek
    |--> S1-05 release ADR --------> S2-02 allowlist paket
    |                                  |
    |                                  +--> S2-06 imzalama
    |                                           |
    |                                           +--> S2-05 Windows saha kabulü
    |
    +--> S1-02 MVP akış ------------> S2-04 yerel import / E2E
                                         |
                                         +--> S1-07 sentetik görev pilotu
```

**Çakışan önceliklerde güvenlik kapıları önce çözülür.** S2-01 portalı devre dışı bırakırken resmî entegrasyon olduğu izlenimi veren UI/manifest/host değişiklikleri bağımsız test planına bağlıdır.

## 8. Sprint Review (21 Ekim 2026) karar tutanağı şablonu

| Karar | Seçenek | Şu an |
| --- | --- | --- |
| Sprint 1 belgeleri incelendi mi? | APPROVED / CHANGES REQUESTED | **PENDING** |
| Yerel arşiv + opsiyonel AI devam? | APPROVED / REVISION | **APPROVED (DEC-S1-001)** |
| S2-01/02/03 teknik sorumlu ve kapasite? | OWNER / START DATE | **PENDING** |
| S2-04 mevcut import açığı E2E yolu? | CONFIRMED / SPIKE | **PENDING** |
| Beta imza ve test cihazları mevcut mu? | READY / BLOCKED | **PENDING** |
| UYAP portal otomasyonunun dış betada bloke olduğu doğrulandı mı? | TESTED / NOT TESTED | **NOT TESTED** |
| KVKK / Avukat Portal / Kurum Portal hukukî inceleme sonucu? | GO / CONDITIONAL / BLOCKED | **BLOCKED / PENDING** |
| Üç ila beş sentetik veri görüşmesi planı onaylandı mı? | YES / NO | **PENDING** |
| Sprint 2 başlatma kararı | INTERNAL DEVELOPMENT GO / HOLD | **PENDING** |
| İlk dış pilot yayın kararı | GO / NO-GO | **NO-GO (ön koşullar henüz tamamlanmadı)** |

**Sprint 1 kapanış kriteri:** Sekiz teslim belgesinin nihai gözden geçirilmesi; her açık P0'ya sorumlu ve takip planı atanması; önceki kabul edilebilir local-first kapsam kararının güncel onayı; Sprint 2 taahhüdünün gerçek kapasiteyle uyumu. Plan taslağı hazırlanması Sprint Review onayı yerine geçmez.

## 9. Kaynak iş kayıtları

- [#2 Core Certification](../../issues/2): 259 evrak canary ve recovery olumlu, 12K + Kurum UYAP + kapanış açık.
- [#3 Product Foundation](../../issues/3): sürüm/kanal/diagnostic/code boundary ilerledi; canlı installer ve certification açık.
- [#4 Windows Installer](../../issues/4): gerçek signed Win11/Win10, rollback ve archiveDataTouched kabulü açık.
- [#5 Workspace metric](../../issues/5): doğrulanmış verinin “Yerele indirildi” etiketine ilişkin açık UI konusu.
- [#6 Secure diagnostics](../../issues/6): redaction, log rotation, support bundle, kanıt şeması açık.
- [#7 AI Verified RAG](../../issues/7): AI foundation, koşullu model ve indeks.
- [#9 PDF quality](../../issues/9): mojibake regresyon ilerledi; gateway sınırının devam eden incelemesi.
- [PR #10 AI workflow](../../pull/10) ayrı taslak, ticari ürün kabulü değildir.

## 10. Ürün başarısı ölçütleri (plan, henüz sonuç değil)

- **S2 P0 güvenlik:** Paket içerik allowlist testi PASS; ticari beta profilde portal eylemleri tüm katmanlarda fail closed; destek tanısında sentetik PII/token yok.
- **Arşiv doğruluğu:** Yanlış dava bağlama = 0, sessiz veri kaybı = 0, sahte VERIFIED = 0 (testlerde).
- **Uygulama sürekliliği:** AI yokken arşiv liste/arama/açma devam eder.
- **Saha:** Win11 + Chrome/Edge fiziksel install/recovery kanıtları; imza yoksa yalnız internal alpha.
- **Pilot:** 3–5 görüşme **hedef**, gerçekleşince n/N ve gözlem kanıtı kaydedilecek.

**Plan bir üretim veya teknik sertifikasyon beyanı değildir. S2-01–S2-05 işlerinin hiçbiri henüz başlatılmadı ya da DONE değildir.**
