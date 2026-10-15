# S1-04 | KVKK, Gizlilik ve Veri Güvenliği — Ön Veri Akış Haritası

**Tarih:** 15.10.2026 · **Sürüm:** İnceleme taslağı v0.1  
**Durum:** **PRIVACY/SECURITY REVIEW REQUIRED; DIŞ PİLOT ONAYI DEĞİLDİR.**  
**Kapsam kararı:** DEC-S1-001 — Yerel arşiv çekirdeği, ayrı onaylı opsiyonel AI.  
**İlgili belgeler:** [S1-01](S1_PRODUCT_BRIEF.md), [S1-02](S1_MVP_SCOPE.md), [S1-03](S1_PORTAL_COMPLIANCE.md)

> Teknik tespitler 15.10.2026'da incelenen `feature/ai-six-workflows` geliştirme dalına dayalıdır (gözlenen commit `041dd9a`). Bu dal, mevcut `develop` üzerine birleştirilmemiş olabilir. Tespit **tasarım / kaynak kodu gözlemidir**, kurulu bir müşterinin çalışma ortamına ilişkin veri sızıntısı testi değildir.

## 1. İlke ve hukukî çerçeve

1. **Kişisel verileri cihazda saklamak, KVKK'dan muafiyet sağlamaz.** KVKK m.12, yetkisiz işleme/erişimi önleme ve koruma için uygun teknik/idari tedbirleri gerektirir. [KV1]
2. Dava belgelerinde sağlık, ceza mahkûmiyeti vb. özel nitelikli kişisel veri bulunabilir; 6698 sayılı Kanunun ilgili işleme şartları somut veri ve faaliyet bazında değerlendirilir. [KV2]
3. Veri sorumlusu / veri işleyen sıfatı tek bir sabit etiket değildir: amaç, yöntem, lisans, destek, bulut AI ve veri paylaşımı bakımından ayrı ayrı incelenmelidir. [KV3]
4. Harici AI sağlayıcısına **belge yerine kısa alıntı** göndermek de kişisel veri işleme/aktarım faaliyeti olabilir. Yurt dışına aktarım söz konusuysa 6698 sayılı Kanun m.9 güncel kuralları (yeterlilik, uygun güvenceler, sınırlı istisnalar) ayrı değerlendirilir. Sadece opt-in tıklaması bütün aktarımı kendiliğinden hukuka uygun yapmaz. [KV4]
5. Chrome Web Store, erişilen kullanıcı verisinin açık ve doğru bildirimini, sınırlı kullanımını ve en dar izinlerin istenmesini öngörür. [C1]

## 2. Mevcut / planlanan veri hareketi

```text
Kullanıcının yetkili yerel belgesi (PDF/UDF/...) [mevcut kaynak / taşınabilir arşiv]
    |
    v
Windows yerel Archive Engine / Native Agent
    |--> Yerel belge dosyaları + _archive.db (bütünlük ve dava eşleştirmesi)
    |--> Chrome/Edge çalışma durumu, IndexedDB / uzantı storage
    |--> Yerel AI indeksi: UYAP_Arsiv/_ai/case-<hash>-v1.sqlite (okunabilir metin parçaları)
    |--> Yerel AI çıktı: %LOCALAPPDATA%/UYAPArsiv/AI_Summaries ve izinli arşiv aynası
    |--> Yerel tanılama: %LOCALAPPDATA%/UYAPArsiv/ai-diagnostic-latest.json
    |
    +--> [Opsiyonel] localhost LM Studio: yalnız açıkça seçilmiş kaynak parçaları
    |
    +--> [HARİCİ AI: beta BLOKE, ayrı inceleme] OpenAI API: kısa alıntılar da
    |      kişisel veri / dava sırrı içerebilir; sözleşme+KVKK aktarım şartı gerekir
    |
    +--> [GELECEK / etkin değil] Supabase üyelik/lisans servisi
           hesap/canlı işlev doğrulanmadan aktarıldı kabul edilmez

Hiçbir çevrimiçi lisans, telemetri ya da bulut depolama kanalı için
'hiç veri gönderilmiyor' varsayımı bağımsız ağ ve kod testi olmadan kesinleştirilmez.
```

## 3. Veri envanteri ve akış tablosu

| Veri kategorisi / işleme | Kaynak & muhtemel kişisel veri | Koddan tespit edilen yer / alıcı | Kullanım amacı | Erişim / yetki | Saklama-silme | Statü |
| --- | --- | --- | --- | --- | --- | --- |
| Asıl dava belgesi | İsim, TCKN, UETS, sağlık, suç, adres vb. olabilir | Kullanıcının seçtiği `UYAP_Arsiv` fiziksel dosyaları | İçe alma, arşivleme ve bütünlük | İzinli yerel kullanıcı; Windows dosya ACL, disk erişimi incelenecek | Kullanıcı arşiv yaşam döngüsü; otomatik yok etme planı **belirsiz** | MEVCUT / RİSK |
| Dava meta verisi / SHA-256 | Mahkeme, dosya no, belge adı, dosya yolu, hash | `UYAP_Arsiv/_archive.db` ve uzantı durum saklama | Eşleştirme, durum, tekrar tespiti | Yerel agent/uzantı; kullanım hesabı sınırları test edilecek | Kayıt/onarım/uninstall ve veri sahibi talimatı ayrı incelenecek | MEVCUT / RİSK |
| AI okunabilir metin indeksi | Metin parçası bazında kişi ve özel veri olabilir | `UYAP_Arsiv/_ai/case-...-v1.sqlite`, `ai_chunks.text` | Arama ve analiz | Sadece seçilen dava; başka dava izolasyonu test edilmeli | Silme/yeniden indeksleme/backup ve FTS yan dosyaları incelenecek | MEVCUT / YÜKSEK RİSK |
| Yerel AI sonucu | Özet, olay/kişi/rol, kaynak refs | `%LOCALAPPDATA%/UYAPArsiv/AI_Summaries` + arşiv özeti aynası | Sonucu yeniden görüntüleme ve PDF | Yerel kullanıcı; başka Windows hesabı erişimi incelenecek | Kayıt geçmişi silme var; `.trash` ve yedi günlük temizleme davranışı sürümde doğrulanmalı | MEVCUT / RİSK |
| AI tanılama JSON | Oturum/dava anahtarı, seçilen kaynak başlıkları ve **ilk 1200 karakter model cevabı** hassas olabilir | `%LOCALAPPDATA%/UYAPArsiv/ai-diagnostic-latest.json` | Hata çözümü | Yerel; kullanıcı gönderirse destek görebilir | Güncel tek dosya üzerine yazma; yedek/kopya, dışa aktarma ve maskelenme kontrolü eksik | **P0 AÇIK** |
| LM Studio lokal AI | Seçilen metin parçaları, model komutları | localhost `127.0.0.1`; cihazdaki model sunucusu | İsteğe bağlı analiz | Kullanıcı tarafından başlatılan yerel servis; port erişim kapsamı kontrol edilecek | Model sunucusunun log ve bellek/önbellek davranışı incelenecek | KOŞULLU |
| Harici AI | Kısa kaynak metinleri; yine hukuk sırrı / kişisel veri olabilir | `https://api.openai.com/v1/responses`; `store:false` istemi | Opsiyonel bulut AI | Üçüncü taraf sağlayıcı; yetkilendirme ve sağlayıcı sözleşmesi gerekli | Sağlayıcının ayrı saklama/işleme politikası kontrol edilecek | **MÜŞTERİ BETASINDA BLOCKED** |
| Giriş / lisans kimliği | E-posta, hesap, cihaz bilgisi (tasarlanan) | `account/account-config.js` içinde `enabled:false`; Supabase backend hazırlığı | Ticari hesap ve lisans, henüz nihai değil | Lisans sağlayıcısı / kimlik rolleri tanımlanmalı | Sözleşme, silme ve müşteri hesabı akışı henüz tasarlanmadı | PLANLANAN |
| Operasyonel log / destek paketi | Windows yol, dava adı, hata, IP vb. olabilir | Native Agent + yerel tanılama ve installer raporları | Destek / olay analizi | Destek yalnız seçilmiş, maskeli veri ve kullanıcının ayrı talebi ile | Saklama süresi, rotasyon, silme ve dışa aktarım politikası yazılacak | **P0 AÇIK (#6)** |
| Telemetri / kullanım analitiği | Olası cihaz ve kullanım metrikleri | Üretimde ne olduğu bağımsız ağ testiyle doğrulanmadı | V1 beta için zorunlu değil | Varsayılan kapalı / opt-in ilkesi önerisi | Toplama başlatılmadan önce envanter + bildirim gerekir | DOĞRULANMADI |

**İşleme şartı sütunu için hukuk uzmanı kararı gerekir.** Yukarıdaki hiçbir satır, tüm kişilerin açık rızasının alındığı veya veri sorumlusu/işleyen rollerinin belirlendiği anlamına gelmez.

## 4. Sorumlu ve işleyen rolleri — değerlendirme şablonu

| Faaliyet | Muhtemel rol dağılımı (hipotez) | Hukukî inceleme sorusu |
| --- | --- | --- |
| Avukatın kendi dava belgelerini yerelde düzenlemesi | Avukat/büro veri işleme amaç ve vasıtalarını belirleyebilir | Meslekî gizlilik, veri sorumluluğu ve işleme şartları somut faaliyet bazında incelensin |
| Yazılım geliştiricisi yalnız lisans sağlar, dava içeriğini görmez | Lisans hesap verilerinde ayrı veri sorumlusu olabilir | Lisans platformu hangi kişisel veriyi niçin ve ne kadar süre tutuyor? |
| Ücretli destek / uzaktan hata çözme | Belge/dava meta verisine erişirse ilişkisel veri işleyen yükümlülükleri doğabilir | Destek veri akışı, talimat, gizlilik, alt işleyen sözleşmesi var mı? |
| Bulut AI sağlayıcısı | Sağlayıcı rolü ve veri aktarım zinciri sözleşmeye bağlı | KVKK m.5/6/9, meslek sırrı, yurt dışı aktarım ve alt işleyenleri hukukçu onayladı mı? |
| Pilot kullanım ve ekran paylaşımı | Görüntülenen gerçek dava verisi üçüncü kişiye sızabilir | Sentetik veri önceliği, açık izin, maskeli demo ve kayıt kısıtı var mı? |

## 5. Öncelikli teknik ve idari tedbirler

### P0 — Beta dış pilot öncesinde zorunlu inceleme
- **P0-01** AI tanı dosyasına model yanıtı önizlemesi / `uncitedClaims` / `selectedSources` içinde hassas metin sızmasını engelle; destek dışa aktarımını varsayılan maskeli hale getir, yetkilendirilmiş kullanıcı kontrolü getir. GitHub #6.
- **P0-02** Ticari beta yapılandırmasında portal otomasyonunu kapalıya alma için gerçek teknik `feature flag` ve E2E test; tasarım kararı yazılım davranışını kendi başına kapatmaz. [S1-03](S1_PORTAL_COMPLIANCE.md).
- **P0-03** Windows yerel dosya yetkileri, dizin geçişi/symlink, çok kullanıcılı oturum ve ortak cihaz testleri; arşiv+SQLite+backuplarda sır saklama.
- **P0-04** Dosya + AI indeks + özet + tanılama + geri alma sırasında silme/tutma tutarlılığı; Windows disk şifrelemesi ve kilitli cihaz başlığı kullanım rehberinde.
- **P0-05** Harici AI çağrısı ve müşteri içerik paylaşımını beta varsayılanında devre dışı tutmak; yalnız opt-in değildir, bağımsız KVKK / sözleşme kabulü gerekir.
- **P0-06** Chrome izinlerinin ve `https://*.uyap.gov.tr/*`, `https://*.supabase.co/*` host kapsamının sürüm özelliğine göre asgariye indirildiğini doğrula; mevcut manifest gereksinimleri ayrıca teknik olarak incelenecek.

### P1 — Beta hazırlığı
- Hesap etkinleştirme için asgari veri, anlaşılır aydınlatma, silme ve talep kanalı.
- Şifreli güvenilir yedek / geri yükleme, bütünlük kanıtı, kurtarma prosedürü.
- Destek ve ihlal müdahalesi: güvenlik olayı, kayıt erişimi, bildirim değerlendirme ve sorumlu iletişim.
- Gizlilik politikası, ürün içi AI izin açıklamaları, silme yönergesi ve Chrome mağaza kullanıcı verisi beyanı.
- Özel veri ve UDF/PDF metin çıkarma kalite/yanlış aktarma testleri.

## 6. Pilot kontrol testleri

| Test ID | Sentetik veri ile senaryo | Beklenen kanıt |
| --- | --- | --- |
| PRIV-01 | Dava A indeks oluştur → Dava B sorgula | Diğer dava metni/citation gelmez |
| PRIV-02 | Kişisel verili sentetik evrakla hata üret | Tanı dışa aktarımında kişi, dosya yolu, özet metni görünmez |
| PRIV-03 | Yerel AI kapalı / bağlantı yok | Arşiv arama, listeleme ve dosya açma çalışır |
| PRIV-04 | Bulut AI için kullanıcı izin vermemiş | Harici sağlayıcıya belge/snippet aktarımı gerçekleşmez (ağ kanıtı) |
| PRIV-05 | Windows ikinci kullanıcı hesabı | Arşive/SQLite/özetlere yetkisiz erişim engellenir |
| PRIV-06 | Silme veya yeniden indeksleme | Beklenmeyen eski metin parçası / FTS / .trash / backup sızıntısı olmaz veya açıkça politikaya uygun |
| PRIV-07 | İlk kurulum/upgrade/rollback/uninstall | Kullanıcı arşivi ve veri tercihleri korunur, veri silinmediği kanıtlanır |
| PRIV-08 | Uzantı izinleri ve host kapsamı | Gereksiz yetki yok; mağaza açıklamaları kodla eşleşir |

Kanıtlar sentetik veya maskelenmiş vaka veri setleriyle oluşturulmalı; canlı gerçek dava adları ve ham belge içerikleri GitHub issue'larına, CI loglarına ya da destek paketine eklenmemeli.

## 7. Açık ürün kararları (15.10.2026)

- **Onaylı:** Yerel arşiv MVP çekirdeği ve opsiyonel/ayrı kabul testli AI.
- **Blocked:** UYAP portal otomasyonu ticari beta kullanımı; hukukî uygunluk değerlendirmesi açık.
- **Blocked:** Harici bulut AI ile müşteri dosyasından metin aktarımı, işleme/aktarım ve gizlilik sözleşmeleri tamamlanmadan.
- **Açık:** Veri sorumlusu/işleyen tespiti, saklama ve silme takvimi, şifreleme profili, veri ihlali prosedürü, lisans/kimlik servisi.
- **Açık:** Yerel AI index ve özetlerin çoklu Windows kullanıcı, backup ve destek erişimi altında güvenli izolasyonu.
- **Açık:** Chrome mağaza gizlilik açıklaması ve `tr.gov.uyap.archive_agent` marka/kimlik riskinin çözümü.

## 8. S1-04 çıkış kapısı

1. Veri envanterindeki her satır için ürün sahibinin gerçek saklama yeri, erişen roller, silme süresi ve eğer varsa dış aktarım noktası doğrulanır.
2. Teknik testlerle ağ çıkışı, gizli verisiz destek tanısı ve yerel kullanıcı izolasyonu kanıtlanır.
3. Hukukçu KVKK işleme şartları, gizlilik/meslek sırrı, veri sorumlusu ve veri işleyen rolleri, yurt dışına aktarım açısından gerekçeli karar verir.
4. Gizlilik politikası, AI izin metni, destek veri paylaşımı ve kayıt silme prosedürü taslakları hazırlanır.
5. **S1-04 ancak bu incelemeler ve yazılı onaylarla “APPROVED” olur.** Belge oluşturulması yalnız ilk envanter taslağının tamamlandığını gösterir.

## 9. Kaynaklar — 15 Ekim 2026

**Resmî hukukî ve mağaza kaynakları**

[KV1] KVKK, Veri Güvenliğine İlişkin Yükümlülükler: https://www.kvkk.gov.tr/Icerik/2040/Veri-Guvenligine-Iliskin-Yukumlulukler  
[KV2] KVKK, Özel Nitelikli Kişisel Veriler: https://www.kvkk.gov.tr/Icerik/2051/Ozel-Nitelikli-Kisisel-Veriler  
[KV3] KVKK, Yurt İçi Aktarım (veri sorumlusu/veri işleyen rolleri): https://www.kvkk.gov.tr/Icerik/2052/Yurtici-Aktarim  
[KV4] KVKK, Kişisel Verilerin Yurt Dışına Aktarılması Rehberi: https://www.kvkk.gov.tr/Icerik/8142/Kisisel-Verilerin-Yurt-Disina-Aktarilmasi-Rehberi  
[C1] Chrome Web Store Developer Program Policies: https://developer.chrome.com/docs/webstore/program-policies/policies

**Kod ve ürün kaynakları** (`feature/ai-six-workflows`; `develop` içinde aynı davranışın bulunduğu varsayılmaz)

- [AI policy](../../config/ai-policy.json): local gateway, provider seçenekleri, varsayılan kapalı ve kaynak kuralı.
- [AI index](../../native/ai_local_index.py): kaynak doğrulama, `ai_documents` ve `ai_chunks` metin alanı.
- [Native Agent](../../native/uyap_archive_agent.py): `ai-diagnostic-latest.json` alanları ve summary klasörleri.
- [AI provider](../../native/ai_provider.py): local LM Studio veya OpenAI çağrısı.
- [Chrome manifest](../../manifest.json): izinler, uyap/supabase host erişimi.
- [Hesap config](../../account/account-config.js): `enabled:false` ve gelecekte Supabase hesabı.
- [Issue #6](../../issues/6): tanı loglarının güvenli maskeleme gereksinimi.

Bu kaynakların hangi dala ait olduğu önemlidir; PR birleştirmesi ve sürüm kabulünden önce ref'ler doğrulanmalıdır.
