from __future__ import annotations

from copy import deepcopy
from typing import Any


CURRENT_VERSIONS = {
    "activity": 1,
    "chatters": 3,
    "sessions": 1,
}


def migrate_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Return a migrated copy of a supported local-data payload."""
    if kind not in CURRENT_VERSIONS:
        raise ValueError(f"Unknown migration kind: {kind}")
    migrated = deepcopy(payload)
    version = int(migrated.get("version", 1))
    target = CURRENT_VERSIONS[kind]
    if version > target:
        raise ValueError(
            f"{kind.title()} data version {version} is newer than supported {target}."
        )
    if kind == "chatters":
        records = migrated.get("chatters", {})
        if isinstance(records, dict):
            for record in records.values():
                if not isinstance(record, dict):
                    continue
                record.setdefault("memories", [])
                record.setdefault("tags", [])
                record.setdefault("private_notes", "")
                record.setdefault("session_messages", {})
                record.setdefault("timeline", [])
                record.setdefault("role_history", [])
    migrated["version"] = target
    return migrated
