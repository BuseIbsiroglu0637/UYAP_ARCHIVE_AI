# S2-04 | Büyük Yerel Arşiv Görünürlüğü — Kayıtlar / Evraklar

**Tarih:** 15.10.2026  
**Durum:** FEATURE BRANCH / CI / SAHA KABULÜ BEKLİYOR.  
**Kapsam:** RC15.9.38 uzantı + Agent 2.7.68 teknik akışı. Portal otomasyonu veya arşiv verileri değiştirilmez.

## Kullanıcı testinden kaynaklanan hata

- Yerel fiziksel arşiv klasörü doğru açılıyor, `_archive.db` SQLite dosyası salt-okunur açılabiliyor.
- Kullanıcının dosyasında `documents` tablosunda **12.784** kayıt: **12.743 VERIFIED**, **41 INVALID_CONTENT**; gereken sütunlar mevcut; `local_path` 12.784 kayıtta dolu.
- AI bölümü tek bir dava için **520/520 fiziksel hazır** evrak gösterirken Dosyalar/Evraklar sayfaları **0 kayıt** gösterdi.
- Kaynak kodda `MAX_RESPONSE_BYTES = 1024 * 1024` ve Workspace'in `GET_ARCHIVE_REGISTRY(limit:20000)` çağrısı tespit edildi. 12.784 satırlık JSON yanıtının bu sınırı aşması **güçlü olası neden**, ancak geçmiş canlı bağlantı tanı JSON'u alınmadığından tek doğrulanmış neden olarak kayda geçilmez.
- Registry hataları Workspace'te daha önce `.catch(()=>null)` ile gizleniyordu.

## Yapılan teknik düzenleme (ayrı dal)

1. **Native Agent:** `_archive_page_filter` ile `summaryOnly`, `offset`, `limit<=100`, `search`, `status`, `extension`, `institutionName`, `caseId` desteklendi; arşiv özetinde `caseIndex[:100]` gönderilir, `caseIndexTruncated` işareti vardır. `matchedFiles`, `hasMore`, `pageOffset`, `pageLimit` döner.
2. **Service worker:** Sayfalama/filtre parametrelerini Native Agent'a iletir.
3. **Download manager:** İşlem/pagina anahtarlı kısa süreli cache; kalıcı 1 MB aşımını önlemek için normal sonuçları 100 belgeyle sınırlar. Arşiv seçme işlemi yalnız özet döndürür.
4. **Workspace:** Dosyalar için dava özeti, Evraklar için 100'lük gerçek sayfa ve sunucu tarafı arama/durum/tür filtresi. Hata halinde `0 kayıt` diye sessiz gizlemek yerine açık tanı bildirimi; eski dava satırlarını yanlış göstermekten kaçınır.
5. **Veri koruması:** `list_archive_registry` var olan salt-okunur SQLite sorgusunu kullanır; `_archive.db`, WAL/SHM ve fiziksel evrakları onarmaz/silmez/taşımaz.

## Testler ve dürüst sınırlamalar

- Yeni `native/s2_04_paged_archive_regression_test.py`: sentetik 12.784 kayıtla 100 satırlık sayfalar, offset, case seçim, status, tür/arama, 1 MiB yanıt bütçesi; geçersiz sayfa parametresi reddi.
- Eski `rc15_9_35_workspace_live_sync_regression_test.py` live sync ve cache davranışı için korunur.
- Linux `Offline source and regression smoke` ve Windows `Windows installer lifecycle smoke` testleri her güncel commit için ayrıca doğrulanmalıdır.
- **Henüz gerçek müşteri cihazına kurulmadı:** GitHub CI sonucu, son kullanıcının OneDrive içindeki 12.784 kaydının tamamının fiziksel olarak doğru olduğunu veya arayüzün sahada hatasız açıldığını **kanıtlamaz**.
- `caseIndex` ilk 100 dava özeti ile sınırlı; yüzlerce ayrı dava dosyası olan arşivlerde dava özeti için ayrıca indeks sayfalaması sonraki kabul işi.
- Dahili Native Agent hâlâ büyük arşivi diskten tarayıp özet çıkarıyor; bu iyileştirme **yanıt boyutunu** sınırlar, tarama maliyetini ve sonraki performans optimizasyonunu ortadan kaldırmaz.
- Öncelikli saha kabulü: 12.784 kayıtta (sentetik) ilk dava listesi, farklı dava seçimi, sayfalar 1/2, arama, durum, hata görünürlüğü; ardından gerçek özel arşivde read-only ekran gözlemi.

## Kullanıcıya güvenli geçiş

Yeni branch ayrı tutulur. Önce source regression:
```powershell
git fetch origin refs/heads/fix/s2-04-paged-archive-registry
git worktree add --detach ..\UYAP_ARCHIVE_AI_REGISTRY_FIX_TEST FETCH_HEAD
cd ..\UYAP_ARCHIVE_AI_REGISTRY_FIX_TEST
py -3 native\s2_04_paged_archive_regression_test.py
node --check workspace\workspace.js
node --check background\download-manager.js
```

Ardından kurulu uzantı ve Native Agent'ı değiştiren `install_host.py` / `chrome://extensions` adımları **ayrı ve kullanıcı onaylı** saha testine bırakılır. Önceden kullanıcının fiziksel arşivinde veri kaybı olmadığını karşılaştıran hash manifesti / yedeği korunmalıdır.

## İlişkili işler

- Sprint 1 S1-06 Beta Testi: `docs/productization/S1_BETA_ACCEPTANCE.md` (docs-only PR #16).
- Geliştirme PR #10: `feature/ai-six-workflows`.
- Sprint 2 S2-01 beta safety profile ayrı PR #17; bu dal onu birleştirmez.
