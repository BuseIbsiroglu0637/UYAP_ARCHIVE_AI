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
- `parties`: yalnız belgelerde açıkça görülen kişi/kurum/rol eşleşmelerini
  kaynak göstererek aktarır.
- `classification`: sınırlı sayıda seçilmiş doğrulanmış kaynağı sınıflandırır.
  Tam dava indeksinin kapsandığı iddia edilmez.
- `chronology`: tarihi veya olay tarihi kanıtı bulunan kaynak parçalarını
  kronolojik analize sunar. Belge tarihi ile olay tarihi ayrılır.

`ask`: genel soru-cevap için ayrı eski akış.

## Güvenlik ve davranış
- Açık `workflowId` Workspace, servis çalışanı, Native Agent ve sağlayıcıya
  taşınır. Başlıktaki "özet" kelimesinden işlem kimliği tahmin edilmez.
- Bilinmeyen `workflowId` hata verir, otomatik dosya özetine dönüşmez.
- Veri kaynağı yalnız tam fiziksel SHA-256 kanıtlı yerel dava AI indeksidir.
- Sağlayıcıya yalnız seçilmiş kısa metin parçaları gider; ham evrak dosyası
  veya kimlik belirleyen dosya yolu aktarılmaz.
- Dosya özeti dışındaki olgusal yanıtlarda `[Dxxxxxx]` kaynak atfı zorunludur.
- Önemli Tarihler ve Ara Kararlar ve Yapılacaklar LM Studio kapalıyken de
  çalışır; yalnız tam dava indeksi gerekir. Evrak kataloğu için indeksin bu
  sürümle bir kez yenilenmesi gerekir.
- LM Studio yalnız localhost üzerinden çağrılır.

## Geliştirici testi
```powershell
py -3 native/ai_context_contract_test.py
```

## Canlı kabul testi (henüz yapılmadı)
1. LM Studio Model Server açık; model `/v1/models` içinde görünür.
2. Tam fiziksel doğrulanmış test dava dosyasını seçip indeks oluştur.
3. Altı iş akışını ayrı ayrı çalıştır. Her yanıtın doğru başlık ve işlem
   kimliğiyle gösterildiğini, kaynakların ilgili dava dosyasına ait olduğunu kontrol et.
4. Kronoloji düğmesi dosya özetine düşmemeli.
5. Tarih/rol/sınıflandırma çıktıları doğrulanmış evraklardan manuel kontrol edilmeli.
6. Önemli Tarihler ilk seferde "YENİ" işareti koymamalı; yeni doğrulanmış
   evrak eklenip indeks yenilendiğinde yalnız o evraka dayanan satırlar
   işaretlenmeli. Ara Kararlar'daki her madde ilgili zabıt/karar evrakıyla
   elle karşılaştırılmalı.
7. Test raporunda çıktı kalitesi, süre ve yanlış iddia örnekleri saklanmalı;
   kullanıcıya paylaşılacak JSON'larda özel belge içeriği maskelenmeli.
