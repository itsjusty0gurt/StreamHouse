from __future__ import annotations

import unittest
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLabel, QLineEdit
from PySide6.QtGui import QTextCursor

from automation.models import TaskDefinition
from ui.automation_page import TaskEditorDialog


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

    def test_trigger_variable_can_be_inserted_and_previewed(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.send_chat_message",
            variables={"user": "TestViewer", "channel": "samplechannel"},
        )
        message = dialog.field_widgets["twitch.send_chat_message"]["message"]
        message.setPlainText("Hello ")
        message.moveCursor(QTextCursor.MoveOperation.End)
        dialog.variable_table.selectRow(0)

        dialog._insert_selected_variable()

        self.assertEqual(message.toPlainText(), "Hello {user}")
        self.assertIn("Hello TestViewer", dialog.variable_preview_label.text())

    def test_variable_reference_shows_sources_and_sample_runtime_values(self) -> None:
        dialog = TaskEditorDialog("twitch.send_chat_message")
        message = dialog.field_widgets["twitch.send_chat_message"]["message"]
        message.setPlainText("Mic is {muted} while playing {game}.")

        rows = {
            dialog.variable_table.item(row, 0).text(): row
            for row in range(dialog.variable_table.rowCount())
        }

        muted_row = rows["{muted}"]
        game_row = rows["{game}"]
        self.assertEqual(
            dialog.variable_table.item(muted_row, 1).text(),
            "OBS runtime lookup",
        )
        self.assertEqual(
            dialog.variable_table.item(game_row, 1).text(),
            "Twitch live context",
        )
        self.assertIn(
            "Mic is Not Muted while playing Science & Technology.",
            dialog.variable_preview_label.text(),
        )

    def test_python_script_form_exposes_safe_execution_options(self) -> None:
        dialog = TaskEditorDialog(
            "core.run_python_script",
            variables={"user": "TestViewer"},
        )
        fields = dialog.field_widgets["core.run_python_script"]
        fields["script"].setText("C:/Scripts/welcome.py")
        fields["arguments"].setText('--user "{user}"')

        self.assertIsInstance(fields["wait_for_completion"], QCheckBox)
        self.assertIsNotNone(dialog.findChild(QLabel, "pythonScriptWarning"))
        self.assertTrue(fields["timeout_seconds"].isEnabled())
        fields["wait_for_completion"].setChecked(False)
        self.assertFalse(fields["timeout_seconds"].isEnabled())
        self.assertFalse(fields["capture_output"].isEnabled())
        self.assertFalse(fields["stop_on_failure"].isEnabled())
        config = dialog.values()["config"]
        self.assertEqual(config["script"], "C:/Scripts/welcome.py")
        self.assertEqual(config["arguments"], '--user "{user}"')
        self.assertFalse(config["wait_for_completion"])


if __name__ == "__main__":
    unittest.main()
