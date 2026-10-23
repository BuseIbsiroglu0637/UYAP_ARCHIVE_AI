# UYAP Archive AI — v0.12 yerel gerçek arşiv hazırlık denetimi

**Amaç:** Gerçek UYAP dava evraklarının kişisel verilerini dışarı çıkarmadan,
zaten indirilmiş yerel arşivin teknik tutarlılığını ölçmek.
Bu özellik UYAP API'si, oturum otomasyonu, mobil veri aktarımı veya imza doğrulaması **değildir**.

## Çalışan v0.11 korunur

- Dal: feature/v0.12-real-archive-readiness
- Daha önceki v0.11 canlı yerel köprü ve Android test sürümleri değişmez.
- Buradaki CLI yalnız okur: evrak, veritabanı ve Chrome profili üzerinde yazma yoktur.

## Çalıştırma — Windows PowerShell

Var olan repoyu değiştirmeden bu dalı ayrı worktree'de kullan:

```powershell
cd 'C:\Users\busei\Documents\GitHub\UYAP_ARCHIVE_AI'
git fetch origin refs/heads/feature/v0.12-real-archive-readiness:refs/remotes/origin/feature/v0.12-real-archive-readiness
git worktree add '..\UYAP_ARCHIVE_AI-v012' origin/feature/v0.12-real-archive-readiness
cd '..\UYAP_ARCHIVE_AI-v012'
python .\native\audit_real_archive.py --selected-archive
```

UYAP Archive AI'de daha önce kaydedilmiş arşiv seçimi yoksa kullanıcı
masaüstünde belirli yolu girerek çalıştırabilir:

```powershell
python .\native\audit_real_archive.py --archive-root 'C:\...\UYAP_Arsiv'
```

## Çıktı

- `records`: DB'deki toplam kayıt
- `verifiedInDatabase`: veritabanında VERIFIED işaretli kayıt
- `notVerified`: diğer statüler
- `internalExcluded`: final paket ve uygulama yönetim dosyaları
- `unsupportedExcluded`: henüz desteklenmeyen ZIP/HTML gibi türler
- `invalidMetadata`: geçersiz boyut/hash/kimlik
- `missingOrUnsafe`: yerelde olmayan, arşiv dışındaki veya güvensiz yol
- `integrityFailed`: dosya mevcut ama SHA veya biçim doğrulanamadı
- `provenOriginals`: fiziksel byte + SHA kontrolünü geçen orijinal kaynaklar
- `provenPDF/UDF/TIF`: biçim bazında başarılı orijinaller

Hiçbir belge adı, tam dosya yolu, dava numarası, şahıs adı, evrak hash'i
veya belge içeriği raporda yer almaz. Yalnız bu özetin sayılarını paylaşın.

## Filtreleme

`archive_document_policy.py` final paket dosyalarını belge envanterine
karıştırmamayı amaçlar: `_manifest.json`, `_verification.csv`,
`_checksums.sha256`, `archive-part-001.zip`, `Final_Paket_*`, `_final` vb.
Buna karşılık gerçek UYAP evrak ekleri arasında ZIP bulunabilir:
bu sürümde ZIP dosyası **desteklenmeyen tür** olarak raporlanır;
sonraki sürümlerde güvenli kapsama alınmalıdır, doğrudan UDF diye işlenmez.

## Güvenlik

- DEBUG canlı köprü yalnız `create_mobile_live_demo.py` ile üretilen
  sentetik fixture işaretli arşive bağlanabilir. İşaret eklenmesi tek
  başına kriptografik güvence sağlamaz; yalnız yanlış kullanım freni.
- Bu raporun PASS olması gerçek mobil üretim sürümüne onay vermez.
- Üretim için HTTPS/TLS, kimlik ve cihaz eşleştirme, izin ve kapsam sınırlaması,
  anahtar iptali, yerel şifreleme/yedekleme, KVKK, dağıtım ve kalıcı kayıtlar
  için ayrı kabul gereklidir.
- `VERIFIED` + SHA, UYAP elektronik imza geçerliliği veya dosyanın
  hukukî doğruluğu ile aynı şey değildir.

## Test

Windows ve Linux GitHub Actions: `test_archive_readiness.py`.
Test verileri yapaydır; gerçek arşiv hiçbir CI sistemine yüklenmemiştir.
