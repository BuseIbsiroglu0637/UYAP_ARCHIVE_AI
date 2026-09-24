// UYAP Archive AI - Account service configuration
// Supabase projesi oluşturulduğunda yalnız publishable key buraya yazılır.
// Secret/service_role anahtarlarını ASLA uzantıya eklemeyin.
globalThis.UYAP_ACCOUNT_CONFIG = Object.freeze({
  enabled: false,
  supabaseUrl: "",
  publishableKey: "",
  apiBaseUrl: "",
  offlineGraceDays: 7
});
