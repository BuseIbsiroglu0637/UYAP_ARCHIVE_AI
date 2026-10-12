# Phase 1 | Release Channels ve Dağıtım Kimliği

## Amaç

Release channel artık yalnız dokümantasyon etiketi değildir. Offline Windows paketleyici release kimliğini ve channel/environment eşleşmesini doğrudan makine-okunabilir config dosyalarından alır.

## Kanallar

| Kanal | Environment | Hedef | İmza politikası | Rollout |
| --- | --- | --- | --- | --- |
| `alpha` | `dev` | İç geliştirme | development build kabul | manual |
| `beta` | `stage` | Pilot saha | signed build gerekli | canary |
| `rc` | `prod` | Release candidate | signed build gerekli | canary |
| `stable` | `prod` | Production | signed build gerekli | staged |

## Packaging entegrasyonu

`native/build_offline_setup.ps1` artık:

1. `config/release-contract.json` dosyasını okur,
2. `config/release-channels.json` içinden aktif channel policy'yi bulur,
3. channel/environment uyuşmazlığında build'i durdurur,
4. paket klasörü, hedef EXE adı ve bootstrap dağıtım dizinini `releaseId` üzerinden üretir,
5. imza zorunlu kanallarda Phase 2 code-signing kapısının henüz kapanmadığını açık uyarı olarak gösterir.

Böylece RC15.9.37 / RC15.9.17 gibi hard-coded paketleme drift'leri kaldırılmıştır.

## Henüz uygulanmamış olanlar

Bu sözleşmenin varlığı aşağıdaki Phase 2 özelliklerinin tamamlandığı anlamına gelmez:

- otomatik updater,
- gerçek staged rollout backend,
- production code-signing pipeline,
- server-side channel promotion,
- otomatik rollback dağıtımı.

Bunlar `distributionCapabilities` altında bilerek `false` tutulur. Bu sayede config, ürünün mevcut kapasitesinden daha ileri bir hazır olma iddiasında bulunmaz.

## Promotion kapısı

`stable` dağıtımı için en az şu koşullar ayrıca kapanmalıdır:

- imzalı installer/agent,
- upgrade + rollback testi,
- Core 1.0 acceptance,
- P0/P1 = 0,
- privacy/security review.
