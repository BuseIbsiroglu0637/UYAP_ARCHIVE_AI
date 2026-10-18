# UYAP Archive AI — Beta1 internal validation

UYAP ve UYAP Kurum arşivlerini doğrulanabilir biçimde yerel olarak arşivleyen ve doğrulanmış evraklarla AI iş akışları sunmayı hedefleyen ürün.

## Beta1 test hattı

- **Branch:** `release/uyap-archive-ai-beta-1`
- **Sürüm adı:** `2.9.0 BETA1 P1P2 TEST ONLY`
- **Native Agent:** `2.7.68`
- **Kanal / ortam:** `beta / stage`
- **Ana ürün:** `main` dalındaki RC15.9.39, Beta1 çalışmalarından bağımsızdır.

## Beta1 değişiklikleri

- P1: AI ekranında sorgu ve kayıt geçmişi gösterimi performans iyileştirmeleri.
- P2: Eksik/kontrol bekleyen dosyalarda görünür AI devre dışı uyarısı ve eylem korumaları.
- Eksik dosyada AI sonuçlarının tam dava incelemesi gibi sunulmaması.

## GitHub Actions

- `beta1-internal-validation.yml`: AI uyarı regresyonları, Windows kurulum sözleşmeleri, dahili **imzasız** EXE artefaktı.
- `beta1-exe-install-e2e.yml`: GitHub'ın geçici Windows ortamında EXE çalıştırma, Native Messaging 2/2 ping, tanılama ve kaldırma.
- Otomatik testler gerçek UYAP oturumu, gerçek Chrome uzantısı kullanım senaryosu ve başka bir son kullanıcı bilgisayarında kurulum kabulünün yerine **geçmez**.

## Dağıtım kısıtları

**INTERNAL TEST ONLY — müşteriye sunulmaz.**

- Kurulum EXE imzasızdır; Beta/Stage yayın sözleşmesi imza zorunluluğunu korur.
- Chrome uzantısının kullanıcıya kurulumu ve Native Messaging entegrasyonu için gerçek tarayıcı kabulü gerekir.
- Normal UYAP ve Kurum UYAP için ayrı uçtan uca canlı kabul gereklidir.
- Eksik dosyada AI kapalı, tam dosyada kaynaklı AI çalışır kabul senaryoları tarayıcıda doğrulanmalıdır.
- Güncelleme, rollback, arşiv bütünlüğü, KVKK ve gizlilik gereksinimleri yayın öncesi kapanmalıdır.
- Gerçek UYAP dava dosyaları veya gizli kayıtlar CI test artefaktlarına yüklenmez.

## İlk yayından önce

1. GitHub Actions son commit için yeşil olmalı.
2. Gerçek Chrome uzantısı + Native Agent bağlantısı ve AI geçiş davranışı kabul edilmeli.
3. İmzalı kurulum ve tutarlı uzantı dağıtım yöntemi hazırlanmalı.
4. Ayrı Windows cihazı, destek ve güncelleme senaryoları onaylanmalı.
