from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QLineEdit
from PySide6.QtGui import QTextCursor

from products.hub.automation.models import TaskDefinition
from products.hub.automation.routines import RoutineStore
from products.hub.automation.variable_providers import context_provider, runtime_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.ui.automation_page import TaskEditorDialog


class FakeObsService:
    connected = True

    def send_request(self, request_type, request_data=None, callback=None):
        responses = {
            "GetSceneList": {"scenes": [{"sceneName": "Gameplay"}, {"sceneName": "Starting Soon"}]},
            "GetInputList": {"inputs": [{"inputName": "Mic/Aux"}, {"inputName": "Music"}]},
            "GetHotkeyList": {"hotkeys": ["OBSBasic.StartStreaming"]},
            "GetSceneItemList": {"sceneItems": [{"sourceName": "Camera"}, {"sourceName": "Game Capture"}]},
        }
        if callback:
            callback(SimpleNamespace(succeeded=True, response_data=responses[request_type]))
        return "request-id"


class TaskEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def variables() -> VariableRegistry:
        registry = VariableRegistry()
        registry.register(context_provider())
        registry.register(
            runtime_provider(
                lambda: {"title": "Building Streamhouse", "category": "Science & Technology", "connected": True},
                obs_connected=lambda: True,
                obs_scene=lambda: "Gameplay",
                hub_uptime=lambda: "01:23:45",
            )
        )
        return registry

    def test_core_launch_form_round_trips_without_json(self) -> None:
        task = TaskDefinition(
            "launch",
            "core.launch_application",
            "Open OBS",
            {
                "executable": "C:/OBS/obs64.exe",
                "arguments": "--minimize-to-tray",
                "working_directory": "C:/OBS",
                "start_minimized": True,
                "only_if_not_running": True,
            },
        )
        dialog = TaskEditorDialog(task.task_type, task=task)
        values = dialog.values()
        self.assertEqual(values["config"], task.config)
        self.assertNotIn("configuration_edit", dialog.__dict__)
        self.assertNotIn("type_combo", dialog.__dict__)

    def test_obs_form_loads_live_editable_choices(self) -> None:
        dialog = TaskEditorDialog(
            "obs.set_scene_item_enabled",
            obs_service=FakeObsService(),
        )
        dialog._refresh_obs_choices()
        fields = dialog.field_widgets["obs.set_scene_item_enabled"]
        scene = fields["scene"]
        source = fields["source"]
        self.assertIsInstance(scene, QComboBox)
        self.assertTrue(scene.isEditable())
        self.assertGreaterEqual(scene.findText("Gameplay"), 0)
        scene.setCurrentText("Gameplay")
        dialog._refresh_obs_sources("Gameplay")
        self.assertGreaterEqual(source.findText("Camera"), 0)
        source.setCurrentText("Camera")
        fields["action"].setCurrentIndex(fields["action"].findData("hide"))
        config = dialog.values()["config"]
        self.assertEqual(config, {"scene": "Gameplay", "source": "Camera", "action": "hide"})

    def test_open_target_has_file_and_folder_picker_field(self) -> None:
        dialog = TaskEditorDialog("core.open_target")
        target = dialog.field_widgets["core.open_target"]["target"]
        self.assertIsInstance(target, QLineEdit)
        target.setText("https://twitch.tv")
        self.assertEqual(dialog.values()["config"]["target"], "https://twitch.tv")

    def test_play_audio_form_exposes_volume_and_wait_controls(self) -> None:
        dialog = TaskEditorDialog("core.play_audio")
        fields = dialog.field_widgets["core.play_audio"]
        fields["file"].setText("C:/Sounds/hello.mp3")
        fields["volume"].setValue(42)

        self.assertIsInstance(fields["wait_for_completion"], QCheckBox)
        self.assertFalse(fields["timeout_seconds"].isEnabled())
        fields["wait_for_completion"].setChecked(True)
        self.assertTrue(fields["timeout_seconds"].isEnabled())
        config = dialog.values()["config"]
        self.assertEqual(config["file"], "C:/Sounds/hello.mp3")
        self.assertEqual(config["volume"], 42)
        self.assertTrue(config["wait_for_completion"])
        self.assertFalse(hasattr(dialog, "variable_table"))
        self.assertFalse(
            any(
                "templates support variables" in label.text()
                for label in dialog.findChildren(QLabel)
            )
        )
        self.assertEqual(dialog.test_audio_button.text(), "Test Audio")

    def test_audio_preview_reports_an_invalid_file_without_saving(self) -> None:
        dialog = TaskEditorDialog("core.play_audio")
        fields = dialog.field_widgets["core.play_audio"]
        fields["file"].setText("C:/Sounds/does-not-exist.ogg")
        fields["volume"].setValue(35)

        dialog.test_audio_button.click()

        self.assertIn("Audio file was not found", dialog.audio_test_status.text())
        self.assertEqual(dialog.audio_test_status.property("state"), "error")

    def test_run_routine_form_lists_existing_routines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RoutineStore(Path(directory) / "routines.json")
            target = store.add("Reusable greeting")
            dialog = TaskEditorDialog("core.run_routine", routine_store=store)
            routine = dialog.field_widgets["core.run_routine"]["routine_id"]

            self.assertIsInstance(routine, QComboBox)
            self.assertEqual(dialog.name_edit.text(), "Run routine")
            self.assertGreaterEqual(routine.findData(target.routine_id), 0)
            routine.setCurrentIndex(routine.findData(target.routine_id))
            self.assertEqual(
                dialog.values()["config"]["routine_id"],
                target.routine_id,
            )

    def test_custom_variable_value_has_template_help(self) -> None:
        dialog = TaskEditorDialog(
            "core.create_routine_variable",
            variables={"user.display_name": "TestViewer"},
            variable_registry=self.variables(),
        )

        rows = {
            dialog.variable_table.item(row, 0).text(): row
            for row in range(dialog.variable_table.rowCount())
        }
        self.assertIn("{user.display_name}", rows)
        self.assertEqual(
            dialog.variable_table.item(rows["{user.display_name}"], 1).text(),
            "Twitch Context",
        )

    def test_if_else_form_disables_value_for_unary_comparison(self) -> None:
        dialog = TaskEditorDialog("core.logic_if_else")
        fields = dialog.field_widgets["core.logic_if_else"]
        operator = fields["operator"]
        operator.setCurrentIndex(operator.findData("is_true"))

        self.assertFalse(fields["right"].isEnabled())
        self.assertEqual(dialog.name_edit.text(), "If / Else")

    def test_switch_form_round_trips_case_routines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RoutineStore(Path(directory) / "routines.json")
            target = store.add("Hydrate response")
            dialog = TaskEditorDialog("core.logic_switch", routine_store=store)
            fields = dialog.field_widgets["core.logic_switch"]
            fields["input"].setText("{event.reward}")
            cases = fields["cases"]
            cases.add_case("Hydrate", target.routine_id)

            config = dialog.values()["config"]

            self.assertEqual(config["cases"], {"Hydrate": target.routine_id})

    def test_random_choice_form_round_trips_weighted_routines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RoutineStore(Path(directory) / "routines.json")
            target = store.add("Play rare sound")
            dialog = TaskEditorDialog("core.logic_random_choice", routine_store=store)
            choices = dialog.field_widgets["core.logic_random_choice"]["choices"]
            choices.add_choice("Rare", 2.5, target.routine_id)

            config = dialog.values()["config"]

            self.assertEqual(
                config["choices"],
                [{"label": "Rare", "weight": 2.5, "routine_id": target.routine_id}],
            )

    def test_file_task_forms_expose_clear_options_without_json(self) -> None:
        read_dialog = TaskEditorDialog("core.file_random_line")
        read_fields = read_dialog.field_widgets["core.file_random_line"]
        read_fields["path"].setText("C:/Sally/responses.txt")
        read_fields["variable"].setText("sally_response")

        read_config = read_dialog.values()["config"]

        self.assertEqual(read_config["path"], "C:/Sally/responses.txt")
        self.assertEqual(read_config["variable"], "sally_response")
        self.assertTrue(read_config["ignore_blank_lines"])
        self.assertTrue(read_config["stop_on_failure"])

        read_fields["variable"].setText("random_line")
        self.assertEqual(
            read_dialog.values()["config"]["variable"],
            "random_line",
        )

        write_dialog = TaskEditorDialog("core.file_write")
        write_fields = write_dialog.field_widgets["core.file_write"]
        write_fields["path"].setText("C:/Sally/activity.txt")
        write_fields["text"].setPlainText("{user.display_name} redeemed {event.reward}")
        write_fields["mode"].setCurrentIndex(
            write_fields["mode"].findData("overwrite")
        )

        write_config = write_dialog.values()["config"]

        self.assertEqual(write_config["mode"], "overwrite")
        self.assertEqual(write_config["text"], "{user.display_name} redeemed {event.reward}")

    def test_trigger_variable_can_be_inserted_and_previewed(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.send_chat_message",
            variables={"user.display_name": "TestViewer", "stream.channel": "samplechannel"},
            variable_registry=self.variables(),
        )
        message = dialog.field_widgets["twitch.send_chat_message"]["message"]
        message.setPlainText("Hello ")
        message.moveCursor(QTextCursor.MoveOperation.End)
        display_row = next(
            row for row in range(dialog.variable_table.rowCount())
            if dialog.variable_table.item(row, 0).text() == "{user.display_name}"
        )
        dialog.variable_table.selectRow(display_row)

        dialog._insert_selected_variable()

        self.assertEqual(message.toPlainText(), "Hello {user.display_name}")
        self.assertIn("Hello TestViewer", dialog.variable_preview_label.text())

    def test_variable_reference_shows_sources_and_sample_runtime_values(self) -> None:
        dialog = TaskEditorDialog("twitch.send_chat_message", variable_registry=self.variables())
        message = dialog.field_widgets["twitch.send_chat_message"]["message"]
        message.setPlainText("Scene is {obs.current_scene} while playing {stream.category}.")

        rows = {
            dialog.variable_table.item(row, 0).text(): row
            for row in range(dialog.variable_table.rowCount())
        }

        muted_row = rows["{obs.current_scene}"]
        game_row = rows["{stream.category}"]
        self.assertEqual(
            dialog.variable_table.item(muted_row, 1).text(),
            "OBS",
        )
        self.assertEqual(
            dialog.variable_table.item(game_row, 1).text(),
            "Twitch",
        )
        self.assertIn(
            "Scene is Gameplay while playing Science & Technology.",
            dialog.variable_preview_label.text(),
        )

    def test_twitch_information_outputs_have_friendly_insertable_labels(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.send_chat_message",
            variables={"automation.stream_title": "Building Streamhouse"},
            variable_registry=self.variables(),
        )
        rows = {
            dialog.variable_table.item(row, 0).text(): row
            for row in range(dialog.variable_table.rowCount())
        }
        expected_sources = {
            "{stream.title}": "Twitch",
            "{user.display_name}": "Twitch Context",
            "{obs.current_scene}": "OBS",
        }
        for variable, source in expected_sources.items():
            self.assertIn(variable, rows)
            self.assertEqual(
                dialog.variable_table.item(rows[variable], 1).text(),
                source,
            )

    def test_python_script_form_exposes_safe_execution_options(self) -> None:
        dialog = TaskEditorDialog(
            "core.run_python_script",
            variables={"user.display_name": "TestViewer"},
            variable_registry=self.variables(),
        )
        fields = dialog.field_widgets["core.run_python_script"]
        fields["script"].setText("C:/Scripts/welcome.py")
        fields["arguments"].setText('--user "{user.display_name}"')

        self.assertIsInstance(fields["wait_for_completion"], QCheckBox)
        self.assertIsNotNone(dialog.findChild(QLabel, "pythonScriptWarning"))
        self.assertTrue(fields["timeout_seconds"].isEnabled())
        fields["wait_for_completion"].setChecked(False)
        self.assertFalse(fields["timeout_seconds"].isEnabled())
        self.assertFalse(fields["capture_output"].isEnabled())
        self.assertFalse(fields["stop_on_failure"].isEnabled())
        config = dialog.values()["config"]
        self.assertEqual(config["script"], "C:/Scripts/welcome.py")
        self.assertEqual(config["arguments"], '--user "{user.display_name}"')
        self.assertFalse(config["wait_for_completion"])


if __name__ == "__main__":
    unittest.main()
