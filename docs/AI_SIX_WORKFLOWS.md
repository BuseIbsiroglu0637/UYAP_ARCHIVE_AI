# UYAP Archive AI — altı bağımsız AI iş akışı (geliştirme dalı)

Bu değişiklik RC15.9.38'in nihai sürüm ilanı değildir. Gerçek UYAP/Windows
kabul testi ve LM Studio canlı testi yapılmadan üretime hazır kabul edilmez.

## Ayrı işlem kimlikleri
- `case_summary`: kısa dosya özeti; deterministik önem sıralaması ve mevcut
  özet geçmişi/PDF çıktı akışı korunur.
- `important_dates`: açık tarih ve hukuki işlem bağlamlı kaynak parçaları
  seçilir. Belge tarihi tek başına duruşma veya süre sonu sayılmaz.
- `new_updates`: ilk çalıştırmada dava indeksinde yalnız hash'lerden oluşan
  başlangıç karşılaştırma kaydı alınır. Sonraki tam indeks yenilemelerinde
  yeni/değişen doğrulanmış evrakları kıyaslar. İlk çağrıda gelişme uydurmaz.
  Karşılaştırma kaydı kullanıcı onayıyla yenilenecek ayrı UI tasarımına açık;
  mevcut geliştirme sürümünde ilk başlangıç kaydı korunur.
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
- "Yeni Ne Var?" ilk çalışmasında baseline oluşturulur; değişiklik yoksa model
  çağrılmaz. Kalıcı baseline aynı davanın özel SQLite indeksinde tutulur.
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
6. Yeni Ne Var? ilk seferde başlangıç kaydı oluşturmalı, ikinci seferde
   değişiklik yok demeli; yeni doğrulanmış evrak eklenip indeks yenilendiğinde
   yalnız yeni/değişen evrakı bulmalı.
7. Test raporunda çıktı kalitesi, süre ve yanlış iddia örnekleri saklanmalı;
   kullanıcıya paylaşılacak JSON'larda özel belge içeriği maskelenmeli.
