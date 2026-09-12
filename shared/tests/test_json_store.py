import tempfile
import unittest
import os
import time
from pathlib import Path
from unittest.mock import patch

from shared.streamhouse_runtime.json_store import (
    JsonStoreCorruptionError,
    UnsupportedJsonSchemaError,
    atomic_write_bytes,
    atomic_write_json,
    load_json_with_backup,
)


class JsonStoreTests(unittest.TestCase):
    def test_corrupt_primary_falls_back_to_previous_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"version": 1})
            atomic_write_json(path, {"version": 2})
            path.write_text("not json", encoding="utf-8")

            self.assertEqual(load_json_with_backup(path), {"version": 1})
            self.assertEqual(path.read_text(encoding="utf-8").strip(), '{\n  "version": 1\n}')
            self.assertEqual(len(list((path.parent / "corrupt").glob("data-*.json"))), 1)

    def test_atomic_write_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"ok": True})
            self.assertFalse(list(path.parent.glob(".data.json.*.tmp")))

    def test_failed_replace_preserves_valid_live_file_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"value": "old"})
            real_replace = __import__("os").replace

            def fail_live_replace(source: object, destination: object) -> None:
                if Path(destination) == path:
                    raise PermissionError("locked")
                real_replace(source, destination)

            with patch(
                "shared.streamhouse_runtime.json_store.os.replace",
                side_effect=fail_live_replace,
            ):
                with self.assertRaises(PermissionError):
                    atomic_write_json(path, {"value": "new"})

            self.assertEqual(load_json_with_backup(path), {"value": "old"})
            self.assertFalse(list(path.parent.glob(".data.json.*.tmp")))

    def test_current_schema_validation_uses_backup_and_quarantines_primary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"version": 1, "items": []})
            atomic_write_json(path, {"version": 1, "items": ["good"]})
            path.write_text('{"version": 1, "items": "broken"}', encoding="utf-8")

            def validate(payload: object) -> None:
                if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
                    raise ValueError("items must be a list")

            self.assertEqual(
                load_json_with_backup(path, validator=validate),
                {"version": 1, "items": []},
            )
            self.assertEqual(
                load_json_with_backup(path, validator=validate),
                {"version": 1, "items": []},
            )
            self.assertEqual(len(list((path.parent / "corrupt").glob("data-*.json"))), 1)

    def test_corrupt_file_without_backup_is_preserved_and_failure_is_surfaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text("not json", encoding="utf-8")

            with self.assertRaises(JsonStoreCorruptionError) as raised:
                load_json_with_backup(path)

            self.assertIsNotNone(raised.exception.quarantine_path)
            self.assertFalse(path.exists())

    def test_unsupported_schema_is_not_classified_as_corruption(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_text('{"version": 0}', encoding="utf-8")

            def validate(_payload: object) -> None:
                raise UnsupportedJsonSchemaError("obsolete")

            with self.assertRaisesRegex(UnsupportedJsonSchemaError, "obsolete"):
                load_json_with_backup(path, validator=validate)
            self.assertTrue(path.exists())

    def test_stale_temp_never_replaces_valid_live_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"value": "live"})
            stale = path.parent / ".data.json.interrupted.tmp"
            stale.write_text('{"value": "stale"}', encoding="utf-8")
            old = time.time() - (25 * 60 * 60)
            os.utime(stale, (old, old))

            self.assertEqual(load_json_with_backup(path), {"value": "live"})
            self.assertFalse(stale.exists())

    def test_atomic_byte_replace_preserves_existing_secret_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "secret.dat"
            atomic_write_bytes(path, b"encrypted-old")
            with patch(
                "shared.streamhouse_runtime.json_store.os.replace",
                side_effect=PermissionError("locked"),
            ):
                with self.assertRaises(PermissionError):
                    atomic_write_bytes(path, b"encrypted-new")
            self.assertEqual(path.read_bytes(), b"encrypted-old")
            self.assertFalse(list(path.parent.glob(".secret.dat.*.tmp")))

    def test_serialization_failure_preserves_existing_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"value": "old"})

            with self.assertRaises(TypeError):
                atomic_write_json(path, {"value": object()})

            self.assertEqual(load_json_with_backup(path), {"value": "old"})
            self.assertFalse(list(path.parent.glob(".data.json.*.tmp")))

    def test_missing_live_file_recovers_valid_backup_without_using_temp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"value": "first"})
            atomic_write_json(path, {"value": "second"})
            path.unlink()

            self.assertEqual(load_json_with_backup(path), {"value": "first"})
            self.assertEqual(load_json_with_backup(path), {"value": "first"})


if __name__ == "__main__":
    unittest.main()
