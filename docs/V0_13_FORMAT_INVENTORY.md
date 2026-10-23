# UYAP Archive AI — v0.13 Gerçek Arşiv Dosya Türü Envanteri

Bu aşama daha önce 26.932 kayıtta 6.081 `unsupportedExcluded` sonucunu üreten yerel
v0.12 denetiminin ardından geliştirilmiştir. **6.081 kaydın kaçının ZIP olduğunu henüz bilmiyoruz.**

## Amaç

- ZIP, HTML, JSON, CSV, XML, DOC/DOCX, XLS/XLSX, görüntü vb. türleri sabit başlıklarla saymak.
- `_final`, `Final_Paket_*`, `archive-part-001.zip`, `_manifest.json` gibi Archive AI yönetim dosyalarını
  mahkeme evraklarından ayırmak.
- 19 sütunlu eski `_archive.db` şemasında `extension` sütunu olmadan çalışmak.
- Kayıt/dava/belge isimlerini, dosya yollarını, kullanıcı oluşturduğu uzantıları ve içeriği raporlamamak.

## Windows çalıştırma

```powershell
cd 'C:\Users\busei\Documents\GitHub\UYAP_ARCHIVE_AI-v012'
git fetch origin refs/heads/feature/v0.13-format-inventory:refs/remotes/origin/feature/v0.13-format-inventory
git worktree add '..\UYAP_ARCHIVE_AI-v013' origin/feature/v0.13-format-inventory
cd '..\UYAP_ARCHIVE_AI-v013'
python .\native\audit_archive_formats.py --archive-root "$archivePath"
```

Çıktıdaki `unsupportedByFormat.ZIP` ZIP adayı sayısıdır;
ZIP dosyalarının gerçekten okunabildiğini veya güvenli olduğunu kanıtlamaz.

## Güvenlik

- Sadece `status='VERIFIED'` olan satırlar desteklenen/desteklenmeyen türlere sınıflanır.
- Dosyalar açılmaz; evrak hash'leri ya da PDF/UDF/TIF gövdeleri okunmaz.
- Sütun varsa veritabanı uzantısı ile yol uzantısı uyuşmadığında `EXTENSION_CONFLICT` sayılır.
- Bildirilmemiş/düzensiz uzantılar sabit `OTHER` başlığı altında birleştirilir.
- Final paket ZIP'leri `internalExcluded` olur; evrak ZIP'leri `unsupportedByFormat.ZIP` olur.
- `supportedBySuffixOnly` yalnız uzantıya dayanır, kaynak SHA doğrulaması değildir.
- Gerçek evrakları Android DEBUG emülatörüne aktarmak bu çalışmanın kapsamı dışındadır.
- Önceden kabul edilen v0.11 Android, RC15.9.39 masaüstü ve v0.12 denetim dalları korunur.

## Sonraki karar

Format dökümü gözlemlendikten sonra yalnız ihtiyaç duyulan dosya türü için
salt-okunur, arşiv dışına çıkmayan, boyut ve zip-bomb korumalı inceleyici eklenebilir.
ZIP içeriği otomatik çıkarılmamalı; kapalı şifreli arşivler ve imza ayrıntıları ayrı değerlendirilmelidir.

## Test

`test_audit_archive_formats.py` sahte/kişisel veri içermeyen legacy ve modern SQLite şemaları,
tür sözlüğü, final paket dışlaması, çelişkili uzantı ve ad/yol sızıntısı kontrolü yapar.
Windows/Linux CI doğrulaması GitHub Actions üzerinde çalışır.
