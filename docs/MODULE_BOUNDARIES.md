# Phase 1 | Modül Sınırları

## Temel kural

Archive Engine çekirdeği AI, üyelik ve cloud servislerinden bağımsız kalır. Portal bağlayıcıları, arşiv motoru ve kullanıcı arayüzü aynı kod tabanında bulunabilir; ancak sorumluluklar birbirine doğrudan sızmamalıdır.

## Katmanlar

| Dizin | Sorumluluk | İzin verilen doğrudan bağımlılıklar |
| --- | --- | --- |
| `shared/` | Saf politika, ortak sabit ve yardımcılar | `shared/` |
| `content/` | UYAP/Kurum sayfa bağlamı ve kaynak adaptörleri | `content/`, `shared/` |
| `background/` | Queue, correlation, doğrulama orkestrasyonu, browser/native köprüsü | `background/`, `shared/` |
| `native/` | Yerel dosya doğrulama, hash, archive, manifest, installer | Python stdlib / build bağımlılıkları; browser JS import etmez |
| `popup/` | Hafif kontrol yüzeyi | `popup/`, `shared/`, mesaj sözleşmesi |
| `workspace/` | Arşiv çalışma alanı UI | `workspace/`, `shared/`, `account/`, mesaj sözleşmesi |
| `account/` | Lisans/hesap istemci adaptörü | `account/`, `shared/` |
| `backend/` | Control Plane / Supabase şema ve backend artefact'ları | Browser runtime tarafından doğrudan import edilmez |
| `config/` | Release/environment sözleşmeleri | Runtime secret içermez |

## Yasak sınırlar

- `shared/` hiçbir üst katmanı import etmez.
- `content/`, `popup/`, `workspace/` veya `account/` doğrudan `background/` modülü import etmez; Chrome message sözleşmesini kullanır.
- `background/` UI katmanlarını import etmez.
- `native/` UYAP ağına bağlanmaz; yerel dosya ve Native Messaging işlemleriyle sınırlı kalır.
- `backend/` kodu indirme/correlation/validation kararının zorunlu çalışma zamanı bağımlılığı olamaz.
- AI veya hesap/lisans özelliği, VERIFIED kararını veya fiziksel hash kanıtını değiştiremez.

## CI koruması

`native/phase1_product_contract_test.py` statik ES module importlarını kontrol eder ve izin verilmeyen katman geçişlerinde build'i kırar. Aynı test environment sözleşmesinin local-first/privacy varsayılanlarını da doğrular.
