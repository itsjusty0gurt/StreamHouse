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

    def test_backup_includes_custom_twitch_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commands = root / "twitch" / "commands.json"
            commands.parent.mkdir(parents=True)
            commands.write_text(
                '{"version":2,"triggers":[]}', encoding="utf-8"
            )
            event_triggers = root / "twitch" / "event_triggers.json"
            event_triggers.write_text(
                '{"version":1,"triggers":[]}', encoding="utf-8"
            )
            routines = root / "automation" / "routines.json"
            routines.parent.mkdir(parents=True)
            routines.write_text(
                '{"version":1,"routines":[]}', encoding="utf-8"
            )
            core_triggers = root / "automation" / "core_triggers.json"
            core_triggers.write_text(
                '{"version":1,"triggers":[]}', encoding="utf-8"
            )
            archive = BackupManager(root, root / "backups").create("test")

            with ZipFile(archive) as source:
                self.assertIn("twitch/commands.json", source.namelist())
                self.assertIn("twitch/event_triggers.json", source.namelist())
                self.assertIn("automation/routines.json", source.namelist())
                self.assertIn(
                    "automation/core_triggers.json", source.namelist()
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
        self.assertEqual(migrated["version"], 6)
        self.assertEqual(record["manual_group"], "")
        self.assertEqual(record["timeline"], [])
        self.assertEqual(record["private_notes"], "")
        self.assertFalse(record["memory_enabled"])
        self.assertEqual(record["memory_consent"], "unknown")

    def test_release_controller_creates_daily_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            controller = ReleaseController(Path(directory))
            first = controller.automatic_backup()
            second = controller.automatic_backup()
            self.assertIsNotNone(first)
            self.assertIsNone(second)

    def test_backup_scrub_removes_deleted_viewer_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            memory = root / "memory"
            memory.mkdir()
            (memory / "twitch_chatters.json").write_text(
                json.dumps(
                    {
                        "version": 6,
                        "chatters": {"1": {"user_name": "Viewer"}, "2": {}},
                    }
                ),
                encoding="utf-8",
            )
            (memory / "twitch_activity.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "events": [
                            {"user_id": "1", "text": "Viewer followed"},
                            {"user_id": "2", "text": "Other followed"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            controller = ReleaseController(root)
            archive = controller.create_backup()

            self.assertEqual(controller.scrub_viewer_data("1", "Viewer"), 1)
            with ZipFile(archive) as source:
                chatters = json.loads(
                    source.read("memory/twitch_chatters.json")
                )
                activity = json.loads(
                    source.read("memory/twitch_activity.json")
                )
            self.assertEqual(set(chatters["chatters"]), {"2"})
            self.assertEqual(
                [event["user_id"] for event in activity["events"]], ["2"]
            )

    def test_windows_release_assets_exist(self) -> None:
        root = Path(__file__).resolve().parent.parent
        self.assertTrue((root / "assets" / "sally-icon.ico").exists())
        metadata = root / "packaging" / "windows-version-info.txt"
        self.assertIn("0.1.0", metadata.read_text(encoding="utf-8"))
        self.assertTrue((root / "scripts" / "package_release.ps1").exists())


if __name__ == "__main__":
    unittest.main()
