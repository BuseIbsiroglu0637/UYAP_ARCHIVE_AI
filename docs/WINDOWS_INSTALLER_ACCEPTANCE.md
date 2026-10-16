# Phase 1 | Windows Installer Acceptance Matrix

## Amaç

Native Agent yaşam döngüsünü iki seviyede doğrulamak:

1. **CI lifecycle smoke:** kaynak, registry contract, current/rollback layout ve PowerShell syntax regressions.
2. **Gerçek Windows acceptance:** imzalı paketle temiz makine, upgrade ve rollback davranışı.

CI sonucu tek başına satışa hazır installer kabulü değildir.

## Otomatik Windows CI

`.github/workflows/windows-installer-smoke.yml` şu kontrolleri Windows runner üzerinde yapar:

- RC15.9.38 / Native Agent 2.7.68 release identity,
- Chrome + Edge Native Messaging registry contract,
- `current / rollback` lifecycle ve kullanıcı verisini silmeme regresyonu,
- portable installer sözleşmesi,
- release-channel packaging sözleşmesi,
- installer PowerShell dosyalarının parser/syntax kontrolü.

## Gerçek acceptance matrisi

| Senaryo | Asgari kanıt | Durum |
| --- | --- | --- |
| Temiz Windows 10 | İmzalı setup, Chrome, 2/2 ping, HKCU registry, archive verisine dokunmama | Bekliyor |
| Temiz Windows 11 | İmzalı setup, Chrome + Edge, 2/2 ping, health check | Bekliyor |
| Aynı sürüm repair | Mevcut current ajanı doğrula, registry onar, archive verisini koru | Bekliyor |
| N-1 -> N upgrade | Eski sağlam ajan rollback'e taşınır, yeni current doğrulanır | Sonraki distinct signed agent build'i bekliyor |
| N -> N-1 rollback | current/rollback swap + registry + 2/2 ping | Sonraki distinct signed agent build'i bekliyor |
| Hatalı upgrade | Yeni current doğrulanmazsa eski current + registry geri yüklenir | Bekliyor |
| Uninstall | Yalnız ajan/registry temizlenir; UYAP arşivi ve browser DB korunur | Bekliyor |

## Acceptance kayıtları

Her gerçek testte şu kanıtlar saklanmalı:

- Windows sürümü ve mimari,
- Chrome/Edge sürümü,
- setup/release kimliği,
- Native Agent sürümü,
- `native-install-diagnostic.json`,
- Authenticode status/subject,
- current/rollback ajan sürümleri,
- Chrome/Edge registry doğrulaması,
- 2/2 Native Messaging ping,
- arşiv dizini öncesi/sonrası dosya sayısı ve SHA-256 örnek kontrolü,
- test sonucu ve varsa hata kodu.

Ham hukuk evrakı veya dava adı acceptance raporuna alınmaz.

## Çıkış kapısı

Phase 1 installer acceptance ancak:

- Windows 10 ve 11 clean-install PASS,
- Chrome ve Edge host PASS,
- gerçek N-1 -> N upgrade PASS,
- gerçek rollback PASS,
- failed-upgrade restoration PASS,
- archive data touched = false

kanıtlarıyla kapatılır.
