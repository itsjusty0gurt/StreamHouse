from __future__ import annotations

import json
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from shared.streamhouse_runtime.version import VERSION


_SECRET_PATTERN = re.compile(
    r"(?i)(access[_ -]?token|refresh[_ -]?token|authorization|secret)\s*[:=]\s*\S+"
)


def export_diagnostics(
    destination: Path,
    project_root: Path,
    settings: dict[str, Any],
    health: dict[str, Any],
) -> Path:
    warnings: list[str] = []
    latest_log = project_root / "logs" / "latest.log"
    if latest_log.exists():
        lines = latest_log.read_text(encoding="utf-8", errors="replace").splitlines()
        warnings = [
            _SECRET_PATTERN.sub(r"\1=[REDACTED]", line)
            for line in lines[-2000:]
            if "[ WARNING ]" in line
            or "[  ERROR  ]" in line
            or "[CRITICAL ]" in line
        ][-500:]
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "streamhouse_version": VERSION,
        "python": sys.version,
        "platform": platform.platform(),
        "settings": settings,
        "health": health,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(destination, "w", ZIP_DEFLATED) as archive:
        archive.writestr("diagnostics.json", json.dumps(payload, indent=2))
        archive.writestr("warnings.log", "\n".join(warnings))
    return destination
