#!/usr/bin/env python3
"""K5.10: Headless Chromium MV3 UI + native-messaging browser smoke.

Uses only GitHub-hosted disposable Windows runner and empty user profile.
No UYAP websites, real user files, account login or downloads.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

EXTENSION_ID = "fppnhbkemmbocmicplkfidcbggeininh"
VERSION_NAME = "2.9.0 BETA1 P1P2 TEST ONLY"
AGENT_VERSION = "2.7.68"


def require(ok, message):
    if not ok:
        raise AssertionError(message)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extension-dir", type=Path, required=True)
    ap.add_argument("--profile-dir", type=Path, required=True)
    args = ap.parse_args()
    root = args.extension_dir.resolve(strict=True)
    profile = args.profile_dir.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8-sig"))
    require(manifest["version_name"] == VERSION_NAME, "Unexpected installed version")
    require("nativeMessaging" in manifest["permissions"], "Native messaging not enabled")
    require(not profile.exists() or not any(profile.iterdir()), "Browser profile must be new")
    t0 = time.monotonic()
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile),
            channel="chromium",
            headless=True,
            args=[
                f"--disable-extensions-except={root}",
                f"--load-extension={root}",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            timeout=90_000,
        )
        try:
            workers = context.service_workers
            if not workers:
                try:
                    context.wait_for_event("serviceworker", timeout=30_000)
                except Exception:
                    pass
                workers = context.service_workers
            require(bool(workers), "MV3 background service worker did not start")
            urls = [w.url for w in workers]
            require(any(u.startswith(f"chrome-extension://{EXTENSION_ID}/") for u in urls),
                    "Unexpected extension ID or missing registered service worker: " + repr(urls))
            print("K5_10_CHROME_MV3_EXTENSION=PASS", flush=True)

            page = context.new_page()
            page.set_default_timeout(30_000)
            page.goto(f"chrome-extension://{EXTENSION_ID}/workspace/index.html?view=ai",
                      wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_function(
                "() => !!document.querySelector('#view-ai')?.classList.contains('active')",
                timeout=40_000)
            version = page.locator("#sideVersion").inner_text()
            require(VERSION_NAME in version, "Workspace version label mismatch: " + version)
            require(page.locator("#aiIncompleteWarning").count() == 1,
                    "Incomplete-case AI warning element missing")
            require(page.locator("#aiSend").is_disabled(),
                    "AI must be disabled with no verified selected case")
            require(page.locator("#mainNav").count() == 1, "Workspace main navigation missing")
            require(
                page.evaluate("() => chrome.runtime.getManifest().version_name") == VERSION_NAME,
                "Chrome running unexpected manifest")
            print("K5_10_CHROME_WORKSPACE_AND_AI_GUARD=PASS", flush=True)

            # Send from a *real extension page* through the actual background worker.
            # This is not a mocked ping and not a direct Python subprocess self-test.
            for attempt in range(2):
                response = page.evaluate("""async () => {
                  return await Promise.race([
                    chrome.runtime.sendMessage({type: 'ping_agent'}),
                    new Promise((_, reject) =>
                      setTimeout(() => reject(new Error('browser-native ping timeout')), 25000))
                  ]);
                }""")
                require(isinstance(response, dict) and response.get("ok") is True,
                        "Extension runtime request failed: " + repr(response)[:500])
                data = response.get("data") or {}
                require(isinstance(data, dict) and data.get("ok") is True,
                        "Browser Native Messaging ping failed: " + repr(data)[:500])
                require(data.get("version") == AGENT_VERSION,
                        "Native Agent version mismatch: " + repr(data.get("version")))
                require(data.get("protocolVersion") == 2, "Native Agent protocol mismatch")
                require(data.get("downloadReady") is True, "Native Agent capabilities incomplete")
                print(f"K5_10_REAL_BROWSER_NATIVE_PING_{attempt + 1}=PASS", flush=True)

            print("K5_10_CHROME_TO_NATIVE=PASS elapsed_s=" +
                  f"{time.monotonic() - t0:.2f}", flush=True)
        finally:
            context.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("K5_10_FAIL: " + str(e)[:1200], file=sys.stderr, flush=True)
        raise
