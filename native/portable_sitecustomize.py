"""UYAP Archive AI portable Native Messaging bootstrap.

This module is loaded automatically by the official CPython embeddable runtime.
For a real Chrome/Edge Native Messaging launch, the browser passes the extension
origin in the interpreter's script slot (sys.argv[0]). We intentionally inspect
ONLY that slot. Scanning every argument is unsafe because installer/self-test
processes may legitimately carry a chrome-extension:// value as a later argument.
"""
from __future__ import annotations

import os
import sys


def _native_origin_from_argv(argv=None):
    args = list(sys.argv if argv is None else argv)
    if not args:
        return None
    value = str(args[0] or "").strip()
    if value.lower().startswith("chrome-extension://"):
        return value
    return None


def _is_native_messaging_launch(argv=None) -> bool:
    return _native_origin_from_argv(argv) is not None


if _is_native_messaging_launch():
    try:
        from uyap_archive_agent import main
        main()
        os._exit(0)
    except BaseException as exc:  # stderr is permitted by Native Messaging.
        try:
            print(f"UYAP_NATIVE_HOST_BOOTSTRAP: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        finally:
            os._exit(1)
