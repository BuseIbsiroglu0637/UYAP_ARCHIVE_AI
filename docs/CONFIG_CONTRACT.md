# Phase 1 | Sürüm ve Config Sözleşmesi

## Amaç

Bu sözleşme, Archive Engine davranışına dokunmadan ürün bileşenlerinin hangi sürüm ve çalışma ortamı kurallarına göre birlikte çalışacağını sabitler.

Makine tarafından okunabilen iki kaynak vardır:

- `config/release-contract.json`: sürüm, protokol, Native Agent ve DB uyumluluk sözleşmesi.
- `config/environments.json`: `dev`, `stage`, `prod` ortam politikaları.

## Sürüm sözleşmesi

`release-contract.json` ürünün tek **beklenen sözleşme kaydıdır**. Çalışan bileşenlerde sürüm değerleri bugün hâlâ kendi doğal dosyalarında bulunur (`manifest.json`, Python ajanı, installer ve JS bridge). CI bunların sözleşmeyle aynı olduğunu doğrular.

Bu yaklaşım bilerek seçildi: Native Agent'ın paketlenmiş EXE davranışını veya Chrome MV3 yükleme yolunu sırf merkezileştirme uğruna değiştirmiyoruz. Phase 1'de önce drift'i engelliyoruz; build-time üretim daha sonra güvenli bir POC ile değerlendirilebilir.

## Environment sözleşmesi

Ortamlar çalışma verisinin nereye gideceğini değil, hangi politika setinin geçerli olduğunu tanımlar.

- `dev`: yerel geliştirme, alpha/beta/RC; cloud varsayılan kapalı.
- `stage`: beta/RC kabul; imzalı build beklenir; cloud varsayılan kapalı.
- `prod`: RC/stable; imzalı build zorunlu; local-first ve privacy-light diagnostics varsayılanı.

Her ortamda:

- Ham hukuk evrakının cloud'a varsayılan çıkışı **yasaktır**.
- AI varsayılan olarak kapalıdır ve yalnız doğrulanmış (`VERIFIED`) içerikten beslenecektir.
- Tanılama ham dava/evrak içeriğini varsayılan olarak içermez.

## Config kaynak önceliği

1. Güvenli ürün varsayılanları.
2. Dağıtım sırasında verilen, secret içermeyen deployment değerleri.
3. Kullanıcıya ait yerel tercihler ve oturum ayarları.

Uzantıya `service_role`, private key veya benzeri secret gömülmez. Supabase kullanılırsa yalnız publishable key kabul edilir; mevcut `account/account-config.js` bu geçiş döneminde compatibility adapter olarak kalır.

## Değişiklik kuralı

Aşağıdaki değişiklikler release contract güncellemesi ve CI doğrulaması olmadan merge edilmez:

- extension version / version_name,
- Native Agent version veya minimum version,
- Native Messaging protocol version,
- IndexedDB version,
- reliability schema version,
- minimum Chrome version,
- release environment / channel.
