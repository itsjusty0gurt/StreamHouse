from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from shared.streamhouse_runtime.logger import Logger


def load_json_with_backup(path: Path) -> Any:
    """Load JSON, falling back to the last known-good backup."""
    try:
        with path.open(encoding="utf-8") as source:
            return json.load(source)
    except (OSError, ValueError, json.JSONDecodeError) as primary_error:
        backup_path = path.with_suffix(path.suffix + ".bak")
        if not backup_path.exists():
            raise primary_error
        Logger.warning(
            f"Recovered unreadable local data from backup: {path.name}",
            source="DATA",
        )
        with backup_path.open(encoding="utf-8") as source:
            return json.load(source)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Atomically replace JSON while retaining one previous version."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    backup_path = path.with_suffix(path.suffix + ".bak")
    with temporary_path.open("w", encoding="utf-8") as destination:
        json.dump(payload, destination, indent=2)
        destination.write("\n")
    if path.exists():
        shutil.copy2(path, backup_path)
    for attempt in range(4):
        try:
            temporary_path.replace(path)
            break
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.02 * (attempt + 1))
