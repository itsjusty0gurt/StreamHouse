from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from os import replace
from zipfile import ZIP_DEFLATED, ZipFile

from shared.streamhouse_runtime.version import VERSION
from shared.streamhouse_runtime.paths import user_data_root


@dataclass(frozen=True, slots=True)
class RestoreReport:
    archive: Path
    restored_files: tuple[str, ...]


class BackupManager:
    FILES = (
        "config/settings.json",
        "memory/twitch_chatters.json",
        "memory/twitch_activity.json",
        "memory/stream_sessions.json",
        "twitch/commands.json",
        "twitch/event_triggers.json",
        "automation/routines.json",
        "automation/core_triggers.json",
        "obs/connection.json",
        "obs/triggers.json",
    )

    def __init__(
        self,
        project_root: Path | None = None,
        backup_directory: Path | None = None,
    ) -> None:
        self.project_root = project_root or user_data_root()
        self.backup_directory = (
            backup_directory or self.project_root / "backups"
        )

    def create(self, label: str = "manual") -> Path:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        archive = self.backup_directory / f"streamhouse-{label}-{timestamp}.zip"
        included: list[str] = []
        with ZipFile(archive, "w", ZIP_DEFLATED) as destination:
            for relative in self.FILES:
                source = self.project_root / relative
                if source.exists():
                    destination.write(source, relative)
                    included.append(relative)
            destination.writestr(
                "manifest.json",
                json.dumps(
                    {
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "version": VERSION,
                        "files": included,
                    },
                    indent=2,
                ),
            )
        return archive

    def latest(self) -> Path | None:
        archives = sorted(
            (
                *self.backup_directory.glob("streamhouse-*.zip"),
                *self.backup_directory.glob("sally-*.zip"),
            ),
            key=lambda path: path.stat().st_mtime,
        )
        return archives[-1] if archives else None

    def restore(self, archive: Path) -> RestoreReport:
        allowed = set(self.FILES)
        restored: list[str] = []
        with ZipFile(archive) as source:
            for member in source.infolist():
                relative = member.filename.replace("\\", "/")
                if relative not in allowed:
                    continue
                destination = (self.project_root / relative).resolve()
                if self.project_root.resolve() not in destination.parents:
                    raise ValueError("Backup contains an unsafe path.")
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(source.read(member))
                restored.append(relative)
        return RestoreReport(archive, tuple(restored))

    def create_daily_if_needed(self) -> Path | None:
        latest = self.latest()
        today = datetime.now(timezone.utc).date()
        if latest is not None:
            modified = datetime.fromtimestamp(
                latest.stat().st_mtime,
                timezone.utc,
            ).date()
            if modified == today:
                return None
        return self.create("automatic")

    def scrub_viewer(self, user_id: str, user_name: str = "") -> int:
        """Remove a viewer's profile and activity rows from existing backups."""
        clean_id = user_id.strip()
        clean_name = user_name.strip().casefold()
        changed_archives = 0
        archives = (
            *self.backup_directory.glob("streamhouse-*.zip"),
            *self.backup_directory.glob("sally-*.zip"),
        )
        for archive in archives:
            changed = False
            temporary = archive.with_suffix(archive.suffix + ".tmp")
            with ZipFile(archive, "r") as source, ZipFile(
                temporary, "w", ZIP_DEFLATED
            ) as destination:
                for member in source.infolist():
                    data = source.read(member)
                    if member.filename == "memory/twitch_chatters.json":
                        data, item_changed = self._scrub_chatter_payload(
                            data, clean_id
                        )
                        changed = changed or item_changed
                    elif member.filename == "memory/twitch_activity.json":
                        data, item_changed = self._scrub_activity_payload(
                            data, clean_id, clean_name
                        )
                        changed = changed or item_changed
                    destination.writestr(member, data)
            if changed:
                replace(temporary, archive)
                changed_archives += 1
            else:
                temporary.unlink(missing_ok=True)
        return changed_archives

    @staticmethod
    def _scrub_chatter_payload(data: bytes, user_id: str) -> tuple[bytes, bool]:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return data, False
        records = payload.get("chatters", {}) if isinstance(payload, dict) else {}
        if not isinstance(records, dict) or records.pop(user_id, None) is None:
            return data, False
        return json.dumps(payload, indent=2).encode("utf-8"), True

    @staticmethod
    def _scrub_activity_payload(
        data: bytes,
        user_id: str,
        user_name: str,
    ) -> tuple[bytes, bool]:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return data, False
        events = payload.get("events", []) if isinstance(payload, dict) else []
        if not isinstance(events, list):
            return data, False
        retained = [
            event
            for event in events
            if not (
                isinstance(event, dict)
                and (
                    str(event.get("user_id", "")) == user_id
                    or (
                        user_name
                        and not event.get("user_id")
                        and str(event.get("text", "")).casefold().startswith(
                            user_name + " "
                        )
                    )
                )
            )
        ]
        if len(retained) == len(events):
            return data, False
        payload["events"] = retained
        return json.dumps(payload, indent=2).encode("utf-8"), True
