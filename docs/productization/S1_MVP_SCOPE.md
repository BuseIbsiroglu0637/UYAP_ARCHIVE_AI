# S1-02 | MVP Kapsam ve Kullanıcı Akışları (taslak)

**Sprint:** Ürünleştirme Sprint 01  
**Hedef kitle:** Bireysel avukat ve 2–5 kişilik küçük büro  
**Tarih:** 15 Ekim 2026  
**Durum:** ÜRÜN SAHİBİ İNCELEMESİNDE. Kabul edilmiş veya müşteriye sunulan bir kapsam değildir.  
**Bağlam:** [S1-01 Ürün Brief'i](S1_PRODUCT_BRIEF.md), [Sprint 01 Planı](SPRINT_01_PLAN.md).

## 1. V1 karar matrisi

| Kimlik | İşlev | Karar önerisi | Mevcut gözlem | Çıkış kapısı |
| --- | --- | --- | --- | --- |
| M-01 | Yetkili edinilmiş belgelerden yerel arşiv / içe alma | DAHİL | Taşınabilir/eski arşiv seçimi ve fiziksel doğrulama mevcut; genel dışa aktarım içe alma alt akışı ürün için ayrıca test edilmeli | Kullanıcı hak/izin kontrolü; sadece yerel arşive güvenli alma ve doğru dava eşleme |
| M-02 | SHA-256, boyut, eksik ve mükerrer belge | DAHİL | Kodda kapsamlı fiziksel doğrulama bulunuyor | Yanlış pozitif verified = 0; sessiz overwrite = 0; reddedilen kanıt görünür |
| M-03 | Dava / evrak listesi ve arama | DAHİL | Workspace var | Dosya bazlı gezinme ve açma; kategori / tarih arama testi |
| M-04 | Zaman çizelgesi ve sorun merkezi | DAHİL | Workspace modülleri var | Gerçek dosya–görsel ekran tutarlılığı ve hata gerekçesi |
| M-05 | İmzalı Windows kurulum, onarım, geri alma | BETA ÖN KOŞUL | Dev kurulum/CI mevcut; gerçek imzalı kabul yok | Win10+Win11 Chrome+Edge, repair/rollback/uninstall kanıtı |
| M-06 | Belge üzerinden kaynaklı dosya özeti | KOŞULLU DAHİL | Yerel model / provider akışları mevcut | Kapsam belirtimi, hukuk uzmanı kaynak/yanlış iddia kabulü |
| M-07 | Önemli Tarihler, Taraflar, Ara Kararlar | AYRI ONAY KAPISI | Kural tabanlı iş akışları geliştirilmiş | Sentetik + izinli veri seti, yanlış kesinlik sıfır, belirsizliği göster |
| M-08 | Tebligatlar ve hukukî süreler | AYRI YÜKSEK RİSK KAPISI | Kural tabanlı analiz var | Hukuk uzmanı ile tebliğ/başlangıç/resmî tatil/adli tatil ve yanlış yönlendirme senaryoları |
| M-09 | UYAP/Kurum UYAP portal otomatik tarama/indirme | TİCARİ BETA BLOKE | Geliştirmeler var | Güncel portal sözleşmesi + gerekli izin + bağımsız hukukî güvenlik onayı |
| M-10 | Bulut evrak saklama ve ekip senkronizasyonu | KAPSAM DIŞI | İlk beta için gerekli değil | Sonraki ürün keşfi |
| M-11 | Mobil uygulama | KAPSAM DIŞI | İlk beta Windows | Daha sonraki sürüm |
| M-12 | Lisans etkinleştirme | BETA ÖN KOŞUL | Ticari lisanslama eksik | Minimum cihaz bazlı lisans, offline çalışma garantisi, müşteri evrakı lisans sunucusuna gitmez |

## 2. Temel kullanıcı akışları

### U1 — Kurulum ve ilk başlatma

1. Kullanıcı bağımsız ürün, veri ve izin bilgilendirmesini görür.
2. İmzalı Windows kurulumunu yapar.
3. Chrome veya Edge uzantısı ile yerel Native Agent bağlantısı doğrulanır.
4. Kullanıcı yerel arşiv klasörünü seçer.
5. Bağlantı yoksa ne yapılacağı ve arşiv konumu anlaşılır biçimde gösterilir.

**Kabul:** Mevcut hukuki arşiv silinmez. 2/2 ping, kapsamlı registry ve geri alma kanıtı alınır. Kurulum hatası sürüm yükseltme kesintisine dönüşmez.

### U2 — Arşiv içe aktarma / taşıma

1. Kullanıcı yetkili olarak elde ettiği yerel belgeleri veya eski UYAP arşiv dizinini seçer.
2. Uygulama desteklenen biçimleri, dosya yolu sınırlarını ve dava eşleşme bilgilerini değerlendirir.
3. Fiziksel kanıt ve SHA-256 hesaplanır; kaynak korunarak kopyalanır/bağlanır.
4. Gözden geçirilmesi gereken evraklar kesin VERIFIED statüsü almadan ayrı listelenir.
5. Kullanıcı içe alma sonucunu ve eksikleri rapor olarak görür.

**Kabul:** Portal otomasyonuna gerek kalmaz; yanlış dava dosyasına bağlanma veya sessiz üstüne yazma yok; desteklenmeyen biçim açıkça belirtilir.

### U3 — İnceleme, arama ve sorun çözme

1. Kullanıcı dava listesinden kayıt seçer.
2. Evrakları tarih/başlık/türe göre arar ve açar.
3. Sorun Merkezi'nde eksik/bozuk/mükerrer kanıt gerekçesi görülür.
4. Veri onarım işlemi yalnız kullanıcının açık isteğiyle yapılır.
5. Sonuç yeniden fiziksel kanıtlanır.

**Kabul:** “Kayıt var” ile “fiziksel olarak doğrulanmış” ayrı gösterilir. Ağ olmadığında yerel arama çalışır.

### U4 — Kaynaklı belge analizi (opsiyonel)

1. Kullanıcı dava dosyası ve analiz türünü seçer.
2. UI okunabilir metin kapsamını (ör. 192/520 evrak) ve taranmış/OCR dışı kayıtları gösterir.
3. AI yalnız kabul edilmiş doğrulanmış metin parçalarını kullanır; kaynak referanslarını belirtir.
4. Üretilen ifade kaynak doğrulamasından geçmezse görünür biçimde reddedilir; riskli tarih/rol kesinliği oluşturulmaz.
5. Sonuç yasal tavsiye değil, kaynaklı inceleme yardımcısı olarak sunulur.

**Kabul:** LM Studio kapalıyken arşivleme devam eder; metni okunamayan  evraklar hakkında kapsam dışı uyarısı bulunur; hukukî analitikler bağımsız kabul şartına bağlıdır.

## 3. Öncelikli olmayan durumlar

- Kullanıcı adına arka planda sınırsız portal taraması.
- Kullanıcı rızası olmadan hukuk evrakının bulut modeline gönderilmesi.
- Güvenliği aşmak için robot engellemesi, captcha veya erişim sınırlarını atlama.
- İlk betada merkezi büro dosyası veritabanı ve çok kullanıcılı ACL.
- Belgelerden bağımsız kesin dava sonucu ya da yasal süre tavsiyesi.

## 4. En büyük kapsam riskleri

| Risk | V1 davranışı |
| --- | --- |
| UYAP portal kullanım izni belirsiz | Portal otomasyonu ticari sürümde kapalı; yerel içe aktarma esas |
| OCR olmayan taranmış evrak | Arşiv fiziksel olarak korunur; AI “metni okunamadı” diye işaretler |
| Belge yanlış dava ile bağlandı | Verified verilmez; insan incelemesi ve kanıt gerekli |
| Model kaynaksız olgu üretti | Yanıt kaynak kapısında reddedilir; uydurma kaynak eklenmez |
| Tebligat süresi belirsiz | Tarih aracı bilgi amaçlı; kesin hak kaybı doğuracak karar desteklenmez |
| Kurulum sırasında DLL kilidi | Güvenli yeniden deneme; çalışan süreç tanısı; arşiv korunur |

## 5. Çıktı ve açık kararlar

**Ürün sahibinden beklenen karar:**
1. İlk beta **yerel içe alma + doğrulama + arama** çekirdeğiyle başlasın mı?
2. Kaynaklı AI, ilk etapta bağımsız ve opsiyonel bir modül olarak kalsın mı?
3. Portal otomasyonu uygunluk onayı gelene kadar ticari beta dışında tutulsun mu?

**S1-02 kabul kriteri:** Yukarıdaki üç kapsam kararı yazılı onaylanır, U1–U4 akışları teknik uygulanabilirlik incelemesinden geçer ve kapsam dışında bırakılan özellikler sürüm belgesine işlenir.

**Durum:** TASLAK; müşteri kabulü ve entegrasyon değişikliği henüz yapılmadı.
