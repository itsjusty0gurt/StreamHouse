from __future__ import annotations

from pathlib import Path

from products.hub.core.backup import (
    BackupComponent,
    BackupInspection,
    BackupManager,
    BackupPreset,
    BackupSummary,
    RestoreReport,
)
from shared.streamhouse_runtime.paths import user_data_root


class ReleaseController:
    """Own Hub data backup and restore workflows."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or user_data_root()
        self.backups = BackupManager(self.project_root)

    @property
    def backup_directory(self) -> Path:
        return self.backups.backup_directory

    def automatic_backup(self, *, enabled: bool = True) -> Path | None:
        if not enabled:
            return None
        return self.backups.create_daily_if_needed()

    def summarize_backup(
        self,
        preset: BackupPreset,
        components: tuple[BackupComponent, ...] = (),
    ) -> BackupSummary:
        return self.backups.summarize(preset, components)

    def create_backup(
        self,
        preset: BackupPreset = BackupPreset.RECOMMENDED,
        components: tuple[BackupComponent, ...] = (),
        destination: Path | None = None,
    ) -> Path:
        return self.backups.create(
            "manual",
            preset=preset,
            components=components,
            destination=destination,
        )

    def inspect_backup(self, archive: Path) -> BackupInspection:
        return self.backups.inspect(archive)

    def restore_backup(
        self,
        archive: Path,
        components: tuple[BackupComponent, ...],
        *,
        active_stream_id: str = "",
    ) -> RestoreReport:
        return self.backups.restore(
            archive,
            components,
            active_stream_id=active_stream_id,
        )

    def scrub_viewer_data(self, user_id: str) -> int:
        return self.backups.scrub_viewer(user_id)
