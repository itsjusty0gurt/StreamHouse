from __future__ import annotations

import os
import tempfile
from pathlib import Path


DATA_DIRECTORY_ENV = "STREAMHOUSE_DATA_DIR"
SMOKE_TEST_ENV = "STREAMHOUSE_SMOKE_TEST"
DATA_DIRECTORY_NAME = "Streamhouse"


def smoke_test_enabled() -> bool:
    return os.environ.get(SMOKE_TEST_ENV, "0") == "1"


def _local_app_data_root() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    return Path(configured) if configured else Path.home()


def user_data_root() -> Path:
    override = os.environ.get(DATA_DIRECTORY_ENV)
    if override:
        root = Path(override).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root
    root = _local_app_data_root() / DATA_DIRECTORY_NAME
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / ".write-test"
        probe.write_text("ok", encoding="ascii")
        probe.unlink()
        return root
    except OSError:
        fallback = Path(tempfile.gettempdir()) / DATA_DIRECTORY_NAME
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback
