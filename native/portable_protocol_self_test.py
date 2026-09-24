from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path


def send(proc, payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    proc.stdin.write(struct.pack("=I", len(raw)))
    proc.stdin.write(raw)
    proc.stdin.flush()
    header = proc.stdout.read(4)
    if len(header) != 4:
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        raise RuntimeError(f"Host yanit basligi alinmadi. stderr={err[:500]}")
    size = struct.unpack("=I", header)[0]
    if size <= 0 or size > 1024 * 1024:
        raise RuntimeError(f"Gecersiz Native Messaging yanit boyutu: {size}")
    body = proc.stdout.read(size)
    if len(body) != size:
        raise RuntimeError(f"Eksik Native Messaging yaniti: {len(body)}/{size}")
    return json.loads(body.decode("utf-8"))


def main():
    if len(sys.argv) < 3:
        raise SystemExit("Kullanim: portable_protocol_self_test.py <python.exe> <origin>")
    exe = str(Path(sys.argv[1]).resolve())
    origin = sys.argv[2]
    proc = subprocess.Popen(
        [exe, origin, "--parent-window=0"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    try:
        r1 = send(proc, {"action": "ping", "probe": "portable-1"})
        r2 = send(proc, {"action": "ping", "probe": "portable-2"})
        for idx, r in enumerate((r1, r2), 1):
            if not r.get("ok"):
                raise RuntimeError(f"Ping {idx} basarisiz: {r}")
            if int(r.get("protocolVersion") or 0) != 2:
                raise RuntimeError(f"Ping {idx} protocolVersion uyumsuz: {r}")
        print(json.dumps({
            "ok": True,
            "version": r2.get("version"),
            "protocolVersion": r2.get("protocolVersion"),
            "capabilities": r2.get("capabilities", []),
            "pingCount": 2,
        }, ensure_ascii=False))
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    main()
