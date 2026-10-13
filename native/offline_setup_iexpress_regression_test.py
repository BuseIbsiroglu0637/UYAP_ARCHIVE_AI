from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "native" / "build_offline_setup.ps1").read_text(encoding="utf-8")

def req(condition, message):
    if not condition:
        raise AssertionError(message)

req("$LASTEXITCODE" not in SCRIPT, "build script must not rely on unset LASTEXITCODE")
req("$sedArg" not in SCRIPT, "manual quoted SED argument must not return")
req("-WorkingDirectory $Work" in SCRIPT, "IExpress must resolve the SED from its working directory")
req("@('/N', '/Q', $sedLeaf)" in SCRIPT, "IExpress must receive simple /N /Q + leaf SED arguments")
req("Split-Path -Leaf $sed" in SCRIPT, "SED leaf name must be used")
req("Test-Path $sed" in SCRIPT, "SED existence must be checked before IExpress launch")
req("Test-Path $target" in SCRIPT, "final setup target must be verified")
req("IExpress basarili sonlandi ancak Setup.exe hedefte bulunamadi." in SCRIPT, "successful process exit must not be treated as sufficient proof")

print("OFFLINE SETUP IEXPRESS REGRESSION: PASS")
