from __future__ import annotations

from pathlib import Path

from products.hub.core.backup import BackupManager, RestoreReport
from shared.streamhouse_runtime.paths import user_data_root


class ReleaseController:
    """Own Hub data backup and restore workflows."""

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root or user_data_root()
        self.backups = BackupManager(self.project_root)

    def automatic_backup(self) -> Path | None:
        return self.backups.create_daily_if_needed()

    def create_backup(self) -> Path:
        return self.backups.create("manual")

    def restore_latest(self) -> RestoreReport | None:
        latest = self.backups.latest()
        if latest is None:
            return None
        self.backups.create("before-restore")
        return self.backups.restore(latest)

    def scrub_viewer_data(self, user_id: str) -> int:
        return self.backups.scrub_viewer(user_id)
