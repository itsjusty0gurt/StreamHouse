from __future__ import annotations

import sys
from pathlib import Path


def resource_path(relative: str) -> Path:
    """Resolve AI resources in source checkouts and PyInstaller bundles."""

    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / relative
    repository_root = Path(__file__).resolve().parents[3]
    return repository_root / "shared" / relative
