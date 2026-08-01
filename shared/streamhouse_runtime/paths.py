from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DATA_DIRECTORY_ENV = "STREAMHOUSE_DATA_DIR"
LEGACY_DATA_DIRECTORY_ENV = "SALLY_DATA_DIR"
SMOKE_TEST_ENV = "STREAMHOUSE_SMOKE_TEST"
LEGACY_SMOKE_TEST_ENV = "SALLY_SMOKE_TEST"
DATA_DIRECTORY_NAME = "Streamhouse"
LEGACY_DATA_DIRECTORY_NAME = "SallyAI"

_pending_deprecation_warnings: set[str] = set()


@dataclass(frozen=True, slots=True)
class UserDataMigrationReport:
    copied_files: int = 0
    existing_files: int = 0
    failed_files: int = 0
    scanned_sources: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.copied_files > 0


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def environment_value(
    name: str,
    legacy_name: str,
    default: str | None = None,
) -> str | None:
    value = os.environ.get(name)
    if value is not None:
        return value
    legacy_value = os.environ.get(legacy_name)
    if legacy_value is not None:
        _pending_deprecation_warnings.add(
            f"{legacy_name} is deprecated; use {name} instead."
        )
        return legacy_value
    return default


def consume_deprecation_warnings() -> tuple[str, ...]:
    warnings = tuple(sorted(_pending_deprecation_warnings))
    _pending_deprecation_warnings.clear()
    return warnings


def smoke_test_enabled() -> bool:
    return (
        environment_value(SMOKE_TEST_ENV, LEGACY_SMOKE_TEST_ENV, "0") == "1"
    )


def _local_app_data_root() -> Path:
    configured = os.environ.get("LOCALAPPDATA")
    return Path(configured) if configured else Path.home()


def legacy_user_data_root() -> Path:
    return _local_app_data_root() / LEGACY_DATA_DIRECTORY_NAME


def user_data_root() -> Path:
    override = environment_value(
        DATA_DIRECTORY_ENV,
        LEGACY_DATA_DIRECTORY_ENV,
    )
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


def _copy_missing_tree(
    source: Path,
    destination: Path,
    relative_files: Iterable[Path] | None = None,
) -> tuple[int, int, int]:
    if not source.is_dir() or source.resolve() == destination.resolve():
        return 0, 0, 0
    candidates = (
        tuple(relative_files)
        if relative_files is not None
        else tuple(
            path.relative_to(source)
            for path in source.rglob("*")
            if path.is_file() and not path.is_symlink()
        )
    )
    copied = 0
    existing = 0
    failed = 0
    for relative in candidates:
        source_file = source / relative
        if not source_file.is_file() or source_file.is_symlink():
            continue
        destination_file = destination / relative
        if destination_file.exists():
            existing += 1
            continue
        try:
            destination_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_file, destination_file)
            copied += 1
        except OSError:
            failed += 1
    return copied, existing, failed


def migrate_legacy_user_data(
    legacy_root: Path | None = None,
    destination_root: Path | None = None,
    *,
    include_development_files: bool = True,
) -> UserDataMigrationReport:
    """Copy legacy data into Streamhouse storage without overwriting or deleting."""

    destination = destination_root or user_data_root()
    copied = 0
    existing = 0
    failed = 0
    scanned: list[str] = []

    explicit_override = bool(
        os.environ.get(DATA_DIRECTORY_ENV)
        or os.environ.get(LEGACY_DATA_DIRECTORY_ENV)
    )
    legacy = legacy_root
    if legacy is None and not explicit_override:
        legacy = legacy_user_data_root()
    if legacy is not None and legacy.is_dir():
        scanned.append(str(legacy))
        result = _copy_missing_tree(legacy, destination)
        copied += result[0]
        existing += result[1]
        failed += result[2]

    if include_development_files and not explicit_override:
        development_root = project_root()
        relative_files = tuple(
            Path(value)
            for value in (
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
        )
        if any((development_root / relative).exists() for relative in relative_files):
            scanned.append(str(development_root))
            result = _copy_missing_tree(
                development_root,
                destination,
                relative_files,
            )
            copied += result[0]
            existing += result[1]
            failed += result[2]

    return UserDataMigrationReport(
        copied_files=copied,
        existing_files=existing,
        failed_files=failed,
        scanned_sources=tuple(scanned),
    )
