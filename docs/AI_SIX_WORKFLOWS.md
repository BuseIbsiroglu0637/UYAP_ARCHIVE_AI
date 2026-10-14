# UYAP Archive AI — altı bağımsız AI iş akışı (geliştirme dalı)

Bu değişiklik RC15.9.38'in nihai sürüm ilanı değildir. Gerçek UYAP/Windows
kabul testi ve LM Studio canlı testi yapılmadan üretime hazır kabul edilmez.

## Ayrı işlem kimlikleri
- `case_summary`: kısa dosya özeti; deterministik önem sıralaması ve mevcut
  özet geçmişi/PDF çıktı akışı korunur.
- `important_dates`: AI sağlayıcısı çağırmadan, yerel kurallarla duruşma,
  karar, kesinleşme ve süre tarihlerini çıkarır. Belge tarihi tek başına
  duruşma veya süre sonu sayılmaz. Son çalıştırmadan beri arşive giren
  evraklara dayanan satırlar "YENİ" olarak işaretlenir (görülen evrak
  listesi yalnız kimlik hash'i olarak dava indeksinde tutulur).
- `court_orders` ("Ara Kararlar ve Yapılacaklar"): AI sağlayıcısı çağırmadan,
  son üç zabıt/ara karar/tensip kaydındaki hüküm fıkralarını okur; her
  maddede kimden ne istendiğini, verilen süreyi ve sonrasında arşive giren
  ilgili evrakı (cevap yazısı, dilekçe, tebligat, rapor) kaynak göstererek
  listeler. Süre sonları "en erken" olarak verilir; tebliğ tarihi arşivden
  kesin bilinmez.
- `new_updates`: eski "Yeni Ne Var?" kartı. Arayüzden kaldırıldı; yeni
  evrak işaretleri Önemli Tarihler içine taşındı. Eski istemciler için
  işlem kimliği native tarafta kabul edilmeye devam eder.
- `parties` ("Taraflar"): AI sağlayıcısı çağırmadan evrak künyelerini okur.
  Her evrak, kendi künyesindeki esas / soruşturma numarasıyla bir dosyaya
  bağlanır; böylece dosyaya eklenen başka dosyaların rolleri bu dosyanın
  taraflarına karışmaz. Gösterir: bu dosyanın tarafları, güncel ve önceki
  vekil / müdafiler (tarihleriyle), "karşı davacı", "istinaf kanun yoluna
  başvuran" gibi nitelikler; hakim (değişmişse öncekiler), bilirkişi ve
  tanıklar; aynı kişilerin eklenen diğer dava (dosya başına bir satır), icra
  takibi ve soruşturmalardaki rolleri (kişi başına özet). Adres, kimlik,
  telefon ve UETS bilgisi gösterilmez; diğer dosya eşleşmeleri ad-soyad
  aynılığına dayanır.
- `classification` ("Akıllı Sınıflandırma") ve `chronology` ("Kronoloji"):
  arayüzden kaldırıldı. En fazla birkaç kaynak parçası görüyorlardı; evrak
  türünü UYAP zaten veriyor, tüm evrakların tarih sırası "Zaman Çizelgesi"
  sayfasında, olaylar Önemli Tarihler'de. Eski istemciler için işlem
  kimlikleri native tarafta kabul edilmeye devam eder.

`ask`: genel soru-cevap için ayrı eski akış.

## PDF okuma
- PDF metni önce gömülü `pypdf` ile okunur (yazı tipleri, ToUnicode CMap,
  nesne akışları); yetersiz kalırsa eski içerik akışı okuyucusu denenir. İki
  sonuç da aynı kalite kontrolünden geçer.
- Eski okuyucu UYAP PDF'lerinin yalnız ~%3'ünü okuyabiliyordu; arşivden
  rastgele 150 PDF'te yeni okuyucu 134'ünü kalite kontrolünden geçirdi.
  Kalan ~%9 taranmış görüntüdür; OCR henüz yoktur.
- Okuma 20 sn bütçeli ayrı bir iş parçacığında çalışır; bütçeyi aşan PDF
  eski okuyucuya düşer, iki takılmadan sonra o işlem boyunca yeni okuyucu
  kullanılmaz.
- Dava indeksinin kapsam kaydında `pdfReader` sürümü tutulur. Bu sürümden
  önce oluşturulmuş indeks için Workspace bir kez "İndeksi güncelle (PDF
  okuma)" önerir.
- Kütüphane `native/vendor` altındadır; kurulum `runtime\vendor` olarak
  kopyalar (bkz. `native/vendor/README.md`).

## Kaydedilen sonuçlar
- Dosya Özeti, Önemli Tarihler, Ara Kararlar ve Yapılacaklar ve Taraflar
  sonuçları her çalıştırmada yerel arşive kaydedilir
  (`%LOCALAPPDATA%\UYAPArsiv\AI_Summaries\<dava>` ve
  `UYAP_Arsiv/_ai/summaries/<dava>` aynası). Aynı içerik tekrar kaydedilmez.
- Kayıt geçmişi türe göre süzülebilir; her kayıt yeniden açılabilir.
  Dosya Özeti, Önemli Tarihler ve Ara Kararlar için PDF alınabilir;
  Taraflar için PDF yoktur (`SAVED_PDF_WORKFLOWS`).
- Liste yanıtı bölüm içeriği taşımaz (native mesaj sınırı); kural
  görünümü açılırken tek kayıt `ai_get_summary` ile tam okunur.
- Silme tek tek ve onaylıdır; 10 saniye "Geri al" sunulur. Silinen kayıt
  merkezi klasörden ve aynadan `.trash` altına taşınır, kimliği
  `_deleted.json`'a yazılır (aynadan geri gelmez), 7 gün sonra kalıcı
  silinir. Arşiv evrakları, dava indeksi ve indirilmiş PDF'lere dokunulmaz.

## Güvenlik ve davranış
- Açık `workflowId` Workspace, servis çalışanı, Native Agent ve sağlayıcıya
  taşınır. Başlıktaki "özet" kelimesinden işlem kimliği tahmin edilmez.
- Bilinmeyen `workflowId` hata verir, otomatik dosya özetine dönüşmez.
- Veri kaynağı yalnız tam fiziksel SHA-256 kanıtlı yerel dava AI indeksidir.
- Sağlayıcıya yalnız seçilmiş kısa metin parçaları gider; ham evrak dosyası
  veya kimlik belirleyen dosya yolu aktarılmaz.
- Dosya özeti dışındaki olgusal yanıtlarda `[Dxxxxxx]` kaynak atfı zorunludur.
- Önemli Tarihler, Ara Kararlar ve Yapılacaklar ve Taraflar LM Studio
  kapalıyken de çalışır; yalnız tam dava indeksi gerekir. Evrak kataloğu için indeksin bu
  sürümle bir kez yenilenmesi gerekir.
- LM Studio yalnız localhost üzerinden çağrılır.

## Geliştirici testi
```powershell
py -3 native/ai_context_contract_test.py
py -3 native/saved_results_regression_test.py
py -3 native/pdf_reader_regression_test.py
```

## Canlı kabul testi (henüz yapılmadı)
1. LM Studio Model Server açık; model `/v1/models` içinde görünür.
2. Tam fiziksel doğrulanmış test dava dosyasını seçip indeks oluştur.
3. Dört iş akışını ve Dosyada Sor'u ayrı ayrı çalıştır. Her yanıtın doğru
   başlık ve işlem kimliğiyle gösterildiğini, kaynakların ilgili dava dosyasına
   ait olduğunu kontrol et.
4. İndeksi PDF okuyucu sürümüyle yeniden oluşturunca indekslenen evrak
   sayısının arttığını ve "metni okunamadı" notlarının azaldığını kontrol et.
5. Tarih/rol çıktıları doğrulanmış evraklardan manuel kontrol edilmeli.
6. Önemli Tarihler ilk seferde "YENİ" işareti koymamalı; yeni doğrulanmış
   evrak eklenip indeks yenilendiğinde yalnız o evraka dayanan satırlar
   işaretlenmeli. Ara Kararlar'daki her madde ilgili zabıt/karar evrakıyla
   elle karşılaştırılmalı.
7. Test raporunda çıktı kalitesi, süre ve yanlış iddia örnekleri saklanmalı;
   kullanıcıya paylaşılacak JSON'larda özel belge içeriği maskelenmeli.
