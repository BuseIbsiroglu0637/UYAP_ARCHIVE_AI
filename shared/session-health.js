const SESSION_EXPIRED_PATTERN = /(?:oturum(?:unuz|unuzun|\s+süreniz)?\s+(?:sona\s+er(?:di|miştir)|sonlan(?:dı|mıştır)|kapat(?:ıldı|ılmıştır)|dol(?:du|muştur))|oturum\s+süresi\s+(?:dol(?:du|muştur)|sona\s+er(?:di|miştir))|yeniden\s+giriş\s+yap(?:ınız|manız)?|tekrar\s+giriş\s+yap(?:ınız|manız)?|giriş\s+yapmanız\s+gerekmektedir|giriş\s+ekranına\s+yönlendir|sistemden\s+çıkış\s+(?:yapıldı|yapılmıştır|gerçekleştirildi)|zaman\s+aşımı|session\s+(?:expired|timeout|ended))/i;
const LOGIN_TITLE_PATTERN = /(?:uyap.*giriş|giriş.*uyap|e-devlet.*giriş|güvenli\s+giriş)/i;
const LOGIN_URL_PATTERN = /(?:\/|[?&_=.-])(?:login|giris|oauth|sso)(?:\/|[?&#_.=-]|$)/i;

export function classifyHeartbeatResponse(response) {
  const evidence = [response?.title, response?.visibleText, response?.responseUrl]
    .filter(Boolean)
    .join(" ")
    .slice(0, 12_000);

  if (
    SESSION_EXPIRED_PATTERN.test(evidence) ||
    LOGIN_TITLE_PATTERN.test(String(response?.title || "")) ||
    LOGIN_URL_PATTERN.test(String(response?.responseUrl || ""))
  ) {
    return { healthy: false, loginRequired: true, reason: "LOGIN_REQUIRED" };
  }

  if (Number(response?.status) === 401) {
    return { healthy: false, loginRequired: true, reason: "LOGIN_REQUIRED" };
  }

  if (!response?.httpOk) {
    return { healthy: false, loginRequired: false, reason: "HEARTBEAT_HTTP_ERROR" };
  }

  return { healthy: true, loginRequired: false, reason: null };
}

export function heartbeatFailurePolicy(health, previousFailures, retryMs, intervalMs) {
  const failures = (previousFailures || 0) + 1;
  if (health?.loginRequired) {
    return { pause: true, failures, retryDelay: null };
  }

  return {
    pause: false,
    failures,
    retryDelay: failures === 1 ? retryMs : intervalMs
  };
}


export const AUTOMATIC_RECOVERY_LIMIT = 2;
export const RECOVERY_BUDGET_RESET_SUCCESSES = 10;

export function recoveryBudgetAfterVerified({ recoveryCount = 0, healthyVerifiedSinceRecovery = 0 } = {}) {
  const count = Math.max(0, Number(recoveryCount || 0));
  if (!count) return { recoveryCount: 0, healthyVerifiedSinceRecovery: 0, reset: false };
  const healthy = Math.max(0, Number(healthyVerifiedSinceRecovery || 0)) + 1;
  if (healthy >= RECOVERY_BUDGET_RESET_SUCCESSES) {
    return { recoveryCount: 0, healthyVerifiedSinceRecovery: 0, reset: true };
  }
  return { recoveryCount: count, healthyVerifiedSinceRecovery: healthy, reset: false };
}

export function canStartAutomaticRecovery(recoveryCount = 0) {
  return Math.max(0, Number(recoveryCount || 0)) < AUTOMATIC_RECOVERY_LIMIT;
}
