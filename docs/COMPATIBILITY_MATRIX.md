# RC15.9.38 sürüm uyumluluk matrisi

Bu matris, kaynak paketinde görülen **beklenen** sürüm sözleşmelerini kaydeder. Gerçek Windows kurulum kabulü veya canlı UYAP testi yerine geçmez.

| Bileşen | Paketle gelen değer | Kaynak |
| --- | --- | --- |
| Chrome extension | `2.9.0` | `manifest.json: version` |
| Release kimliği | `RC15.9.38 ARCHIVE-SYNC-RACE-FIX` | `manifest.json: version_name` |
| Manifest | MV3 | `manifest.json: manifest_version` |
| Minimum Chrome | `120` | `manifest.json: minimum_chrome_version` |
| Native Agent | `2.7.64` | `native/uyap_archive_agent.py: AGENT_VERSION` |
| Native protokolü | `2` | `native/uyap_archive_agent.py: PROTOCOL_VERSION` |
| Kurulum hedef sürümü | `2.7.64` | `native/install_host.py: VERSION` |
| Yeniden kullanılabilir ajan alt sınırı | `2.7.64` | `native/install_host.py: MIN_REUSABLE_VERSION` |
| Extension ajan alt sınırı | `2.7.64` | `background/native-agent.js: MIN_AGENT_VERSION` |

## Kabul sınırı

- `native/current_release_identity_test.py`, bu değerleri tek noktadan denetler.
- Geçmiş RC15.9 regresyon testleri belirli eski RC etiketleri yerine `2.9.0 RC15.9.` sürüm ailesini arar.
- Temiz Windows 10/11 kurulumu, Chrome/Edge native messaging kayıtları, ajan ping testi, yükseltme/rollback ve canlı UYAP + UYAP Kurum kabulü **henüz burada doğrulanmış değildir**.
- UYAP doğrulama, SHA-256 ve kaynak eşleştirme çekirdeği AI veya lisans özelliği eklenirken değiştirilmeyecektir; gerekli bir değişiklik ayrıca regresyon ve canary gerektirir.
