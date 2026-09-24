# RC15.9.37 QUEUE-RECOVERY işlem akışı

1. Uzantı/popup açılır; kullanıcı başka bilgisayardaki yarım arşiv için **Başka bilgisayardaki arşivden devam et** düğmesine basar.
2. Native Agent Windows klasör seçicisini açar. `UYAP_Arsiv` veya onu içeren üst klasör doğrulanır ve seçim kullanıcı profilinde atomik olarak saklanır.
3. `GET_ARCHIVE_REGISTRY` Native Agent 2.7.64'e gider; seçilmiş taşınabilir kök otomatik adaydır.
4. Agent `UYAP_Arsiv/_archive.db` dosyasını **read-only** açar; DB yalnız indeks/metadata kaynağıdır.
4. `documents` satırları okunur; SQLite sayfası bozuksa rowid aralıklarıyla kurtarmalı tarama yapılır.
5. Her `document_id` için en güncel kayıt seçilir ve `institution_name + case_id` ile dosya indeksi oluşturulur.
6. İlk ekran için fiziksel katman ayrıca kontrol edilir:
   - dosya yok → `missing`
   - dosya var, kayıtlı boyut farklı → `size_mismatch`
   - dosya var, boyut uyumlu → `physicalReady` (hafif ilk-ekran kanıtı)
7. Popup ve Arşiv Merkezi Kurum/Daire + Dosya No + Evrak + Fiziksel Uygun + Eksik/Sorun + Boyut + Son Güncelleme gösterir.
8. Bir arşiv işi başladığında `sync_inventory` daha sıkı kontrol yapar: gerçek dosya okunur, boyut doğrulanır ve SHA-256 yeniden hesaplanır.
9. Native Agent yalnız bu güncel fiziksel SHA-256 kanıtı için `physicalProofs[level=sha256]` döner.
10. Extension yalnız bu kanıt varsa evrağı `UNCHANGED_LOCAL_ARCHIVE / SKIPPED` yapabilir.
11. Eski sürümden metadata-only `SKIPPED` kalmışsa terminal kayıtlar zorunlu yeniden fiziksel kanıtlamadan geçer.
12. Dosya silinmiş/değişmişse eski skip iptal edilir, kayıt `QUEUED` olur ve canlı UYAP ağacına yeniden bağlanır.
13. `ALREADY_ARCHIVED` kararı öncesinde tüm metadata-skip adayları zorunlu yeniden fiziksel SHA-256 kanıtlamadan geçirilir; ancak tüm evraklarda güncel `sha256 + pathExists + size` kanıtı bulunduğunda karar verilir.
14. Aynı boyutta farklı byte'lar SHA-256 eşleşmediği için arşivlenmiş kabul edilmez.
15. Yeni bir UYAP evrakı tarayıcının mevcut indirme klasörüne inse bile Native Agent onu seçili eski arşivin `_incoming` alanına taşır ve orada doğrular/finalleştirir.
16. Kaynak yol ilk ajan işleminden sonra kaybolmuşsa yeniden deneme seçili arşivdeki staging byte'ını bulur; farklı içerik hiçbir zaman ezilmez.
17. Eski DB `local_path` kaydı `evrak_<id>.*` biçimindeyse Agent seçilen üst kökte aynı basename'i arar.
18. Dosya ancak DB boyutu, DB SHA-256 değeri ve yapısal içerik doğrulaması birlikte geçerse kabul edilir.
19. Kaynak yerinde bırakılır; kanonik `UYAP_Arsiv` hedefine hard-link veya doğrulanmış atomik kopya oluşturulur.
20. Giriş/hata HTML'si, hash uyuşmazlığı veya farklı içerikli hedef güvenli biçimde reddedilir ve UYAP'tan yeniden indirme yolunda kalır.
21. Gerçek eski DB'deki `source_document_id`, kökteki `evrak_<source_document_id>.*` adıyla doğrudan indeks üzerinden bağlanır; eski `local_path` basename'inin farklı olması sorun değildir.
22. Yerel arşiv kayıt/listeme işlemleri ayrı `registry` kanalında yürür; büyük `_archive.db` taraması `sync_inventory` veya indirme kuyruğunu tutamaz.
23. `sync_inventory` en fazla 5 dakika bekler. Süre aşımı/hata kuyruk kilidini bırakır ve kalıcı kurtarma alarmı üzerinden kontrollü yeniden deneme yapılır.
24. Tarayıcı IndexedDB oturumu yoksa Native Agent `caseIndex` kayıtları Workspace üst seçicisine ve Dosyalar ekranına salt-okunur yerel kayıtlar olarak eklenir.
25. Kullanıcı aynı UYAP dosyasını açıp işi yeniden oluşturduğunda güncel UYAP envanteri fiziksel arşivle uzlaştırılır; doğrulanmış dosyalar yeniden indirilmez.
26. Aynı `attemptId` için geç öğrenilen uzantı/hedef yolu değişirse transfer günlüğü değişmez oturum+belge+staging sahibini doğrular ve önceki byte-kanıtlı sonucu tekrar oynatır.
27. Oturum, belge veya staging sahibi farklıysa yeniden kullanım kesin olarak reddedilir; farklı içeriğin başka evraka bağlanmasına izin verilmez.
28. Workspace açılışında tarayıcı oturumları ve aktif iş önce okunur; yerel `_archive.db` taraması bu ilk görünümü bloke etmez.
29. Yerel arşiv indeksi arka planda tamamlandığında dosya seçici, özet ve arşiv görünümü yerinde güncellenir.
30. Aynı anda gelen arşiv listeleme istekleri tek native çağrıya katılır; sonuç 60 saniye paylaşılır ve gereksiz tam taramalar kuyrukta birikmez.
31. Kurum UYAP dava bağlamı mesajı, yalnız uzantının kendi content script kimliği, `kurum.uyap.gov.tr` üst sekmesi ve güvenilir frame origin'i birlikte doğrulanırsa kabul edilir.
32. Alt frame ve `about:blank/srcdoc` geçişlerinde URL yerine Chrome'un gerçek `MessageSender.origin` kanıtı kullanılır; farklı origin mesajları fail-closed reddedilir.
33. PDF `%%EOF` sonrasında en fazla 256 baytlık tek bir yazdırılabilir ASCII PDF üretici yorumu varsa, xref tablo/akış doğrulaması da geçmek şartıyla kaynak baytları değiştirilmeden kabul edilir.
34. İç içe `%PDF`, yeni `%%EOF/startxref`, JavaScript/Launch/OpenAction, HTML veya rastgele ikili veri son ekleri fail-closed reddedilir.
35. Önceki sürümde `PDF_TRAILING_DATA` ile karantinaya alınan kayıtlar açılışta Native Agent 2.7.63 ile yerelden yeniden doğrulanır; geçerli olanlar yeniden indirilmeden VERIFIED durumuna taşınır.
36. PDF sonundaki tam `Invalid request` metni UYAP belge-servisi hatası olarak yeniden sınıflandırılır; bozuk yanıt karantinada kalır ve kayıt taze indirmeye alınır.
37. Art arda belge-servisi hatasında 15/30/60 saniyelik en fazla üç kontrollü test yapılır.
38. Üç test de başarısızsa kuyruk `CONTENT_ENDPOINT_REFRESH_REQUIRED` durumunda durur; hiçbir yeni UYAP isteği gönderilmez.
39. Kullanıcı UYAP dava/Evrak ekranını yenileyip devam ettiğinde kaynak bağı ve preflight yeniden doğrulanır, ardından tek half-open testi yapılır.
40. Geçmeyen kayıt karantinada korunur ve müdahale listesinde kalır; tanılamaya yalnız sınırlı suffix hex kanıtı eklenir.

Bu tasarımda “DB'de kayıt var” ile “fiziksel arşiv mevcut” aynı kavram değildir.

Native Agent: 2.7.64.

## RC15.9.37 canlı Workspace ek akışı

41. Workspace görünür durumdayken seçili oturum özeti 4 saniyede bir hafifçe sorgulanır; sayaç veya durum değişmişse ekran anında yeniden çizilir.
42. Dosyalar görünümü seçili oturum için yalnız değişiklik olduğunda 12 saniyelik kontrollü aralıkla rapor verisini yeniler.
43. Yerel arşiv registry'si veri değişikliğinde dirty olur; 15 saniyelik debounce sonunda `forceRefresh` ile taze tarama istenir. Başarılı doğrulama/senkron işlemleri background cache'i geçersiz kılar.
44. Native Agent caseIndex oluştururken eski bilgisayardan kalmış mutlak `local_path` yerine mevcut dosyaya çözümlenen rebased `target_path` adayını kullanır.
45. DB'de satırı olmayan fiziksel dosyalar da caseIndex'e eklenir; fiziksel olarak mevcut görünür, fakat SHA-256/DB kanıtı bulunmadığından sorun sayacına girer ve otomatik VERIFIED sayılmaz.

