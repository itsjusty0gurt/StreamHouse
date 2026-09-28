import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from products.hub.automation.routines import RoutineStore
from products.hub.core.backup import (
    BackupComponent,
    BackupManager,
    BackupPreset,
)
from products.hub.core.diagnostics import DiagnosticsService
from products.hub.core.settings import AppSettings, SettingsStore
from products.hub.twitch.chatter_history import (
    BACKUP_CHATTER_FIELDS,
    ChatterHistoryStore,
)
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.ui.controllers.release_controller import ReleaseController


class ReleaseToolsTests(unittest.TestCase):
    def test_render_blueprint_defines_the_modern_relay_service(self) -> None:
        root = Path(__file__).resolve().parents[2]
        blueprint = root / "render.yaml"
        contents = blueprint.read_text(encoding="utf-8")

        self.assertTrue(blueprint.is_file())
        self.assertIn("name: streamhouse-soundboard-relay", contents)
        self.assertIn("key: STREAMHOUSE_RELAY_KEYS", contents)
        self.assertIn("key: STREAMHOUSE_RELAY_DB", contents)
        self.assertNotIn("key: SALLY_RELAY", contents)
        self.assertIn(
            "buildCommand: python -m compileall extensions/twitch/app",
            contents,
        )
        self.assertIn(
            "startCommand: python -m extensions.twitch.app.relay_server",
            contents,
        )
        self.assertFalse((root / "extensions" / "twitch" / "render.yaml").exists())

    def test_legacy_render_entry_point_forwards_to_current_relay(self) -> None:
        from extensions.twitch.app import relay_server as current
        from twitch_extension import relay_server as legacy

        self.assertIs(legacy.RelayHandler, current.RelayHandler)
        self.assertIs(legacy.RelayState, current.RelayState)
        self.assertIs(legacy.main, current.main)

    def test_backup_create_and_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "config" / "settings.json"
            SettingsStore(settings).save(AppSettings(startup_page="Automation"))
            manager = BackupManager(root, root / "backups")
            archive = manager.create(
                "manual",
                preset=BackupPreset.CUSTOM,
                components=[BackupComponent.HUB_SETTINGS],
            )
            self.assertEqual(archive.suffix, BackupManager.EXTENSION)
            with ZipFile(archive) as source:
                manifest = json.loads(source.read("manifest.json"))
            self.assertEqual(manifest["backup_type"], "manual")
            self.assertEqual(manifest["preset"], BackupPreset.CUSTOM.value)
            self.assertEqual(manifest["included_components"], ["hub_settings"])
            self.assertEqual(
                manifest["components"]["hub_settings"]["schema"],
                SettingsStore.VERSION,
            )
            SettingsStore(settings).save(AppSettings(startup_page="Logs"))

            report = manager.restore(
                archive,
                [BackupComponent.HUB_SETTINGS],
                create_safety=False,
            )

            self.assertEqual(report.restored_components, ("hub_settings",))
            restored = SettingsStore(settings).load()
            self.assertEqual(restored.startup_page, "Automation")

    def test_backup_packages_command_with_required_routine_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routine_store = RoutineStore(root / "automation" / "routines.json")
            command_store = TwitchCommandTriggerStore(
                root / "twitch" / "commands.json",
                routine_store,
            )
            command = command_store.add("hello", "Hello from Streamhouse")
            archive = BackupManager(root, root / "backups").create(
                "manual",
                preset=BackupPreset.CUSTOM,
                components=[BackupComponent.COMMANDS],
            )

            with ZipFile(archive) as source:
                self.assertEqual(
                    set(source.namelist()),
                    {"manifest.json", "components/commands.json"},
                )
                manifest = json.loads(source.read("manifest.json"))
                commands = json.loads(source.read("components/commands.json"))
            self.assertEqual(manifest["included_components"], ["commands"])
            self.assertEqual(
                manifest["components"]["commands"]["schema"],
                TwitchCommandTriggerStore.VERSION,
            )
            self.assertEqual(commands["schema"], TwitchCommandTriggerStore.VERSION)
            self.assertEqual(commands["triggers"][0]["trigger_id"], command.trigger_id)
            dependencies = commands["routine_dependencies"]
            self.assertEqual(dependencies["scope"], "commands")
            self.assertEqual(
                dependencies["routines"]["routines"][0]["routine_id"],
                command.routine_id,
            )

    def test_support_bundle_includes_only_sanitized_diagnostic_log_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            logs = root / "logs"
            logs.mkdir()
            current_log = logs / "StreamhouseHub-current.log"
            current_log.write_text(
                "[   INFO  ] user: private chat\n"
                "[ WARNING ] Authorization=secret-value\n",
                encoding="utf-8",
            )
            service = DiagnosticsService(root)
            service.set_state_provider(lambda: {"health": {"connection": "Connected"}})
            from shared.streamhouse_runtime.logger import Logger

            previous_log_path = Logger._session_log_path
            Logger._session_log_path = current_log
            try:
                destination = service.create_support_bundle(root / "support.zip")
            finally:
                Logger._session_log_path = previous_log_path
                service.clean_shutdown()

            with ZipFile(destination) as archive:
                warnings = archive.read("logs/current-session.log").decode()
                payload = json.loads(archive.read("diagnostics/state.json"))
            self.assertNotIn("private chat", warnings)
            self.assertNotIn("secret-value", warnings)
            self.assertEqual(payload["health"]["connection"], "Connected")

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
            store = ChatterHistoryStore(root / "memory" / "twitch_chatters.json")
            store.observe_message("1", "Viewer", user_login="viewer")
            store.set_manual_group("1", "Regulars")
            store.observe_message("2", "Other", user_login="other")
            store.records["1"].private_notes = "must never be archived"
            store.save()
            controller = ReleaseController(root)
            archive = controller.create_backup(BackupPreset.EVERYTHING_ELIGIBLE)

            with ZipFile(archive) as source:
                manifest = json.loads(source.read("manifest.json"))
                users_before = json.loads(source.read("components/users.json"))
            self.assertEqual(
                manifest["components"]["users"]["schema"],
                ChatterHistoryStore.VERSION,
            )
            self.assertEqual(set(users_before["chatters"]), {"1", "2"})
            self.assertEqual(
                users_before["chatters"]["1"]["manual_group"], "Regulars"
            )
            self.assertNotIn("must never be archived", json.dumps(users_before))
            forbidden_fields = {"private_notes", "message", "text", "evidence"}
            for record in users_before["chatters"].values():
                self.assertLessEqual(set(record), BACKUP_CHATTER_FIELDS)
                self.assertTrue(forbidden_fields.isdisjoint(record))

            self.assertEqual(controller.scrub_viewer_data("1"), 1)
            with ZipFile(archive) as source:
                users_after = json.loads(source.read("components/users.json"))
            self.assertEqual(set(users_after["chatters"]), {"2"})

    def test_windows_release_assets_exist(self) -> None:
        root = Path(__file__).resolve().parents[2]
        icon_root = root / "shared" / "assets" / "streamhouse-icons"
        self.assertTrue((icon_root / "streamhouse-hub.png").exists())
        self.assertTrue((icon_root / "streamhouse-ai.png").exists())
        self.assertTrue((icon_root / "streamhouse-brand.png").exists())
        self.assertTrue((icon_root / "windows" / "streamhouse-hub.ico").exists())
        self.assertTrue((icon_root / "windows" / "streamhouse-ai.ico").exists())
        hub_metadata = root / "tools" / "packaging" / "windows-hub-version-info.txt"
        ai_metadata = root / "tools" / "packaging" / "windows-ai-version-info.txt"
        self.assertIn("StreamhouseHub.exe", hub_metadata.read_text(encoding="utf-8"))
        self.assertIn("StreamhouseAI.exe", ai_metadata.read_text(encoding="utf-8"))
        self.assertTrue((root / "tools" / "release" / "package_all.ps1").exists())


if __name__ == "__main__":
    unittest.main()
