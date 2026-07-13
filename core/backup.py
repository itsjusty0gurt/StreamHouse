from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from config.version import VERSION
from core.paths import user_data_root


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
        archive = self.backup_directory / f"sally-{label}-{timestamp}.zip"
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
        archives = sorted(self.backup_directory.glob("sally-*.zip"))
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
