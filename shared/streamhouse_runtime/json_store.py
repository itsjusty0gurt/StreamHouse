from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TypeVar
from uuid import uuid4

from shared.streamhouse_runtime.logger import Logger


JsonValidator = Callable[[Any], None]
ParsedJson = TypeVar("ParsedJson")


class UnsupportedJsonSchemaError(ValueError):
    """An intentionally unsupported persisted schema, not file corruption."""


class JsonStoreCorruptionError(ValueError):
    """A persisted JSON file could not be parsed or validated safely."""

    def __init__(self, path: Path, quarantine_path: Path | None, reason: BaseException) -> None:
        preserved = (
            f" Preserved the unreadable file as {quarantine_path.name}."
            if quarantine_path is not None
            else ""
        )
        super().__init__(f"Could not load {path.name}: {reason}.{preserved}")
        self.path = path
        self.quarantine_path = quarantine_path
        self.reason = reason


def _read_and_validate(path: Path, validator: JsonValidator | None) -> Any:
    with path.open(encoding="utf-8") as source:
        payload = json.load(source)
    if validator is not None:
        validator(payload)
    return payload


def _quarantine(path: Path) -> Path | None:
    if not path.exists():
        return None
    directory = path.parent / "corrupt"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    destination = directory / f"{path.stem}-{stamp}-{uuid4().hex[:8]}{path.suffix}"
    try:
        os.replace(path, destination)
    except OSError as error:
        Logger.error(
            f"Could not preserve unreadable local data {path.name}: {error}",
            source="DATA",
        )
        return None
    return destination


def _cleanup_stale_temps(path: Path) -> None:
    # A valid live file is always authoritative. Interrupted, uncommitted files
    # are never promoted automatically. Fresh unique temps may belong to an
    # active writer, especially until Hub gains a single-instance guard.
    cutoff = time.time() - (24 * 60 * 60)
    candidates = [*path.parent.glob(f".{path.name}.*.tmp")]
    legacy = path.with_suffix(path.suffix + ".tmp")
    if legacy.exists():
        candidates.append(legacy)
    for candidate in candidates:
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            pass


def json_store_exists(path: Path) -> bool:
    path = Path(path)
    return path.exists() or path.with_suffix(path.suffix + ".bak").exists()


def load_json_with_backup(
    path: Path,
    *,
    validator: JsonValidator | None = None,
) -> Any:
    """Load validated JSON, preserving corrupt data and using a known-good backup.

    Validators should raise :class:`UnsupportedJsonSchemaError` for obsolete
    pre-Alpha schemas. Those inputs remain distinct from current-schema
    corruption and are left to the owning store's reset policy.
    """
    path = Path(path)
    _cleanup_stale_temps(path)
    backup_path = path.with_suffix(path.suffix + ".bak")
    try:
        return _read_and_validate(path, validator)
    except FileNotFoundError:
        if not backup_path.exists():
            raise
        recovered = _read_and_validate(backup_path, validator)
        _durable_copy(backup_path, path)
        Logger.warning(
            f"Recovered missing local data from backup: {path.name}",
            source="DATA",
        )
        return recovered
    except UnsupportedJsonSchemaError:
        raise
    except OSError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as primary_error:
        try:
            recovered = _read_and_validate(backup_path, validator)
        except UnsupportedJsonSchemaError:
            raise
        except OSError as backup_error:
            quarantine = _quarantine(path)
            raise JsonStoreCorruptionError(path, quarantine, primary_error) from backup_error
        except (TypeError, ValueError, json.JSONDecodeError) as backup_error:
            primary_quarantine = _quarantine(path)
            _quarantine(backup_path)
            raise JsonStoreCorruptionError(path, primary_quarantine, primary_error) from backup_error

        quarantine = _quarantine(path)
        Logger.warning(
            f"Recovered unreadable local data from backup: {path.name}"
            + (f"; preserved original as {quarantine.name}" if quarantine else ""),
            source="DATA",
        )
        try:
            _durable_copy(backup_path, path)
        except OSError as error:
            Logger.error(
                f"Could not restore the known-good live copy for {path.name}: {error}",
                source="DATA",
            )
        return recovered


def load_validated_json(
    path: Path,
    parser: Callable[[Any], ParsedJson],
) -> ParsedJson:
    """Load JSON and return only a completely parsed, validated store state."""
    parsed: list[ParsedJson] = []

    def validate(payload: Any) -> None:
        value = parser(payload)
        parsed.clear()
        parsed.append(value)

    load_json_with_backup(path, validator=validate)
    return parsed[0]


def _durable_copy(source: Path, destination: Path) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as target:
            temporary = Path(target.name)
            with source.open("rb") as current:
                shutil.copyfileobj(current, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, destination)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Durably write bytes to a same-directory temp file, then replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary = Path(destination.name)
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_json(path: Path, payload: Any) -> None:
    """Durably serialize then atomically replace JSON, retaining one prior version."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = path.with_suffix(path.suffix + ".bak")
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as destination:
            temporary_path = Path(destination.name)
            json.dump(payload, destination, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())

        # Prove serialization is readable before touching either durable copy.
        with temporary_path.open(encoding="utf-8") as source:
            json.load(source)

        if path.exists():
            _durable_copy(path, backup_path)
        for attempt in range(4):
            try:
                os.replace(temporary_path, path)
                temporary_path = None
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.02 * (attempt + 1))
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
