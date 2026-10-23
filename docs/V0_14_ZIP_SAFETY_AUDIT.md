# UYAP Archive AI — v0.14 ZIP güvenlik envanteri

**Girdi:** v0.13 yerel, yalnız salt-okunur format dökümü:
- 26.932 kayıt; 26.884 VERIFIED; 48 VERIFIED olmayan;
- uzantıya göre desteklenmeyen 6.081: 3.061 ZIP, 2.440 HTML, 385 JPEG,
  170 XML, 15 XLSX ve 10 PNG;
- `ZIP` uzantısı, ZIP dosyasının sağlam veya güvenli olduğunu tek başına göstermez.

**Amaç:** ZIP dosyalarını açıp diske çıkarmadan, kaynağın SHA-256 bütünlüğünü
ve ZIP merkez dizinindeki (central directory) temel riskleri toplu sayılarla değerlendirmek.

## Yalnızca Windows'ta çalışma

UYAP_ARCHIVE_AI deposunu ve kabul edilmiş diğer dalları değiştirmeden:

```powershell
cd 'C:\Users\busei\Documents\GitHub\UYAP_ARCHIVE_AI-v013'
git fetch origin refs/heads/feature/v0.14-zip-safety-audit:refs/remotes/origin/feature/v0.14-zip-safety-audit
git worktree add '..\UYAP_ARCHIVE_AI-v014' origin/feature/v0.14-zip-safety-audit
cd '..\UYAP_ARCHIVE_AI-v014'
python .\native\audit_archive_zip.py --archive-root "$archivePath"
```

İşlem arşivin altındaki gerçek evrakları değiştirmez, dosya oluşturmaz,
e-imza geçerliliği iddia etmez veya Android'e gerçek içerik göndermez.

## Ön kontroller

- Kaynak, seçilen `UYAP_Arsiv` içinde bulunan mutlak, symlink içermeyen dosya olmalıdır.
- Veritabanındaki `status='VERIFIED'`, gerçek dosya uzunluğu ve SHA-256 eşleşmelidir.
- Bir ZIP kaynağı en fazla 32 MiB boyutunda işlenir.
- Tek tarama sırasında en çok 2 GiB kaynak ZIP baytı okunur; aşan adaylar
  `SCAN_BUDGET_REACHED` sayılır, otomatik kabul edilmez.
- SHA doğrulanan baytların *aynı immutable kopyası* üzerinden ZIP metadata okunur.
- Merkez dizinde üye sayısı en fazla 2.000; bir üyenin iddia edilen açılmış
  boyutu en fazla 32 MiB; toplam iddia edilen açılmış boyut en fazla 128 MiB.
- Sıkıştırma oranı 100:1 üzeri, güvensiz üye yolu, tekrarlı isim, symlink,
  parola koruması veya desteklenmeyen sıkıştırma çeşidi risk olarak sayılır.

## Sonuçların sınırı

- `SAFE_CENTRAL_DIRECTORY`: Fiziksel ZIP kaynağı SHA ile eşleşti; merkez
  dizindeki yukarıdaki sınır ve risk kontrollerini geçti.
- Bu sonuç **ZIP'in içindeki her üyenin CRC'sinin, içeriğinin veya UYAP imzasının
  doğrulandığı anlamına gelmez**. Tek bir ZIP üyesi bile dışarı çıkarılmaz
  veya açılıp okunmaz.
- Bütün çıktı yalnız sabit kategori etiketleri ve toplu sayılardır.
- `scannedBytes` taranan toplam ZIP kaynak baytıdır, kullanıcı dosya adı değildir.
- Arşiv final paketleri normal dava ZIP evrakıyla karıştırılmamalıdır.

## Sonraki üretim kapısı

Gerçek telefonlarda UYAP evrakı açılmadan önce güvenli cihaz kimliği/eşleştirme,
TLS, yetki iptali, kayıt kapsamı, şifreli orijinal saklama, tam HARX yedek
ve güvenlik/mahremiyet değerlendirmeleri gereklidir.
ZIP içindeki belgelerin güvenli kontrollü çözülmesi gelecekte ayrı test ve onay
kapısından geçecektir. DEBUG emülatöre gerçek UYAP evrakı aktarılmaz.

## Test kanıtı

`native/test_audit_archive_zip.py` yalnız sentetik belgeler içerir.
Windows ve Linux Actions adımları: `Local ZIP central directory safety audit (synthetic)`.
