import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from core.backup import BackupManager
from core.diagnostics import export_diagnostics
from core.migrations import migrate_payload
from ui.controllers.release_controller import ReleaseController


class ReleaseToolsTests(unittest.TestCase):
    def test_backup_create_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "config" / "settings.json"
            settings.parent.mkdir(parents=True)
            settings.write_text('{"value": 1}', encoding="utf-8")
            manager = BackupManager(root, root / "backups")
            archive = manager.create("test")
            settings.write_text('{"value": 2}', encoding="utf-8")

            report = manager.restore(archive)

            self.assertIn("config/settings.json", report.restored_files)
            self.assertEqual(
                json.loads(settings.read_text(encoding="utf-8"))["value"],
                1,
            )

    def test_diagnostics_include_only_warning_and_error_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "logs"
            logs.mkdir()
            (logs / "latest.log").write_text(
                "[   INFO  ] user: private chat\n"
                "[ WARNING ] Authorization=secret-value\n",
                encoding="utf-8",
            )
            destination = root / "diagnostics.zip"
            export_diagnostics(
                destination,
                root,
                {"startup_page": "AI"},
                {"connection": "Connected"},
            )

            with ZipFile(destination) as archive:
                warnings = archive.read("warnings.log").decode()
                payload = json.loads(archive.read("diagnostics.json"))
            self.assertNotIn("private chat", warnings)
            self.assertNotIn("secret-value", warnings)
            self.assertEqual(payload["health"]["connection"], "Connected")

    def test_chatter_migration_adds_release_fields(self) -> None:
        migrated = migrate_payload(
            "chatters",
            {"version": 1, "chatters": {"1": {"user_name": "Viewer"}}},
        )
        record = migrated["chatters"]["1"]
        self.assertEqual(migrated["version"], 5)
        self.assertEqual(record["manual_group"], "")
        self.assertEqual(record["timeline"], [])
        self.assertEqual(record["private_notes"], "")
        self.assertTrue(record["memory_enabled"])

    def test_release_controller_creates_daily_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ReleaseController(Path(directory))
            first = controller.automatic_backup()
            second = controller.automatic_backup()
            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_windows_release_assets_exist(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "assets" / "sally-icon.ico").exists())
        metadata = root / "packaging" / "windows-version-info.txt"
        self.assertIn("0.1.0", metadata.read_text(encoding="utf-8"))
        self.assertTrue((root / "scripts" / "package_release.ps1").exists())


if __name__ == "__main__":
    unittest.main()
