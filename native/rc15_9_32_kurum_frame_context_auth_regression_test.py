from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
WORKER = (ROOT / "background" / "service-worker.js").read_text(encoding="utf-8")
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def req(condition, message):
    if not condition:
        raise AssertionError(message)


start = WORKER.index("function trustedKurumContextSender")
end = WORKER.index("async function testProductNotification", start)
guard = WORKER[start:end]

req("sender.id !== chrome.runtime.id" in guard, "own-extension sender identity is not enforced")
req("sender.tab?.url" in guard and "sender.origin" in guard and "sender.url" in guard,
    "tab and frame origins are not jointly verified")
req("url.protocol === 'https:'" in guard and "kurum.uyap.gov.tr" in guard,
    "Kurum HTTPS origin restriction missing")
req("return tabTrusted && frameTrusted" in guard,
    "both enclosing tab and frame proof must be required")
req("sender.frameId !== 0" not in WORKER,
    "valid Kurum child frames are still rejected")
req("if (!trustedKurumContextSender(sender)) throw new Error('KURUM_CONTEXT_NOT_AUTHORIZED')" in WORKER,
    "Kurum context message does not use the hardened frame-aware guard")
req("2.9.0 RC15.9." in MANIFEST.get("version_name", ""),
    "release identity mismatch")

print("RC15.9.32 Kurum frame context auth regression: PASS")
