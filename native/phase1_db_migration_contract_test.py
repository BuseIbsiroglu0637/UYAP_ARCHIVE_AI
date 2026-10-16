#!/usr/bin/env python3
"""Static contract test for Phase 1 DB migration/rollback framework."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

release = json.loads((ROOT / "config" / "release-contract.json").read_text(encoding="utf-8"))
migrations = json.loads((ROOT / "config" / "storage-migrations.json").read_text(encoding="utf-8"))
constants = (ROOT / "shared" / "constants.js").read_text(encoding="utf-8")
db = (ROOT / "background" / "db.js").read_text(encoding="utf-8")
registry = (ROOT / "background" / "db-migrations.js").read_text(encoding="utf-8")

match = re.search(r'export const DB_VERSION\s*=\s*(\d+)', constants)
assert match, "DB_VERSION not found"
db_version = int(match.group(1))

assert db_version == 6
assert release["storage"]["indexedDbVersion"] == db_version
assert release["storage"]["migrationFrameworkVersion"] == 1
assert migrations["currentVersion"] == db_version
assert migrations["frameworkVersion"] == 1
assert migrations["strategy"] == "additive-idempotent"
assert migrations["downgrade"]["supported"] is False
assert migrations["rollback"]["destructiveMigrationRequiresBackup"] is True
assert migrations["rollback"]["preMigrationBackupImplementedForDestructiveChanges"] is False
assert migrations["releaseGate"]["backupRestoreTestRequiredForDestructiveMigration"] is True

assert 'import { applyDatabaseUpgrade } from "./db-migrations.js";' in db
assert "applyDatabaseUpgrade({" in db
assert "request.oldVersion" in db
assert "request.newVersion || DB_VERSION" in db
assert "createObjectStore" not in db, "schema mutation leaked back into db.js"

assert "export const CURRENT_DB_VERSION = 6;" in registry
assert "export const DB_MIGRATION_FRAMEWORK_VERSION = 1;" in registry
assert 'reason:"DOWNGRADE_NOT_SUPPORTED"' in registry
assert 'reason:"UNKNOWN_TARGET_VERSION"' in registry
assert "if (Number(oldVersion) < 5)" in registry
assert 'ensureIndex(documents, "sessionStatus"' in registry
assert 'ensureIndex(diagnosticEvents, "sessionAt"' in registry

print("Phase 1 DB migration and rollback contract: PASS")
