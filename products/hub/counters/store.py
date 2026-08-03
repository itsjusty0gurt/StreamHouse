from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any, Callable

from products.hub.counters.models import CounterDefinition, validate_counter_id
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup

INDEX_VERSION = 1
COUNTER_VERSION = 1


def empty_counter_payload(counter_id: str, starting_total: int = 0) -> dict[str, Any]:
    return {"version": COUNTER_VERSION, "counter_id": validate_counter_id(counter_id), "channel_total": int(starting_total), "current_stream": {"stream_id": "", "value": 0}, "viewers": {}}


class CounterStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.index_path = self.directory / "index.json"
        self._index_lock = RLock()
        self._locks_guard = RLock()
        self._counter_locks: dict[str, RLock] = {}

    def _lock_for(self, counter_id: str) -> RLock:
        counter_id = validate_counter_id(counter_id)
        with self._locks_guard:
            return self._counter_locks.setdefault(counter_id, RLock())

    def _counter_path(self, counter_id: str) -> Path:
        return self.directory / f"{validate_counter_id(counter_id)}.json"

    @staticmethod
    def _check_version(payload: dict[str, Any], expected: int, label: str) -> None:
        if payload.get("version") != expected:
            raise ValueError(f"Unsupported {label} version {payload.get('version')!r}; expected {expected}.")

    def list_definitions(self) -> list[CounterDefinition]:
        with self._index_lock:
            if not self.index_path.exists() and not self.index_path.with_suffix(".json.bak").exists():
                return []
            payload = load_json_with_backup(self.index_path)
            if not isinstance(payload, dict):
                raise ValueError("Counter index must be a JSON object.")
            self._check_version(payload, INDEX_VERSION, "counter index")
            raw = payload.get("counters", [])
            if not isinstance(raw, list):
                raise ValueError("Counter index counters must be a list.")
            definitions = [CounterDefinition.from_dict(item) for item in raw if isinstance(item, dict)]
            ids = [item.counter_id for item in definitions]
            if len(ids) != len(set(ids)):
                raise ValueError("Counter index contains duplicate IDs.")
            return definitions

    def save_definitions(self, definitions: list[CounterDefinition]) -> None:
        with self._index_lock:
            atomic_write_json(self.index_path, {"version": INDEX_VERSION, "counters": [item.to_dict() for item in definitions]})

    def create(self, definition: CounterDefinition, starting_total: int = 0) -> None:
        with self._index_lock:
            definitions = self.list_definitions()
            if any(item.counter_id == definition.counter_id for item in definitions):
                raise ValueError(f'Counter ID "{definition.counter_id}" already exists.')
            path = self._counter_path(definition.counter_id)
            with self._lock_for(definition.counter_id):
                if path.exists():
                    raise ValueError(f'Counter data file "{path.name}" already exists.')
                atomic_write_json(path, empty_counter_payload(definition.counter_id, starting_total))
                try:
                    self.save_definitions([*definitions, definition])
                except Exception:
                    path.unlink(missing_ok=True)
                    raise

    def update_definition(self, definition: CounterDefinition) -> None:
        with self._index_lock:
            definitions = self.list_definitions()
            if not any(item.counter_id == definition.counter_id for item in definitions):
                raise KeyError(definition.counter_id)
            self.save_definitions([definition if item.counter_id == definition.counter_id else item for item in definitions])

    def _read_data_unlocked(self, counter_id: str) -> dict[str, Any]:
        path = self._counter_path(counter_id)
        if not path.exists() and not path.with_suffix(".json.bak").exists():
            return empty_counter_payload(counter_id)
        payload = load_json_with_backup(path)
        if not isinstance(payload, dict):
            raise ValueError(f"Counter {counter_id} data must be a JSON object.")
        self._check_version(payload, COUNTER_VERSION, f"counter {counter_id}")
        if payload.get("counter_id") != counter_id or not isinstance(payload.get("viewers", {}), dict):
            raise ValueError(f"Counter {counter_id} data is invalid.")
        return payload

    def read_data(self, counter_id: str) -> dict[str, Any]:
        with self._lock_for(counter_id):
            return self._read_data_unlocked(counter_id)

    def mutate_data(self, counter_id: str, mutation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock_for(counter_id):
            original = self._read_data_unlocked(counter_id)
            working = {**original, "current_stream": dict(original.get("current_stream", {})), "viewers": {str(key): {**value, "current_stream": dict(value.get("current_stream", {}))} for key, value in original.get("viewers", {}).items() if isinstance(value, dict)}}
            result = mutation(working)
            atomic_write_json(self._counter_path(counter_id), working)
            return result

    def delete(self, counter_id: str) -> None:
        counter_id = validate_counter_id(counter_id)
        with self._index_lock, self._lock_for(counter_id):
            definitions = self.list_definitions()
            if not any(item.counter_id == counter_id for item in definitions):
                raise KeyError(counter_id)
            self.save_definitions([item for item in definitions if item.counter_id != counter_id])
            path = self._counter_path(counter_id)
            path.unlink(missing_ok=True)
            path.with_suffix(".json.bak").unlink(missing_ok=True)
