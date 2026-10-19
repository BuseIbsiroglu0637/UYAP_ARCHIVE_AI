#!/usr/bin/env python3
"""Build and validate an internal-only Chrome MV3 ZIP from a clean Git export.

This intentionally does NOT test a running browser, a live UYAP session, or the
Native Messaging handshake. No personal archives, host installer, or secrets
belong in the extension-only artifact.
"""
import argparse
import base64
import hashlib
import json
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

EXPECTED_ID = "fppnhbkemmbocmicplkfidcbggeininh"
HOST_NAME = "tr.gov.uyap.archive_agent"
EXTENSION_DIRS = ("background", "content", "popup", "workspace", "shared", "icons")
FORBIDDEN_EXTENSIONS = {".db", ".sqlite", ".sqlite3", ".ps1", ".bat", ".exe", ".pem", ".key", ".pfx", ".patch"}
FORBIDDEN_NAMES = {".env", "secrets.json", "credentials.json", "_archive.db"}
MAX_TOTAL_SIZE = 40 * 1024 * 1024

class HtmlAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag in ("script", "img") and data.get("src"):
            self.resources.append(data["src"])
        if tag == "link" and data.get("rel", "").lower() in ("stylesheet", "icon") and data.get("href"):
            self.resources.append(data["href"])

def require(condition, message):
    if not condition:
        raise AssertionError(message)

def extension_id(key):
    decoded = base64.b64decode(key, validate=True)
    digest = hashlib.sha256(decoded).hexdigest()[:32]
    return "".join(chr(ord("a") + int(x, 16)) for x in digest)

def validate_rel_path(name):
    posix = PurePosixPath(name.replace("\\", "/"))
    require(bool(name) and not posix.is_absolute() and ".." not in posix.parts,
            f"Unsafe ZIP member or URL path: {name}")
    require(not any(part.startswith(".") for part in posix.parts),
            f"Hidden file in package: {name}")
    require(not posix.suffix.lower() in FORBIDDEN_EXTENSIONS, f"Forbidden file type: {name}")
    require(posix.name.lower() not in FORBIDDEN_NAMES, f"Forbidden file: {name}")
    return posix.as_posix()

def file_list(source):
    result = [source / "manifest.json"]
    for directory in EXTENSION_DIRS:
        folder = source / directory
        require(folder.is_dir(), f"Missing Chrome source directory: {directory}")
        result.extend(sorted(p for p in folder.rglob("*") if p.is_file()))
    seen = set()
    clean = []
    for file in result:
        require(not file.is_symlink(), f"Symlink refused: {file}")
        rel = file.relative_to(source).as_posix()
        validate_rel_path(rel)
        require(rel.lower() not in seen, f"Duplicate case-insensitive path: {rel}")
        seen.add(rel.lower())
        clean.append((rel, file))
    require(sum(f.stat().st_size for _, f in clean) < MAX_TOTAL_SIZE, "Chrome extension exceeds size limit")
    return sorted(clean)

def verify_sources(source, paths):
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8-sig"))
    contract = json.loads((source / "config/release-contract.json").read_text(encoding="utf-8-sig"))
    require(manifest["manifest_version"] == 3, "MV3 required")
    require(manifest["version_name"] == contract["release"]["versionName"], "Beta1 version display mismatch")
    require(contract["release"]["releaseId"] == "BETA1", "Not Beta1")
    require(contract["release"]["channel"] == "beta" and contract["release"]["environment"] == "stage",
            "Must remain beta/stage")
    require(manifest["version"] == contract["release"]["extensionVersion"], "Extension numeric version mismatch")
    require("nativeMessaging" in manifest["permissions"], "Native Messaging permission absent")
    require(extension_id(manifest["key"]) == EXPECTED_ID, "Chrome extension ID changed")
    native = (source / "native/portable_installer.ps1").read_text(encoding="utf-8-sig")
    require(f"$CurrentExtensionId = '{EXPECTED_ID}'" in native, "Native Agent permitted origin drift")
    require(f"$HostName = '{HOST_NAME}'" in native, "Native host name mismatch")
    require('"chrome-extension://$CurrentExtensionId/"' in native, "Native Agent current origin missing")

    required = {"manifest.json"}
    def add_resource(path):
        if not path or path.startswith(("http:", "https:", "data:", "chrome:")):
            return
        require(not path.startswith("/"), f"Absolute resource URL unexpected: {path}")
        target = validate_rel_path(path)
        required.add(target)

    bg = manifest["background"]["service_worker"]
    add_resource(bg)
    add_resource(manifest["action"]["default_popup"])
    for file in manifest.get("icons", {}).values():
        add_resource(file)
    for file in manifest.get("action", {}).get("default_icon", {}).values():
        add_resource(file)
    for script in manifest.get("content_scripts", []):
        for name in script.get("js", []) + script.get("css", []):
            add_resource(name)

    for file in paths:
        if file.endswith(".html"):
            parser = HtmlAssets()
            parser.feed((source / file).read_text(encoding="utf-8-sig"))
            for raw in parser.resources:
                if raw.startswith(("http:", "https:", "data:", "chrome:", "#")):
                    continue
                joined = (PurePosixPath(file).parent / raw.split("?", 1)[0]).as_posix()
                import posixpath
                add_resource(posixpath.normpath(joined))
        if file.endswith((".js", ".mjs")):
            js = (source / file).read_text(encoding="utf-8-sig")
            # Only static relative ECMAScript imports: dynamic expressions are out of scope.
            for token in re.findall(r'\b(?:from\s*|import\s*\()\s*["\x27](\.[^"\x27]+)["\x27]', js):
                import posixpath
                candidate = posixpath.normpath(posixpath.join(posixpath.dirname(file), token))
                if "." not in PurePosixPath(candidate).name:
                    candidate += ".js"
                add_resource(candidate)
    missing = sorted(required - set(paths))
    require(not missing, "Missing referenced Chrome assets: " + ", ".join(missing[:30]))
    print(f"CHROME_SOURCE_AND_NATIVE_ORIGIN=PASS id={EXPECTED_ID} files={len(paths)}")

def verify_zip(zip_name, expected):
    with zipfile.ZipFile(zip_name) as z:
        names = z.namelist()
        require(len(names) == len(set(names)) and len(names) == len(expected),
                "Archive member list differs from expected sources")
        require(set(names) == set(expected), "Archive missing or extra paths")
        require("manifest.json" in names, "Manifest not at ZIP root")
        for name in names:
            validate_rel_path(name)
            require(name == "manifest.json" or name.split("/")[0] in EXTENSION_DIRS,
                    f"Unexpected ZIP directory: {name}")
            require(not z.getinfo(name).is_dir(), f"Directory member not expected: {name}")
            require(z.getinfo(name).file_size < MAX_TOTAL_SIZE, f"Overlarge ZIP member: {name}")
        require(z.testzip() is None, "ZIP CRC validation failed")
    print("CHROME_PACKAGE_ZIP_CONTRACT=PASS")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--build", type=Path, required=True)
    args = ap.parse_args()
    source = args.source.resolve(strict=True)
    files = file_list(source)
    verify_sources(source, [p for p, _ in files])
    args.build.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.build, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, absolute in files:
            z.write(absolute, arcname=name)
    verify_zip(args.build, [p for p, _ in files])
    print("CHROME_ZIP_SHA256=" + hashlib.sha256(args.build.read_bytes()).hexdigest().upper())

if __name__ == "__main__":
    try:
        main()
    except (AssertionError, OSError, ValueError, KeyError) as exc:
        print(f"CHROME_PACKAGE_FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
