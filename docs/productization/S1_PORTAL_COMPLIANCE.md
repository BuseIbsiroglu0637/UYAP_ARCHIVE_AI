# S1-03 | UYAP Portal Kullanım Uygunluğu ve Dağıtım Karar Kaydı

**Tarih / sürüm:** 15.10.2026 — taslak v0.1  
**Sorumluluk:** Ürün sahibi + Türkiye'de yetkin hukuk danışmanı + gerekiyorsa UYAP/Adalet Bakanlığı muhatabı  
**Durum:** **HUKUKÎ ONAY ALINMADI. PORTAL OTOMASYONU TİCARİ BETADA BLOCKED.**  
**Ürün karar kaydı:** DEC-S1-001 (yerel arşiv + opsiyonel AI). [S1-01](S1_PRODUCT_BRIEF.md) · [S1-02](S1_MVP_SCOPE.md)

> Bu bir hukuk müşavirliği görüşü veya Adalet Bakanlığı izin belgesi değildir. Belgelerdeki kaynak özeti ile risk azaltma amacıyla önerilen ürün politikası ayrı tutulur. 15 Ekim 2026 itibarıyla incelenen kamuya açık sayfalar güncel oturum sırasında kabul edilen sözleşmenin yerini tutmaz.

## 1. Kaynak temelli durum tespiti

### A. UYAP Avukat Portal

**Resmî açıklama:** Adalet Bakanlığı'na bağlı UYAP Portal Yönetim yardım açıklaması, Avukat Portal kullanım sözleşmesinin 9. maddesi kapsamında avukatlık programlarıyla olağan insan kullanımını aşan, robot benzeri işlemlerin portalı yavaşlatması/aksatması halinde erişim kısıtlaması riski açıkladığını belirtir. Ayrıca birden fazla elektronik imzayla farklı lokasyonlardan eş zamanlı erişim başlığını anmaktadır. [Kaynak A1]

**Resmî sözleşme:** Avukat Portal sözleşmesinin kamuya açık aktarımında 8. maddede Bakanlığın değişiklik yapabilme yetkisi; 9. maddede saldırı mahiyetinde eylemler ile erişimin sınırlanabilmesi; 10. maddede sözleşmeye aykırılıktan sorumluluk hükümleri bulunur. İmzalama anında kullanıcıya gösterilen güncel metin ayrıca alınmalıdır. [Kaynak A2]

**Bu kaynakların kesin olarak söylemediği:** Bütün üçüncü taraf yazılımların kategorik olarak yasak olduğu, yavaşlatmayan her robot kullanımının izinli olduğu veya ürün geliştiricisinin otomatik toplu evrak indirme hakkına sahip olduğu **çıkarılamaz**. Özellik ve işletim modeline göre resmî teyit/hukukî görüş gereklidir.

### B. UYAP Kurum Portal

**Resmî açıklama:** Kurum Portal tanıtımı, kamu kurumları/özel şirketler için ayrı başvuru ve Kurum Portal kullanım sözleşmesi imzalama süreci tarif eder; başvurunun ilgili ekranında “başvuru sözleşmesini indir” ile sözleşme üretilebildiğini belirtir. [Kaynak K1]

**Eksik kanıt:** Kamuya açık sayfadan Kurum Portal'ın spesifik otomasyon maddeleri ve üçüncü taraf yazılım yetkisi bu incelemede doğrulanamadı. Güncel Kurum Portal kullanım sözleşmesinin yetkili şekilde edinilen bir örneği ve varsa entegrasyon teknik şartları incelenmeden GO verilmez.

### C. Chrome Web Store ve ürün kimliği

Chrome Web Store politikaları kullanıcı verisi, dar izin kapsamı, açık gizlilik bildirimleri ve kurumsal onay/ilişki varmış gibi davranmama gereklilikleri içerir. Özellikle uygulama adı, ikonları, web mağazası metni ve `tr.gov.uyap.archive_agent` Native Messaging host kimliği bağımsız ürün açıklamasıyla tutarlı hâle getirilmelidir. Teknik kimlik değiştirme Chrome/Edge bağlantısını bozabileceğinden migrasyon testi gerekir. [Kaynak C1]

## 2. Özellik bazlı GO / BLOCKED matrisi

| Kullanım / veri erişimi | 15.10.2026 ürün kararı | Gerekçe | GO için gereken |
| --- | --- | --- | --- |
| Kullanıcının yetkili olarak elde ettiği yerel PDF/UDF/diğer dosyayı bilgisayarından seçmesi | **CONDITIONAL GO — geliştirme** | Portalı otomatik sorgulamaz; ancak meslek sırrı, veri işleme ve üçüncü kişi hakları devam eder | Veri haritası, yerel içe aktarma güvenliği, kullanıcı yetkisi ve veri amaçları incelemesi |
| Eski/taşınabilir arşivi kullanıcı seçimiyle onarma ve SHA-256 kontrolü | **CONDITIONAL GO — geliştirme** | Yerel arşiv işleme, yanlış dava/evrak ve veri silinmesi riski taşır | Fiziksel kanıt, klasör dışına çıkmama, korumalı onarım, gerçek saha testi |
| Dosya adlarını/tarihleri yerel listede arama | **CONDITIONAL GO — geliştirme** | Portal ağına yeni istek gerekmez; yerel veri erişimi kuralları geçerlidir | Rol/erişim, kullanım/silme bildirimleri |
| Kullanıcının tekil evrakı portal arayüzünden manuel indirmesi | **LEGAL REVIEW** | Kullanıcının hesabı ve o andaki sözleşme şartları belirleyicidir | Yetki / kullanım sözleşmesi ve portal akışı incelemesi |
| Portalın arka planda otomatik taranması, toplu sorgulama/indirme | **BLOCKED — ticari beta** | Avukat Portal 9. madde uyarıları; Kurum Portal sözleşmesi henüz incelenmedi | Sözleşme bazlı hukukî görüş, gerekiyorsa resmî yazılı izin, teknik güvenlik, saha testleri |
| CAPTCHA/robot engeli/erişim sınırı aşma, kimlik veya oturum kontrolünü atlatma | **BLOCKED — tasarım dışı** | Güvenli kullanım ve portal erişim şartlarıyla bağdaşmaz | Sprint kapsamına alınmaz |
| Sadece doğrulanmış yerel dosyalardan, cihazda LM Studio ile analiz | **CONDITIONAL GO — geliştirme** | Portal etkileşimi yok; kişisel veri, hukuk mesleği sırrı, model ve indeks kapsam riski sürer | [S1-04](S1_DATA_PRIVACY_MAP.md), analiz çıkış kapısı |
| Dava metin parçalarını harici bulut AI'a göndermek | **BLOCKED — müşteri beta, ayrıca inceleme** | Kullanıcı/sözleşme veri aktarım hakları ve KVKK yurt dışı aktarım riskleri | Veri sorumlusu/işleyen rolleri, KVKK m.5/6/9 analizi, sağlayıcı sözleşmesi, teknik tedbirler, uygun bilgilendirme/izin |

**Terminoloji:** CONDITIONAL GO sadece **kodu ve kapalı laboratuvar testini geliştirme** yetkisidir; satışa, dış pilot kullanımına veya hukuken uygunluğa onay değildir.

## 3. Geliştiriciden istenecek sözleşme/kanıt paketi

- [ ] Resmî **Avukat Portal Kullanım Sözleşmesi** güncel tam metni; kullanıcı oturumu esnasında onaylanan sürüm ve tarih (kimlik bilgileri maskeli).
- [ ] Resmî **Kurum Portal Kullanım Sözleşmesi** tam metni ve kullanıcı tipi (kamu kurum / özel şirket) ayrımı.
- [ ] Tarafların izin verdiği erişim ve arşiv kullanım kapsamı, API/entegrasyon kanalı bulunup bulunmadığı.
- [ ] Özellik özeti: hangi URL/ekran, istek sıklığı, otomatik indirme davranışı, kullanıcı etkileşimi, oluşan veri, saklama yeri, hata halinde durma.
- [ ] Ürün hukukçusunun tarihli ve imzalı değerlendirmesi: her özellik için GO / ŞARTA BAĞLI / RED; gerekçe ve koşullar.
- [ ] Gerekiyorsa UYAP/Adalet Bakanlığı muhatabından yetkili yazılı teyit veya izin; cevap tarih ve kimliği.
- [ ] Marka/kurum adına dair inceleme: `UYAP Archive AI` adı, ikon, “resmî değildir” açıklaması, host adının geçiş yöntemi.

## 4. Ürün kabulü için hukuka uygun şekilde hazırlanacak teknik kontrol listesi

1. **Portal kullanımına bağlı modüller ayrıştırılmalı:** Ticari dağıtımda portal otomasyonunun arayüz, background ve Native Agent seviyesinde kapalı olduğu testle ispatlanmalı. Mevcut kodun şu anda teknik olarak kapatıldığı **doğrulanmış değildir**.
2. **İnsan başlatması tek başına izin anlamına gelmez:** Otomasyonun işlem hızı ve tipi sözleşme incelemesinden geçirilir.
3. **Kimlik ve yetki:** Portal kimlik bilgisi ve imza PIN'i ürün sunucusuna gönderilmez; yetkisiz üçüncü taraf verisi çekilmez.
4. **Ağ/durum koruması:** 401/403, portal kullanım uyarısı, erişim kısıtı veya güvenlik kontrolü görüldüğünde iş güvenli durur. Engel aşma mantığı geliştirilmez.
5. **Yerel alternatif:** Uygunluk kararı beklenirken kullanıcı yetkisiyle elde ettiği yerel belgeleri içe alıp okuyabilmeli; portalı sorgulamayabilir.
6. **Müşteri açıklaması:** Ürün resmî UYAP/Adalet Bakanlığı hizmetiymiş gibi pazarlanmaz; bağımsız geliştirici, veri ve destek kanalları açıkça yazılır.

## 5. Hukukçu / resmî muhataba yöneltilecek kısa sorular

1. Avukat Portal sözleşmesindeki hangi maddeler, kullanıcının kendi hesabında çalışan otomatik toplu evrak işlemlerini kapsıyor? Hız sınırı tek ölçüt mü?
2. Üçüncü taraf ürün ile manuel dışa aktarılmış belgelerin **çevrimdışı yerel analizine** ilişkin ek lisans, veri veya güvenlik kısıtı var mı?
3. Kurum Portal sözleşmesinde Avukat Portal'dan farklı otomasyon, veri aktarımı veya kurum içi yetkilendirme yükümlülükleri bulunuyor mu?
4. Yazılı entegrasyon izni veya resmî arayüz varsa başvuru ve denetim yolu nedir?
5. Ürün adı, resmi UYAP logosu/markası, chrome uzantısı ve Native Messaging host kimliği bakımından kullanım sınırları nelerdir?

## 6. Karar protokolü / çıkış kapısı

- **Şu an:** S1-03 `LEGAL-REVIEW / BLOCKED (portal automation)`.
- **İzinli kaynak:** UYAP portalından etkilenmeyen yerel arşiv çekirdeği **geliştirme** çalışmalarına devam edilir.
- **S1-03 inceleme tamamlandı denmesi için:** Güncel iki portal sözleşmesi, ürün özelliği matrisi ve hukukçu görüşü bulunmalı; gerekiyorsa resmî izin kaydı alınmalı.
- **Beta portal otomasyonu GO:** Yalnız ayrıca tarihli yazılı onay ve teknik kapılar tamamlandıktan sonra.
- **Bu doküman onayı:** Ürün sahibi kararının kaydı ayrı; hukuk danışmanı onayı olmadan hukuki uygunluk kesinleştirilmez.

## 7. Resmî inceleme kaynakları (erişim / inceleme 15.10.2026)

[A1] UYAP Portal Yönetim sık sorulan sorular, Avukat Portal 9. madde açıklaması: https://portalyonetim.uyap.gov.tr/login.uyap  
[A2] UYAP resmî Avukat Portal Kullanım Sözleşmesi kamuya açık aktarımı: https://kurum.uyap.gov.tr/main/avukat.jsp  
[K1] UYAP Kurum Portal resmî tanıtımı ve başvuru sözleşmesi: https://www.esatis.uyap.gov.tr/main/kurum.jsp  
[C1] Chrome Web Store Developer Program Policies: https://developer.chrome.com/docs/webstore/program-policies/policies

Bu kaynaklar sürümünün daha sonra değişmeyeceğini veya ürünün otomasyonunun izinli olduğunu kanıtlamaz; nihai karar tarihli tam sözleşme ve hukukî değerlendirme ile verilir.
