# UYAP Archive AI / Supabase

M1 Account Foundation backend hazırlığı.

1. UYAP Archive AI için ayrı bir Supabase projesi oluşturun.
2. `migrations/001_account_foundation.sql` migration'ını uygulayın.
3. Authentication > Providers altında Email'i etkin bırakın; Google sonraki adımda eklenebilir.
4. Project URL ve **publishable** key değerini `account/account-config.js` içine girin ve `enabled: true` yapın.
5. Secret/service_role key'i uzantıya eklemeyin.

Ödeme ve AI secret'ları ileride yalnız server/Edge Function ortamında tutulacaktır.
