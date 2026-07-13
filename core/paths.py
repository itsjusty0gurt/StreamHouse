from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def user_data_root() -> Path:
    override = os.environ.get("SALLY_DATA_DIR")
    if override:
        root = Path(override).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    root = local_app_data / "SallyAI"
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return root
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "SallyAI"
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def migrate_legacy_user_data() -> tuple[str, ...]:
    """Copy development-era user files into the stable app-data directory."""
    source_root = project_root()
    destination_root = user_data_root()
    migrated: list[str] = []
    relative_files = (
        "config/settings.json",
        "memory/twitch_chatters.json",
        "memory/twitch_activity.json",
        "memory/stream_sessions.json",
    )
    for relative in relative_files:
        source = source_root / relative
        destination = destination_root / relative
        if source.exists() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            migrated.append(relative)
        backup = source.with_suffix(source.suffix + ".bak")
        destination_backup = destination.with_suffix(
            destination.suffix + ".bak"
        )
        if backup.exists() and not destination_backup.exists():
            destination_backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, destination_backup)
    return tuple(migrated)
