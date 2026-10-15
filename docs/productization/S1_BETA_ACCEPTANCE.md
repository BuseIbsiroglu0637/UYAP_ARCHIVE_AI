# S1-06 | Beta Kabul Testleri ve Gerçek Windows Saha Matrisi

**Tarih:** 15.10.2026  
**Durum:** TEST TASARIMI HAZIR / GERÇEK SAHA KOŞUMLARI BEKLİYOR.  
**Hedef:** İlk ticari beta için Windows 11 merkezli, yerel arşiv + opsiyonel AI.  
**Kapsam kararı:** DEC-S1-001. Teknik sınırlar: [S1-05](S1_ARCHITECTURE_ADR.md); portal: [S1-03](S1_PORTAL_COMPLIANCE.md); KVKK: [S1-04](S1_DATA_PRIVACY_MAP.md).

> Hiçbir matris satırı doküman hazırlanarak PASS olmamıştır. Var olan GitHub `Offline source and regression smoke` ve `Windows installer lifecycle smoke` sonuçları yalnız **CI-SMOKE**, gerçek müşteri makinesi üstünde test yerine geçmez. İmzalı paket, üretim policy ve lisans uygulaması hazır değilken dış pilot yayımlanmaz.

## 1. Test nesnesi ve sürüm tespiti

- Ana kod referansı: `feature/ai-six-workflows` gözlenen `041dd9a`, ana entegrasyon `develop`, docs-only PR #16.
- Mevcut release contract: Extension `2.9.0 RC15.9.38`; Native Agent `2.7.68`; protocol `2`; IndexedDB `v6`. Bunlar beta final numarası **değil**.
- Ticari beta için **ayrı imzalı beta release kimliği ve digest** üretilecek. Sürüm artefaktları ile test kaydı aynı SHA-256 ve aynı artefakt ID'yi taşımalı.
- Windows 11: önerilen birincil dış kullanıcı platformu (desteklenen, güncel yama). Windows 10: yalnız **ESU ve işletim sistemi güvenlik desteği doğrulanmış koşullu test**; Windows 10 standart desteği 14.10.2025'te sona erdi.
- Chrome + Edge sürümleri ve Windows build numarası her testte kaydedilir.
- Gerçek hukuk dosyası testlerde ancak yetkili, izinli ve kimliği maskelenmiş kapalı senaryoda. CI ve GitHub raporlarında yalnız sentetik veya hassas verisiz kanıt.
- Test öncesi arşiv/indeks/özet verilerinin yedeği ve SHA-256 taban çizgisi alınır; test ortamındaki yedek açık biçimde saklanmaz.

## 2. Test ortamı matrisi

| Ortam ID | İşletim sistemi | Tarayıcı | Kurulum koşulu | Gerçek durum |
| --- | --- | --- | --- | --- |
| ENV-01 | Desteklenen Windows 11 x64 (güncel) | Chrome stable | Temiz kullanıcı profili / ilk kurulum | NOT RUN |
| ENV-02 | Desteklenen Windows 11 x64 (güncel) | Edge stable | Temiz profil / ilk kurulum | NOT RUN |
| ENV-03 | Windows 11 x64 | Chrome+Edge | RC mevcut → ayrı imzalı beta N yükseltme | NOT RUN |
| ENV-04 | Windows 11 x64 | Chrome+Edge | Aynı sürüm onarım, DLL kilidi, yeniden başlatma | NOT RUN |
| ENV-05 | Windows 11 x64 | Chrome+Edge | Başarısız güncelleme, rollback, offline | NOT RUN |
| ENV-06 | Windows 11 x64 | Chrome veya Edge | Kısıtlı standart kullanıcı / ikinci Windows hesabı | NOT RUN |
| ENV-07 | **Windows 10 x64 + etkin ESU + doğrulanmış yamalar** | Chrome+Edge desteklenen sürüm | Yalnız conditional compatibility | NOT RUN |
| ENV-08 | Windows 11 x64 | Tarayıcı yok veya Agent çalışmıyor | Eksik bileşende kurtarma / güvenli hata | NOT RUN |

**Karar:** Standart destek bitmiş Windows 10, yeşil test alsa bile koşulsuz satış destek listesine eklenmez. Aynı zamanda Edge veya Chrome mağaza sürümü/uzantı kimliği Native Messaging `allowed_origins` listesine tam eşleşmelidir.

## 3. Test ön şartları (Definition of Ready)

- [ ] Test eden kişi, ortam sorumlusu, sürüm kimliği, SHA-256 hash, imza ve paket oluşturma kanıtı atandı.
- [ ] **İmzalı** beta EXE, Native Agent ve gerekli paket öğeleri doğrulandı; imzasız `--allow-unsigned-dev` müşteri testinde kullanılamaz.
- [ ] Paket dosya listesi izin verilen build allowlist'iyle üretildi; `.git`, `.env`, gizli anahtar, test fixture, kullanıcı klasörü, kişisel belge içermediği otomatik kontrolden geçti.
- [ ] Ticari beta profili portal otomasyonu UI/background/native seviyelerinde engelliyor; negatif testle kanıtlandı.
- [ ] Yerel içe aktarma güvenli, yanlış dava eşleştirme ve gizli veri log sınırlarını karşılıyor.
- [ ] Lisans sistemi yoksa ücretli dış beta gibi sunulmuyor; devre dışı hesap konfigürasyonu açıkça anlatılıyor.
- [ ] Gerçek dosya kullanan her test için erişim yetkisi, saklama ve imha protokolü belirlendi.
- [ ] Test verisi, kayıt izinleri ve ekran görüntüleri PII redaction kontrolünden geçti.
- [ ] S1-03 portal sözleşme / S1-04 KVKK dış pilot blokajlarına uygunluk teyidi alındı.

Bu koşullar eksikse yalnız izole iç geliştirme testleri yapılır.

## 4. P0 Çekirdek Arşiv / Veri Bütünlüğü Testleri

| ID | Senaryo | Kanıtlanacak beklenen sonuç | Öncelik | Durum |
| --- | --- | --- | --- | --- |
| BETA-CORE-01 | Kullanıcının yetkili yerel dosyaları veya taşınabilir arşivi içe alınır | Orijinal kaynak korunur; doğru dava/evrak eşleştirilir; unsupported format görünür | P0 | NOT RUN |
| BETA-CORE-02 | Aynı evrak tekrar içe alınır | Hash aynıysa sessiz yeniden yazma yok; fiziksel kanıtla mükerrer/unchanged sınıfı | P0 | NOT RUN |
| BETA-CORE-03 | Dosya yolu kayıtlı ama gerçek dosya silinmiş | VERIFIED verilmez; açık missing / onarım durumu | P0 | NOT RUN |
| BETA-CORE-04 | Dosya aynı boyutta ancak byte içeriği farklı | SHA-256 uyuşmazlığı algılanır; başka evrağa bağlanmaz, sessiz veri kaybı yok | P0 | NOT RUN |
| BETA-CORE-05 | İki dava benzer isim ve evrak numarasına sahip | Çapraz dava ve klasör eşleştirmesi 0; belirsizlikte insan incelemesi | P0 | NOT RUN |
| BETA-CORE-06 | Wi-Fi kapalı, LM Studio kapalı, lisans bağlantısı yok | Kullanıcı geçmiş yerel arşive erişir, belgeleri açar ve arar; veri kaybı yok | P0 | NOT RUN |
| BETA-CORE-07 | 500+ evrak; kesinti ve yeniden başlatma | Yanlış VERIFIED veya mükerrer yazma yok; kaldığı yerden kurtarma kanıtı | P0 | NOT RUN |
| BETA-CORE-08 | Uzun dosya yolu, özel Türkçe karakter, kilitli dosya, disk dolu | Anlaşılır hata; path traversal yok; kaynak dosya korunur | P0 | NOT RUN |
| BETA-CORE-09 | IndexedDB oturumu kaybolur, arşiv yerelde kalır | Yerel registry/DB üzerinden dosyalar yeniden listelenir; kayıp gibi görünmez | P0 | NOT RUN |
| BETA-CORE-10 | Yerel DB ve fiziksel dosyalar kasıtlı tutarsız | Meta kayıt `VERIFIED` statüsü yerine geçmez; kanıt kanalı açık | P0 | NOT RUN |

**Çekirdek kabul eşiği:** P0 senaryolardan her biri PASS; yanlış dava kaydı = 0, sessiz veri kaybı = 0, hatalı VERIFIED = 0. Büyük örneklem testleri başarısızlık türüne göre kanıtla raporlanır; otomatik CI, bu saha testlerini geçmiş kabul edilmez.

## 5. Windows, Chrome/Edge ve Native Agent Testleri

| ID | Senaryo | PASS ölçütü | Öncelik | Durum |
| --- | --- | --- | --- | --- |
| BETA-INST-01 | Windows 11 temiz kurulum + Chrome | İmzalı artefakt ve Authenticode doğrulama; 2/2 ping; HKCU 32/64 kayıtları doğru | P0 | NOT RUN |
| BETA-INST-02 | Windows 11 temiz kurulum + Edge | İmzalı artefakt, Edge Native Messaging, 2/2 ping, uzantı ID allowlist doğru | P0 | NOT RUN |
| BETA-INST-03 | Mevcut RC N-1 → beta N | Veriler korunur, agent `current` yeni; sağlam önceki ajan `rollback` | P0 | NOT RUN |
| BETA-INST-04 | Beta N → önceki sağlam N-1 rollback | Host, registry ve arşiv okunabilir; DB şema uyumsuzluğunda riskli downgrade reddedilir | P0 | NOT RUN |
| BETA-INST-05 | Hatalı/yarım kurulumda geri dönüş | Eski çalışır Native Agent ve registry atomik geri kurulur; veri değişmez | P0 | NOT RUN |
| BETA-INST-06 | Aynı sürüm onarım | Kurulum ve registry düzelir; belge ve özet klasörlerine dokunulmaz | P0 | NOT RUN |
| BETA-INST-07 | Agent DLL `libcrypto-3.dll` kullanımda (WinError 5) | Güvenli süreç tanısı/uyarı, tekrar deneme; tüm Python işlemlerini öldürme yok | P1 | NOT RUN |
| BETA-INST-08 | Uninstall | Yalnız program ve registry; arşiv/veri silinmez, kullanıcıya açık bilgi | P0 | NOT RUN |
| BETA-INST-09 | Tarayıcı/Agent sürüm uyumsuzluğu | Sessiz işlem yerine açıklayıcı hata ve güvenli durma; yükseltme talimatı | P1 | NOT RUN |
| BETA-INST-10 | Yanlış Native Messaging kökeni/manifest | `allowed_origins` dışındaki ID ve path reddedilir | P0 | NOT RUN |
| BETA-INST-11 | Arşiv içeriği dosya sayısı/SHA öncesi ve sonrası | Örnek değil, kritik veri bütünlük karşılaştırması fark yok | P0 | NOT RUN |
| BETA-INST-12 | Windows 10 + güncel ESU ortamı | Kod uyumluluğu ve kurulum testleri; destek kararı ayrı | P1 / CONDITIONAL | NOT RUN |

**Kayıt:** Her ortamda Windows build, tarayıcı sürümü, gerçek extension ID, Native Agent path/version, manifest, registry 32/64, SHA ve imza özeti, tanı dosyası yalnız redakte edilmiş biçimde tutulur.

## 6. Güvenlik, gizlilik ve paketleme testleri

| ID | Senaryo | PASS ölçütü | Öncelik | Durum |
| --- | --- | --- | --- | --- |
| BETA-SEC-01 | Paket allowlist içerik taraması | Kurulum içine `.git`, `.env`, kullanıcı/hukuk belgesi, secret, test fixture, repo tam kökü girmemiş | P0 | NOT RUN |
| BETA-SEC-02 | Üretim imza doğrulaması | Beklenen yayıncı, geçerli Authenticode, imzalı agent/setup; checksum ve version tutarlı | P0 | NOT RUN |
| BETA-SEC-03 | Tanı JSON, özel kişi/adres/UETS içeren sentetik AI yanıtı | PII/özel veri önizlemede, destek paketinde ve GitHub raporunda bulunmaz | P0 | NOT RUN |
| BETA-SEC-04 | Standart kullanıcı, diğer Windows hesabı ve izin reddi | Başka kullanıcı için dava/indeks/özet/log erişimi engellenir, hata güvenli | P0 | NOT RUN |
| BETA-SEC-05 | A ve B davaları indeks/sorgu | Çapraz kaynak/kimlik/atıf sızıntısı yok | P0 | NOT RUN |
| BETA-SEC-06 | Portal otomasyonu kapalı beta politikası | UI, service worker, Native Agent yollarından toplu sorgu tetiklenmez; fail closed | P0 | NOT RUN |
| BETA-SEC-07 | Bulut AI devre dışı | Kullanıcı izinsiz dış sağlayıcıya belge/snippet/veri çıkışı yok; ağ kaydı kanıt | P0 | NOT RUN |
| BETA-SEC-08 | Lisans doğrulama istekleri ve hata | Dava/kişi/hash/yol sunucuya aktarılmaz; hata temel yerel arşivi kilitlemez | P0 | NOT RUN |
| BETA-SEC-09 | Path traversal/symlink/yetkisiz klasör | Arşiv kökü dışından dosya açılmaz, dışa yazılmaz; sahte dosya imzası kabul edilmez | P0 | NOT RUN |
| BETA-SEC-10 | Yedekleme/silme/geri alma | AI indeksi ve `.trash` kalıntıları politika ile tutarlı; veri silme beyanıyla çelişki yok | P1 | NOT RUN |

## 7. Opsiyonel AI fonksiyon kabulü (satışı koşullu)

| ID | Test | PASS ölçütü | Durum |
| --- | --- | --- | --- |
| BETA-AI-01 | LM Studio kapalı | Arşiv ve arama çalışır, model gerektiren özellik açıkça devre dışı görünür | NOT RUN |
| BETA-AI-02 | `VERIFIED` ve okunabilir belge ile özet | Kapsam sayıları, kaynak kimlikleri ve kaynaksız iddia reddi; kısa ve anlaşılır içerik | NOT RUN |
| BETA-AI-03 | 520 fiziksel / 192 metni indeksli gibi eksik OCR | `tüm dava okundu` iddiası yok; 192/520 gibi gerçek oran açık, OCR eksikliği belirtilir | NOT RUN |
| BETA-AI-04 | Önemli tarihler / ara kararlar | Uydurma kesin son gün yok, belge tarihi ile işlem tarihi ayrılır; hukukçu değerlendirmesi | NOT RUN |
| BETA-AI-05 | Taraflar | Başka davanın tarafı ana davaya karışmaz; rol kaynağı görünür | NOT RUN |
| BETA-AI-06 | Tebligatlar | Mazbata/tebliğ verileri, süre başlangıcı/tatil istisnaları manuel uzman doğrulaması; belirsizde kesin yönlendirme yok | NOT RUN |
| BETA-AI-07 | Model cevabı kaynaksız / yanlış `Dxxxxxx` | Yanıt reddedilir veya açıkça etiketlenmiş deterministik adaylar; modelin uydurduğu iddia geçmez | NOT RUN |
| BETA-AI-08 | Sonuç kaydet, yeniden aç, sil, geri al, PDF | Kullanıcı kontrolü; `_deleted.json`, `.trash` ve PDF kaynak/kimlik hatası yok | NOT RUN |

**AI kararı:** Uzman onaylı sentetik + yetkili maskeleme yapılmış hukuk vaka veri seti olmadan “hukukî olarak doğru” veya “kesin süre” şeklinde ürün iddiası yapılmaz. Her AI alt akışı ayrı **GO / NOT GO** alır; başarısız modül çekirdek beta yayınına engel olmayabilir fakat ticari betada gizli/kapalı tutulur.

## 8. Performans ve kullanıcı deneyimi kabulü

- **BETA-UX-01:** İlk 15 dakika içinde kullanıcı (teknik yardım olmadan) yerel dosyayı içe alma, dava bulma, belge açma ve “eksik dosya” uyarısı görme görevlerini gerçekleştirebilir. 3–5 küçük pilot kullanıcı; yön gösterici hedef ≥%80 görev tamamlama (küçük örneklem yüzdesi istatistiksel genelleme değildir).
- **BETA-UX-02:** 500+ evrakta UI kilitlenmez; progress, durma/yeniden başlama ve sorun merkezi anlaşılır. Hız eşikleri pilot cihazda gerçek ölçüme dayalı belirlenecek, henüz kanıtsız ms/saniye vaat edilmeyecek.
- **BETA-UX-03:** Kurulum hata mesajı ve 2/2 ping anlaşılır; kullanıcının sistem genelinde antivirüs kapatması veya sır paylaşması istenmez.
- **BETA-UX-04:** Destek paketi kullanıcıya ne göndereceğini önceden gösterir; onaylı maskeli çıktı dışına veri aktarılmaz.

## 9. Test kanıt şeması

Her test için aşağıdaki kayıt en az doldurulur:

```yaml
test_id: BETA-INST-01
status: NOT_RUN  # NOT_RUN | PASS | FAIL | BLOCKED
test_date: null
tester_role: null
case_type: SYNTHETIC  # SYNTHETIC | MASKED_AUTHORIZED
environment:
  os_name: null
  os_build: null
  esu_status: null
  browser_name: null
  browser_version: null
  extension_id_masked: null
  extension_version: null
  native_agent_version: null
artifact:
  release_id: null
  build_commit: null
  installer_sha256: null
  signer_subject_masked: null
  authenticode_status: null
evidence:
  sanitized_log: null
  registry_check: null
  ping_result: null
  archive_before_sha256_manifest: null
  archive_after_sha256_manifest: null
  screenshot_masked: null
  reviewer: null
  notes_no_pii: null
finding:
  issue_url: null
  severity: null # P0 | P1 | P2 | P3
  expected: null
  observed_masked: null
  retest_date: null
```

Kayıtlar gerçek ad, dava numarası, UETS, TCKN, API key, tam kişisel belge yolu veya ham evrak içermemelidir. Ekran görüntüsünü GitHub'a aktarmadan önce yerel redaction kontrolünden geçir. Kanıt klasörü ayrı erişim kontrollü alan olarak belirlenir.

## 10. Giriş / çıkış kuralları

**CI-SMOKE:** Kod sözdizimi, offline regresyon ve Windows installer lifecycle smoke başarılı olabilir. Bu **yalnız geliştirici düzeyi** kanıttır.

**INTERNAL ALPHA:** Geliştirme testleri ve laboratuvar içi denemeler; unsigned-dev yalnız izole ve güvenli test cihazında.

**CONTROLLED BETA GO (ilk dış pilot):**
- [ ] UYAP otomasyonu ticari profilde **teknik olarak** kapalı ve testli; portal kullanımı için ayrı hukukî onay yoksa online UYAP işlemi yapılmıyor.
- [ ] Hukukî/KVKK/meslek sırrı/gizlilik kullanımı değerlendirilmiş; gerçek kullanıcı verisi izin süreçleri tamam.
- [ ] İmzalı yayın, paket allowlist denetimi, müşteri dostu onarım ve güvenli rollback gerçek cihazda PASS.
- [ ] Windows 11 Chrome + Edge; clean install, repair, N-1→N, bozuk N→rollback, uninstall testleri PASS.
- [ ] BETA-CORE tüm P0 PASS; BETA-SEC tüm P0 PASS; **açık P0/P1 = 0**; 0 yanlış dava eşleme, 0 sessiz veri kaybı.
- [ ] Tanılama PII redaction ve kullanıcı kontrollü destek paylaşımı PASS.
- [ ] AI özelliklerinden her biri ayrı uzman/kanıtlı test kararına sahip; onaysız olanlar müşteri profilinde kapalı.
- [ ] İlgili PR'lar merge edilmiş, sürüm sözleşmesi/manifest/agent ID ve yayın artefaktı tek sürüme kilitlenmiş.
- [ ] Ürün sahibi, teknik sorumlu ve hukuk/gizlilik gözden geçiren tarihli kararı kaydetmiş.

**NO-GO:** İmzalanmamış paket; hatalı VERIFIED/yanlış dava bağlama; veri silme; paket içine gizli belge/anahtar sızması; açık portal otomasyonu; gizli veri dışarı gönderimi; kontrolsüz hukuki son gün yönlendirmesi; başarısız rollback; P0/P1 açık.

**Sprint 1 kapanışı ≠ beta GO:** Bu matrisi hazırlamak yalnız **test tasarımı** teslimidir; beta işlevleri veya gerçek cihaz sonuçları tamamlanmış değildir.

## 11. İzlenebilirlik ve sonraki işler

- Installer: [#4](../../issues/4), [S1-05](S1_ARCHITECTURE_ADR.md), [mevcut Windows installer acceptance](https://github.com/BuseIbsiroglu0637/UYAP_ARCHIVE_AI/blob/feature/ai-six-workflows/docs/WINDOWS_INSTALLER_ACCEPTANCE.md).
- Veri redaction: [#6](../../issues/6), [S1-04](S1_DATA_PRIVACY_MAP.md).
- 12K fiziksel arşiv sertifikasyonu: [#2](../../issues/2).
- AI OCR/metin kalite: [#9](../../issues/9), [AI değişiklikleri PR #10](../../pull/10).
- UYAP/Kurum kullanım uygunluğu: [S1-03](S1_PORTAL_COMPLIANCE.md).
- Uyum için dış kaynak: [Google Native Messaging](https://developer.chrome.com/docs/extensions/develop/concepts/native-messaging), [Microsoft Windows 10 lifecycle](https://support.microsoft.com/en-us/windows/deployment/updates-lifecycle/windows-10-support-has-ended-on-october-14-2025).
