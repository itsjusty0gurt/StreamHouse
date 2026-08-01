import tempfile
import unittest
from pathlib import Path

from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup


class JsonStoreTests(unittest.TestCase):
    def test_corrupt_primary_falls_back_to_previous_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"version": 1})
            atomic_write_json(path, {"version": 2})
            path.write_text("not json", encoding="utf-8")

            self.assertEqual(load_json_with_backup(path), {"version": 1})

    def test_atomic_write_leaves_no_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            atomic_write_json(path, {"ok": True})
            self.assertFalse(path.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
