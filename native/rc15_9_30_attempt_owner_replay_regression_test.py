#!/usr/bin/env python3
import json
from pathlib import Path

import uyap_archive_agent as agent

ROOT = Path(__file__).resolve().parents[1]
bridge = (ROOT / "background" / "native-agent.js").read_text(encoding="utf-8")
manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))


def req(value, message):
    if not value:
        raise AssertionError(message)


stored = json.dumps(["session-1", "document-1", r"C:\old\evrak.pdf", "UYAP_Arsiv/_incoming/session-1/attempt-1.part", "UYAP_Arsiv/old/path.bin"])
same_owner_late_target = {
    "sessionId": "session-1", "attemptId": "attempt-1",
    "stagingPath": "UYAP_Arsiv/_incoming/session-1/attempt-1.part",
    "targetPath": "UYAP_Arsiv/new/path.pdf",
    "document": {"documentId": "document-1"},
}
different_document = {**same_owner_late_target, "document": {"documentId": "document-2"}}
different_staging = {**same_owner_late_target, "stagingPath": "UYAP_Arsiv/_incoming/session-1/attempt-2.part"}

req(not agent._journal_identity_matches(stored, same_owner_late_target), "target drift must be detected")
req(agent._journal_attempt_owner_matches(stored, same_owner_late_target), "same attempt owner must replay")
req(not agent._journal_attempt_owner_matches(stored, different_document), "different document must fail closed")
req(not agent._journal_attempt_owner_matches(stored, different_staging), "different staging owner must fail closed")
req(tuple(map(int, agent.AGENT_VERSION.split("."))) >= (2, 7, 64), agent.AGENT_VERSION)
req("attempt_owner_replay_v1" in agent.dispatch({"action": "ping"}).get("capabilities", []), "native capability missing")
req(tuple(map(int, bridge.split('const MIN_AGENT_VERSION = "', 1)[1].split('"', 1)[0].split("."))) >= (2, 7, 64), "old agent is not rejected")
req("ATTEMPT_OWNER_REPLAY_CAPABILITY" in bridge, "extension capability gate missing")
req("2.9.0 RC15.9." in manifest.get("version_name", ""), "release identity")

print("RC15.9.30 attempt-owner replay regression: PASS")
