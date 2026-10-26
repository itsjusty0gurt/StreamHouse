from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
)
from PySide6.QtGui import QTextCursor

from products.hub.automation.models import TaskDefinition
from products.hub.automation.routines import RoutineStore
from products.hub.automation.variable_providers import context_provider, runtime_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.automation.variable_outputs import generated_output_definitions
from products.hub.ui.automation_page import (
    OBS_SCREENSHOT_JPEG_FILTER,
    OBS_SCREENSHOT_PNG_FILTER,
    TaskEditorDialog,
)
from products.hub.ui.variable_picker import VariablePickerDialog


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class FakeObsService:
    def __init__(
        self,
        *,
        connected: bool = True,
        failures: set[str] | None = None,
        deferred: set[str] | None = None,
    ) -> None:
        self.connected = connected
        self.failures = set(failures or ())
        self.deferred = set(deferred or ())
        self.requests: list[tuple[str, dict[str, object]]] = []
        self.pending: list[tuple[str, object, object]] = []
        self.state_changed = FakeSignal()

    def send_request(self, request_type, request_data=None, callback=None):
        data = dict(request_data or {})
        self.requests.append((request_type, data))
        source_filters = {
            "Mic/Aux": ["Compressor", "Noise Suppression"],
            "Music": ["Limiter"],
            "Gameplay": ["Scene Color"],
            "Starting Soon": [],
        }
        responses = {
            "GetSceneList": {"scenes": [{"sceneName": "Gameplay"}, {"sceneName": "Starting Soon"}]},
            "GetInputList": {"inputs": [{"inputName": "Mic/Aux"}, {"inputName": "Music"}]},
            "GetHotkeyList": {"hotkeys": ["OBSBasic.StartStreaming"]},
            "GetSceneTransitionList": {
                "transitions": [
                    {"transitionName": "Cut"},
                    {"transitionName": "Fade"},
                ]
            },
            "GetSceneItemList": {"sceneItems": [{"sourceName": "Camera"}, {"sourceName": "Game Capture"}]},
            "GetSourceFilterList": {
                "filters": [
                    {"filterName": name}
                    for name in source_filters.get(str(data.get("sourceName", "")), [])
                ]
            },
        }
        result = SimpleNamespace(
            succeeded=request_type not in self.failures,
            comment="Discovery rejected" if request_type in self.failures else "",
            response_data=responses[request_type],
        )
        if callback:
            if request_type in self.deferred:
                self.pending.append((request_type, callback, result))
            else:
                callback(result)
        return "request-id"

    def complete_next(self, request_type: str) -> None:
        index = next(
            index
            for index, (pending_type, _callback, _result) in enumerate(self.pending)
            if pending_type == request_type
        )
        _pending_type, callback, result = self.pending.pop(index)
        callback(result)


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
        service = FakeObsService()
        dialog = TaskEditorDialog(
            "obs.set_scene_item_enabled",
            obs_service=service,
        )
        dialog._refresh_obs_choices()
        self.assertEqual([kind for kind, _data in service.requests], ["GetSceneList"])
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

    def test_obs_discovery_is_contextual_to_the_task(self) -> None:
        cases = {
            "obs.set_input_mute": "GetInputList",
            "obs.set_browser_source_url": "GetInputList",
            "obs.set_media_source_file": "GetInputList",
            "obs.set_source_audio_track": "GetInputList",
            "obs.set_color_source_color": "GetInputList",
            "obs.restart_media_source": "GetInputList",
            "obs.take_screenshot": "GetInputList",
            "obs.set_program_scene": "GetSceneList",
            "obs.set_source_transform": "GetSceneList",
            "obs.set_transition": "GetSceneTransitionList",
            "obs.trigger_hotkey": "GetHotkeyList",
        }
        for task_type, expected_request in cases.items():
            with self.subTest(task_type=task_type):
                service = FakeObsService()
                dialog = TaskEditorDialog(task_type, obs_service=service)
                dialog._refresh_obs_choices()

                self.assertEqual(
                    [kind for kind, _data in service.requests],
                    [expected_request],
                )
                expected_status = (
                    "Select an OBS scene to load sources."
                    if task_type == "obs.set_source_transform"
                    else ""
                )
                self.assertEqual(dialog.obs_choices_status.text(), expected_status)

    def test_browser_source_url_form_supports_canonical_variables(self) -> None:
        dialog = TaskEditorDialog("obs.set_browser_source_url")
        fields = dialog.field_widgets["obs.set_browser_source_url"]

        self.assertEqual(fields["url"].text(), "{automation.clip_embed_url}")
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["obs.set_browser_source_url"],
            ("url",),
        )
        self.assertEqual(
            TaskEditorDialog.OBS_PRIMARY_DISCOVERY["obs.set_browser_source_url"],
            "obs_input",
        )

    def test_obs_batch_one_forms_and_transition_discovery(self) -> None:
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["obs.set_media_source_file"],
            ("file",),
        )
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["obs.set_color_source_color"],
            ("color",),
        )
        service = FakeObsService()
        dialog = TaskEditorDialog("obs.set_transition", obs_service=service)
        dialog._refresh_obs_choices()
        transition = dialog.field_widgets["obs.set_transition"]["transition"]
        self.assertIsInstance(transition, QComboBox)
        self.assertGreaterEqual(transition.findText("Fade"), 0)

        audio = TaskEditorDialog("obs.set_source_audio_track")
        fields = audio.field_widgets["obs.set_source_audio_track"]
        self.assertEqual(fields["track"].count(), 6)
        self.assertEqual(fields["action"].count(), 3)
        self.assertEqual(audio.LABELS["obs.set_source_audio_track"], "OBS — Set Source Audio Track Routing")
        self.assertEqual(
            [fields["action"].itemText(index) for index in range(3)],
            ["Enable on track", "Disable on track", "Toggle on track"],
        )

    def test_obs_batch_two_forms_support_variables_and_contextual_discovery(self) -> None:
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["obs.take_screenshot"],
            ("file",),
        )
        self.assertIn(
            "position_x",
            TaskEditorDialog.TEMPLATED_FIELDS["obs.set_source_transform"],
        )
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["obs.create_record_chapter"],
            ("title",),
        )

        service = FakeObsService()
        screenshot = TaskEditorDialog("obs.take_screenshot", obs_service=service)
        screenshot._refresh_obs_choices()
        target = screenshot.field_widgets["obs.take_screenshot"]["target"]
        self.assertIsInstance(target, QComboBox)
        self.assertGreaterEqual(target.findText("Mic/Aux"), 0)

        target_type = screenshot.field_widgets["obs.take_screenshot"]["target_type"]
        target_type.setCurrentIndex(target_type.findData("scene"))
        screenshot._refresh_obs_choices()
        self.assertGreaterEqual(target.findText("Gameplay"), 0)

        transform = TaskEditorDialog("obs.set_source_transform", obs_service=service)
        transform._refresh_obs_choices()
        fields = transform.field_widgets["obs.set_source_transform"]
        fields["scene"].setCurrentText("Gameplay")
        transform._refresh_obs_sources("Gameplay")
        self.assertGreaterEqual(fields["source"].findText("Camera"), 0)

    def test_source_transform_editor_preserves_usable_field_width_and_scrolls(self) -> None:
        task = TaskDefinition(
            "transform",
            "obs.set_source_transform",
            "Move camera",
            {
                "scene": "Gameplay",
                "source": "Camera",
                "position_x": "{automation.x}",
                "position_y": "240",
                "scale_x": "1.25",
                "scale_y": "1.25",
                "rotation": "15",
                "crop_top": "2",
                "crop_right": "4",
                "crop_bottom": "6",
                "crop_left": "8",
            },
        )
        dialog = TaskEditorDialog(
            task.task_type,
            task=task,
            obs_service=FakeObsService(),
        )
        scroll = dialog.findChild(QScrollArea, "sourceTransformScrollArea")
        self.assertIsNotNone(scroll)
        fields = dialog.field_widgets[task.task_type]
        for field in fields.values():
            self.assertGreaterEqual(
                field.minimumWidth(),
                TaskEditorDialog.SOURCE_TRANSFORM_FIELD_MINIMUM_WIDTH,
            )

        dialog.resize(760, 900)
        dialog.show()
        self.application.processEvents()
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)

        dialog.resize(dialog.minimumWidth(), 500)
        self.application.processEvents()
        self.assertGreater(scroll.horizontalScrollBar().maximum(), 0)
        for field in fields.values():
            self.assertGreaterEqual(
                field.width(),
                TaskEditorDialog.SOURCE_TRANSFORM_FIELD_MINIMUM_WIDTH,
            )
        self.assertEqual(dialog.values()["config"], task.config)
        dialog.close()

    def test_other_task_editor_does_not_gain_transform_scroll_area(self) -> None:
        dialog = TaskEditorDialog("obs.set_input_mute")

        self.assertIsNone(
            dialog.findChild(QScrollArea, "sourceTransformScrollArea")
        )
        self.assertEqual(dialog.minimumWidth(), 620)

    def test_screenshot_browse_uses_save_dialog_and_default_filename(self) -> None:
        dialog = TaskEditorDialog("obs.take_screenshot", obs_service=FakeObsService())
        output = dialog.field_widgets["obs.take_screenshot"]["file"]

        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")) as save:
            dialog._choose_screenshot_file(output)

        self.assertEqual(save.call_args.args[1], "Save OBS Screenshot")
        self.assertEqual(save.call_args.args[2], "screenshot.png")
        self.assertIn("*.png", save.call_args.args[3])
        self.assertIn("*.jpg *.jpeg", save.call_args.args[3])
        self.assertEqual(save.call_args.args[4], OBS_SCREENSHOT_PNG_FILTER)
        self.assertEqual(output.text(), "")

    def test_screenshot_save_dialog_normalizes_selected_extension(self) -> None:
        dialog = TaskEditorDialog("obs.take_screenshot", obs_service=FakeObsService())
        output = dialog.field_widgets["obs.take_screenshot"]["file"]
        cases = (
            ("", "C:/captures/new-shot", OBS_SCREENSHOT_PNG_FILTER, "C:/captures/new-shot.png"),
            ("C:/captures/shot.png", "C:/captures/shot.png", OBS_SCREENSHOT_JPEG_FILTER, "C:/captures/shot.jpg"),
            ("C:/captures/shot.png", "C:/captures/custom.jpeg", OBS_SCREENSHOT_PNG_FILTER, "C:/captures/custom.jpeg"),
            ("", "C:/captures/custom.gif", OBS_SCREENSHOT_PNG_FILTER, "C:/captures/custom.png"),
        )
        for original, selected, selected_filter, expected in cases:
            with self.subTest(selected=selected, selected_filter=selected_filter):
                output.setText(original)
                with patch.object(
                    QFileDialog,
                    "getSaveFileName",
                    return_value=(selected, selected_filter),
                ):
                    dialog._choose_screenshot_file(output)
                self.assertEqual(Path(output.text()), Path(expected))

    def test_screenshot_save_dialog_accepts_existing_file_and_cancel_preserves_value(self) -> None:
        dialog = TaskEditorDialog("obs.take_screenshot", obs_service=FakeObsService())
        output = dialog.field_widgets["obs.take_screenshot"]["file"]
        with tempfile.TemporaryDirectory() as directory:
            existing = Path(directory) / "existing.png"
            existing.write_bytes(b"existing")
            with patch.object(
                QFileDialog,
                "getSaveFileName",
                return_value=(str(existing), OBS_SCREENSHOT_PNG_FILTER),
            ):
                dialog._choose_screenshot_file(output)
            self.assertEqual(output.text(), str(existing))

        output.setText("C:/captures/keep.jpg")
        with patch.object(QFileDialog, "getSaveFileName", return_value=("", "")):
            dialog._choose_screenshot_file(output)
        self.assertEqual(output.text(), "C:/captures/keep.jpg")

    def test_screenshot_editor_uses_path_as_format_authority_and_supports_variables(self) -> None:
        task = TaskDefinition(
            "screenshot",
            "obs.take_screenshot",
            "Capture gameplay",
            {
                "target_type": "scene",
                "target": "Gameplay",
                "file": "C:/captures/gameplay.jpg",
            },
        )
        dialog = TaskEditorDialog(
            task.task_type,
            task=task,
            obs_service=FakeObsService(),
        )
        self.assertNotIn("image_format", dialog.field_widgets[task.task_type])
        self.assertEqual(dialog.values()["config"], task.config)

        fields = dialog.field_widgets[task.task_type]
        fields["file"].setText("{automation.screenshot_output}")
        self.assertEqual(
            dialog.values()["config"]["file"],
            "{automation.screenshot_output}",
        )

        fields["file"].setText("C:/captures/gameplay.webp")
        with self.assertRaisesRegex(ValueError, "PNG, JPG, or JPEG"):
            dialog.values()

    def test_non_screenshot_file_field_still_uses_open_dialog(self) -> None:
        dialog = TaskEditorDialog("obs.set_image_source", obs_service=FakeObsService())
        output = dialog.field_widgets["obs.set_image_source"]["file"]
        with (
            patch.object(
                QFileDialog,
                "getOpenFileName",
                return_value=("C:/images/overlay.png", "All Files (*)"),
            ) as open_file,
            patch.object(QFileDialog, "getSaveFileName") as save_file,
        ):
            dialog._choose_file(output)
        open_file.assert_called_once()
        save_file.assert_not_called()
        self.assertEqual(output.text(), "C:/images/overlay.png")

    def test_obs_task_without_discovery_hides_refresh_toolbar(self) -> None:
        service = FakeObsService()
        dialog = TaskEditorDialog("obs.stream_control", obs_service=service)

        dialog._refresh_obs_choices()

        self.assertTrue(dialog.refresh_obs_choices_button.isHidden())
        self.assertTrue(dialog.obs_choices_status.isHidden())
        self.assertEqual(service.requests, [])

    def test_obs_loading_status_is_specific_and_clears_after_completion(self) -> None:
        service = FakeObsService(deferred={"GetInputList"})
        dialog = TaskEditorDialog("obs.set_input_mute", obs_service=service)

        dialog._refresh_obs_choices()
        self.assertEqual(dialog.obs_choices_status.text(), "Loading OBS inputs…")

        service.complete_next("GetInputList")
        self.assertEqual(dialog.obs_choices_status.text(), "")

    def test_obs_discovery_failure_and_disconnect_resolve_loading_state(self) -> None:
        failed = FakeObsService(failures={"GetInputList"})
        dialog = TaskEditorDialog("obs.set_input_mute", obs_service=failed)
        dialog._refresh_obs_choices()
        self.assertIn("Discovery rejected", dialog.obs_choices_status.text())
        self.assertNotIn("Loading", dialog.obs_choices_status.text())

        saved = TaskDefinition(
            "mute",
            "obs.set_input_mute",
            "Mute mic",
            {"input": "Mic/Aux", "action": "mute"},
        )
        disconnected = TaskEditorDialog(
            saved.task_type,
            task=saved,
            obs_service=FakeObsService(connected=False),
        )
        disconnected._refresh_obs_choices()
        self.assertEqual(
            disconnected.field_widgets[saved.task_type]["input"].currentText(),
            "Mic/Aux",
        )
        self.assertIn("disconnected", disconnected.obs_choices_status.text())
        self.assertNotIn("Loading", disconnected.obs_choices_status.text())

        disconnected.obs_service.connected = True
        disconnected.obs_service.state_changed.emit(object(), "Connected")
        self.application.processEvents()
        self.assertEqual(
            [kind for kind, _data in disconnected.obs_service.requests],
            ["GetInputList"],
        )
        self.assertEqual(disconnected.obs_choices_status.text(), "")

    def test_source_filter_discovery_tracks_selected_source(self) -> None:
        task = TaskDefinition(
            "source-filter",
            "obs.set_source_filter_state",
            "Mic filter",
            {"source": "Mic/Aux", "filter": "Compressor", "action": "enable"},
        )
        service = FakeObsService()
        dialog = TaskEditorDialog(task.task_type, task=task, obs_service=service)

        dialog._refresh_obs_choices()

        fields = dialog.field_widgets[task.task_type]
        source = fields["source"]
        filters = fields["filter"]
        self.assertEqual(
            [kind for kind, _data in service.requests],
            ["GetInputList", "GetSourceFilterList"],
        )
        self.assertGreaterEqual(filters.findText("Noise Suppression"), 0)

        source.setCurrentText("Music")
        source.activated.emit(source.currentIndex())
        self.assertEqual(service.requests[-1][1], {"sourceName": "Music"})
        self.assertGreaterEqual(filters.findText("Limiter"), 0)
        self.assertEqual(filters.findText("Noise Suppression"), -1)

    def test_disconnected_source_filter_keeps_saved_values_editable(self) -> None:
        task = TaskDefinition(
            "source-filter",
            "obs.set_source_filter_state",
            "Mic filter",
            {"source": "Missing Mic", "filter": "Saved Filter", "action": "enable"},
        )
        dialog = TaskEditorDialog(
            task.task_type,
            task=task,
            obs_service=FakeObsService(connected=False),
        )
        dialog._refresh_obs_choices()
        fields = dialog.field_widgets[task.task_type]

        self.assertTrue(fields["source"].isEditable())
        self.assertTrue(fields["filter"].isEditable())
        self.assertEqual(fields["source"].currentText(), "Missing Mic")
        self.assertEqual(fields["filter"].currentText(), "Saved Filter")
        self.assertIn("disconnected", dialog.obs_choices_status.text())

    def test_scene_filter_discovery_handles_empty_and_failed_lists(self) -> None:
        task = TaskDefinition(
            "scene-filter",
            "obs.set_scene_filter_state",
            "Scene filter",
            {"scene": "Gameplay", "filter": "Scene Color", "action": "enable"},
        )
        service = FakeObsService()
        dialog = TaskEditorDialog(task.task_type, task=task, obs_service=service)
        dialog._refresh_obs_choices()
        fields = dialog.field_widgets[task.task_type]
        scene = fields["scene"]
        filters = fields["filter"]
        self.assertGreaterEqual(filters.findText("Scene Color"), 0)
        self.assertEqual(service.requests[-1][1], {"sourceName": "Gameplay"})

        scene.setCurrentText("Starting Soon")
        scene.activated.emit(scene.currentIndex())
        self.assertEqual(filters.count(), 0)
        self.assertEqual(filters.currentText(), "Scene Color")
        self.assertEqual(dialog.obs_choices_status.text(), "No OBS filters found.")

        service.failures.add("GetSourceFilterList")
        dialog._refresh_obs_filters("Gameplay")
        self.assertEqual(filters.count(), 0)
        self.assertIn("Discovery rejected", dialog.obs_choices_status.text())

    def test_late_filter_response_cannot_restore_stale_options(self) -> None:
        service = FakeObsService(deferred={"GetSourceFilterList"})
        dialog = TaskEditorDialog(
            "obs.set_source_filter_state",
            obs_service=service,
        )
        source = dialog.field_widgets[dialog.task_type]["source"]
        filters = dialog.field_widgets[dialog.task_type]["filter"]

        dialog._refresh_obs_choices()
        source.setCurrentText("Mic/Aux")
        dialog._refresh_obs_filters("Mic/Aux")
        source.setCurrentText("Music")
        dialog._refresh_obs_filters("Music")
        service.complete_next("GetSourceFilterList")
        self.assertEqual(filters.findText("Compressor"), -1)
        service.complete_next("GetSourceFilterList")
        self.assertGreaterEqual(filters.findText("Limiter"), 0)

    def test_open_target_has_file_and_folder_picker_field(self) -> None:
        dialog = TaskEditorDialog("core.open_target")
        target = dialog.field_widgets["core.open_target"]["target"]
        self.assertIsInstance(target, QLineEdit)
        target.setText("https://twitch.tv")
        self.assertEqual(dialog.values()["config"]["target"], "https://twitch.tv")

    def test_wait_form_accepts_variables_and_units(self) -> None:
        dialog = TaskEditorDialog(
            "core.wait",
            variables={"custom.overlay_delay": "2.5"},
            variable_registry=self.variables(),
        )
        fields = dialog.field_widgets["core.wait"]

        self.assertIsInstance(fields["duration"], QLineEdit)
        self.assertIsInstance(fields["unit"], QComboBox)
        self.assertEqual(fields["duration"].text(), "1")
        self.assertEqual(fields["unit"].currentData(), "seconds")

        fields["duration"].setText("{custom.overlay_delay}")
        fields["unit"].setCurrentIndex(fields["unit"].findData("minutes"))

        self.assertEqual(
            dialog.values()["config"],
            {"duration": "{custom.overlay_delay}", "unit": "minutes"},
        )
        self.assertIn("duration", dialog.variable_picker_buttons)
        self.assertIs(
            dialog.variable_picker_buttons["duration"].target,
            fields["duration"],
        )
        self.assertTrue(dialog.variable_picker_buttons["duration"].isEnabled())
        self.assertNotIn("unit", dialog.variable_picker_buttons)

    def test_every_declared_template_field_uses_the_shared_picker(self) -> None:
        registry = self.variables()
        for task_type, field_keys in TaskEditorDialog.TEMPLATED_FIELDS.items():
            with self.subTest(task_type=task_type):
                dialog = TaskEditorDialog(task_type, variable_registry=registry)
                self.assertEqual(
                    set(dialog.variable_picker_buttons),
                    set(field_keys),
                )
                for key in field_keys:
                    self.assertIs(
                        dialog.variable_picker_buttons[key].target,
                        dialog.field_widgets[task_type][key],
                    )
                dialog.close()

    def test_play_audio_form_exposes_volume_and_wait_controls(self) -> None:
        dialog = TaskEditorDialog("core.play_audio")
        fields = dialog.field_widgets["core.play_audio"]
        fields["file"].setText("C:/Sounds/hello.mp3")
        fields["volume"].setValue(42)

        self.assertIsInstance(fields["wait_for_completion"], QCheckBox)
        self.assertEqual(
            fields["wait_for_completion"].text(),
            "Wait until audio finishes",
        )
        self.assertFalse(fields["timeout_seconds"].isEnabled())
        fields["wait_for_completion"].setChecked(True)
        self.assertTrue(fields["timeout_seconds"].isEnabled())
        config = dialog.values()["config"]
        self.assertEqual(config["file"], "C:/Sounds/hello.mp3")
        self.assertEqual(config["volume"], 42)
        self.assertTrue(config["wait_for_completion"])
        self.assertEqual(dialog.variable_picker_buttons, {})
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

        self.assertIn("value", dialog.variable_picker_buttons)
        button = dialog.variable_picker_buttons["value"]
        picker = VariablePickerDialog(button.registry, button.context)
        picker.search_edit.setText("user.display_name")
        self.assertEqual(picker.selected_placeholder(), "{user.display_name}")
        self.assertEqual(picker.table.item(0, 1).text(), "TestViewer")

    def test_if_form_has_exact_operators_and_disables_right_for_unary(self) -> None:
        dialog = TaskEditorDialog("core.if")
        fields = dialog.field_widgets["core.if"]
        operator = fields["operator"]
        choices = [operator.itemData(index) for index in range(operator.count())]
        self.assertEqual(
            choices,
            [
                "equals",
                "not_equals",
                "contains",
                "not_contains",
                "starts_with",
                "ends_with",
                "greater_than",
                "greater_or_equal",
                "less_than",
                "less_or_equal",
                "is_empty",
                "is_not_empty",
                "user_in_group",
            ],
        )
        operator.setCurrentIndex(operator.findData("is_not_empty"))

        self.assertFalse(fields["right"].isEnabled())
        self.assertEqual(dialog.name_edit.text(), "If")
        self.assertEqual(dialog.then_tasks_editor.value(), [])
        self.assertEqual(dialog.else_tasks_editor.value(), [])

    def test_if_form_round_trips_owned_nested_tasks_and_reorders_them(self) -> None:
        first = TaskDefinition("first", "core.wait", "First", {"duration": "1"})
        second = TaskDefinition("second", "core.wait", "Second", {"duration": "2"})
        condition = TaskDefinition(
            "condition",
            "core.if",
            "If coffee",
            {"left": "coffee", "operator": "equals", "right": "coffee"},
            then_tasks=[first, second],
        )
        dialog = TaskEditorDialog("core.if", task=condition)
        dialog.then_tasks_editor.task_list.setCurrentRow(1)
        dialog.then_tasks_editor.move_selected(-1)

        values = dialog.values()

        self.assertEqual(
            [task.task_id for task in values["then_tasks"]],
            ["second", "first"],
        )
        self.assertEqual(values["else_tasks"], [])

    def test_if_branch_editor_adds_edits_and_deletes_children(self) -> None:
        dialog = TaskEditorDialog("core.if")
        editor = dialog.then_tasks_editor
        added = TaskDefinition("added", "core.wait", "Added")
        edited = TaskDefinition("added", "core.wait", "Edited")
        with patch(
            "products.hub.ui.automation_page.QInputDialog.getItem",
            return_value=("Wait", True),
        ), patch.object(editor, "_run_editor", return_value=added):
            editor.add_task()
        self.assertEqual([task.name for task in editor.value()], ["Added"])

        editor.task_list.setCurrentRow(0)
        with patch.object(editor, "_run_editor", return_value=edited):
            editor.edit_selected()
        self.assertEqual([task.name for task in editor.value()], ["Edited"])

        editor.task_list.setCurrentRow(0)
        editor.delete_selected()
        self.assertEqual(editor.value(), [])

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
        read_fields["path"].setText("C:/Streamhouse/responses.txt")
        read_fields["variable"].setText("command_response")

        read_config = read_dialog.values()["config"]

        self.assertEqual(read_config["path"], "C:/Streamhouse/responses.txt")
        self.assertEqual(read_config["variable"], "command_response")
        self.assertTrue(read_config["ignore_blank_lines"])
        self.assertTrue(read_config["stop_on_failure"])

        read_fields["variable"].setText("random_line")
        self.assertEqual(
            read_dialog.values()["config"]["variable"],
            "random_line",
        )
        self.assertEqual(
            read_dialog.findChild(QLabel, "generatedOutputPlaceholder").text(),
            "Generated placeholder: {automation.random_line}",
        )
        read_fields["variable"].setText("command.data")
        with self.assertRaisesRegex(ValueError, "Automation output names"):
            read_dialog.values()

        write_dialog = TaskEditorDialog("core.file_write")
        write_fields = write_dialog.field_widgets["core.file_write"]
        write_fields["path"].setText("C:/Streamhouse/activity.txt")
        write_fields["text"].setPlainText("{user.display_name} redeemed {event.reward}")
        write_fields["mode"].setCurrentIndex(
            write_fields["mode"].findData("overwrite")
        )

        write_config = write_dialog.values()["config"]

        self.assertEqual(write_config["mode"], "overwrite")
        self.assertEqual(write_config["text"], "{user.display_name} redeemed {event.reward}")

    def test_chat_editor_accepts_only_canonical_preceding_output(self) -> None:
        output_definitions = generated_output_definitions(
            "core.file_random_line",
            {"variable": "random_line"},
        )
        dialog = TaskEditorDialog(
            "twitch.send_chat_message",
            variable_registry=self.variables(),
            output_definitions=output_definitions,
        )
        message = dialog.field_widgets["twitch.send_chat_message"]["message"]
        message.setPlainText("{automation.random_line}")

        self.assertEqual(
            dialog.values()["config"]["message"],
            "{automation.random_line}",
        )
        self.assertEqual(
            tuple(
                definition.name
                for definition in dialog.variable_picker_buttons[
                    "message"
                ].extra_definitions
            ),
            ("automation.random_line",),
        )
        button = dialog.variable_picker_buttons["message"]
        picker = VariablePickerDialog(
            button.registry,
            button.context,
            extra_definitions=button.extra_definitions,
        )
        picker.search_edit.setText("automation.random_line")
        self.assertEqual(
            picker.selected_placeholder(),
            "{automation.random_line}",
        )
        self.assertEqual(
            picker.table.item(0, 5).text(),
            "Available only during the routine after its producing task runs.",
        )

        message.setPlainText("{random_line}")
        with self.assertRaisesRegex(ValueError, "Invalid canonical variable placeholder"):
            dialog.values()

    def test_chat_validation_failure_keeps_editor_open_and_preserves_form(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.send_chat_message",
            variable_registry=self.variables(),
        )
        fields = dialog.field_widgets["twitch.send_chat_message"]
        message = fields["message"]
        as_bot = fields["as_bot"]
        dialog.name_edit.setText("Custom greeting")
        message.setPlainText("Hello {user.not_a_real_variable}")
        as_bot.setChecked(False)
        message_identity = id(message)
        as_bot_identity = id(as_bot)
        dialog.show()
        self.application.processEvents()

        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()
            dialog.accept()

        self.assertTrue(dialog.isVisible())
        self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
        self.assertEqual(message.toPlainText(), "Hello {user.not_a_real_variable}")
        self.assertFalse(as_bot.isChecked())
        self.assertEqual(dialog.name_edit.text(), "Custom greeting")
        self.assertEqual(id(message), message_identity)
        self.assertEqual(id(as_bot), as_bot_identity)
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(warning.call_args.args[1], "Invalid Task")
        self.assertIn("user.not_a_real_variable", warning.call_args.args[2])

        message.setPlainText("Hello {user.display_name}")
        dialog.accept()

        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(
            dialog.values()["config"],
            {"message": "Hello {user.display_name}", "as_bot": False},
        )

    def test_cancel_after_chat_validation_failure_discards_unsaved_task_edits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RoutineStore(Path(directory) / "routines.json")
            routine = store.add("Greeting")
            original = store.add_task(
                routine.routine_id,
                task_type="twitch.send_chat_message",
                name="Original greeting",
                config={"message": "Hello {user.display_name}", "as_bot": True},
            )
            dialog = TaskEditorDialog(
                original.task_type,
                task=original,
                variable_registry=self.variables(),
            )
            fields = dialog.field_widgets[original.task_type]
            fields["message"].setPlainText("Broken {user.not_a_real_variable}")
            fields["as_bot"].setChecked(False)
            dialog.name_edit.setText("Unsaved name")
            dialog.show()

            with patch.object(QMessageBox, "warning"):
                dialog.accept()
            self.assertTrue(dialog.isVisible())
            self.assertEqual(
                store.get(routine.routine_id).tasks[0].config,
                {"message": "Hello {user.display_name}", "as_bot": True},
            )

            dialog.reject()

            self.assertEqual(dialog.result(), QDialog.DialogCode.Rejected)
            saved = store.get(routine.routine_id).tasks[0]
            self.assertEqual(saved.name, "Original greeting")
            self.assertEqual(
                saved.config,
                {"message": "Hello {user.display_name}", "as_bot": True},
            )

    def test_existing_task_updates_only_after_corrected_validation_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = RoutineStore(Path(directory) / "routines.json")
            routine = store.add("Greeting")
            original = store.add_task(
                routine.routine_id,
                task_type="twitch.send_chat_message",
                name="Original greeting",
                config={"message": "Hello {user.display_name}", "as_bot": True},
            )
            dialog = TaskEditorDialog(
                original.task_type,
                task=original,
                variable_registry=self.variables(),
            )
            fields = dialog.field_widgets[original.task_type]
            fields["message"].setPlainText("Broken {user.not_a_real_variable}")
            fields["as_bot"].setChecked(False)
            dialog.name_edit.setText("Updated greeting")

            with patch.object(QMessageBox, "warning"):
                dialog.accept()
            self.assertEqual(
                store.get(routine.routine_id).tasks[0].config,
                {"message": "Hello {user.display_name}", "as_bot": True},
            )

            fields["message"].setPlainText("Welcome {user.display_name}")
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            store.update_task(routine.routine_id, original.task_id, **dialog.values())

            saved = store.get(routine.routine_id).tasks[0]
            self.assertEqual(saved.name, "Updated greeting")
            self.assertEqual(
                saved.config,
                {"message": "Welcome {user.display_name}", "as_bot": False},
            )

    def test_shared_validation_keeps_non_twitch_task_editor_open(self) -> None:
        dialog = TaskEditorDialog("core.wait")
        fields = dialog.field_widgets["core.wait"]
        duration = fields["duration"]
        unit = fields["unit"]
        dialog.name_edit.setText("Wait later")
        duration.setText("")
        unit.setCurrentIndex(unit.findData("minutes"))
        dialog.enabled_check.setChecked(False)
        dialog.show()

        with patch.object(QMessageBox, "warning") as warning:
            dialog.accept()

        self.assertTrue(dialog.isVisible())
        self.assertEqual(duration.text(), "")
        self.assertEqual(unit.currentData(), "minutes")
        self.assertFalse(dialog.enabled_check.isChecked())
        self.assertIn("Duration is required", warning.call_args.args[2])

        duration.setText("5")
        dialog.accept()
        self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)

    def test_trigger_variable_is_inserted_at_the_current_cursor(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.send_chat_message",
            variables={"user.display_name": "TestViewer", "stream.channel": "samplechannel"},
            variable_registry=self.variables(),
        )
        message = dialog.field_widgets["twitch.send_chat_message"]["message"]
        message.setPlainText("Hello ")
        message.moveCursor(QTextCursor.MoveOperation.End)
        dialog.variable_picker_buttons["message"].insert_placeholder(
            "{user.display_name}"
        )

        self.assertEqual(message.toPlainText(), "Hello {user.display_name}")

    def test_unified_picker_shows_only_actual_runtime_values(self) -> None:
        dialog = TaskEditorDialog("twitch.send_chat_message", variable_registry=self.variables())
        button = dialog.variable_picker_buttons["message"]
        picker = VariablePickerDialog(
            button.registry,
            button.context,
            extra_definitions=button.extra_definitions,
        )

        rows = {
            picker.table.item(row, 0).text(): row
            for row in range(picker.table.rowCount())
        }

        muted_row = rows["{obs.current_scene}"]
        game_row = rows["{stream.category}"]
        self.assertEqual(
            picker.table.item(muted_row, 1).text(),
            "Gameplay",
        )
        self.assertEqual(
            picker.table.item(game_row, 1).text(),
            "Science & Technology",
        )
        user_row = rows["{user.display_name}"]
        self.assertEqual(
            picker.table.item(user_row, 1).text(),
            "Not currently available",
        )
        for placeholder in (
            "{user.name}",
            "{command.data}",
            "{keyword.message}",
        ):
            self.assertEqual(
                picker.table.item(rows[placeholder], 1).text(),
                "Not currently available",
            )
        definition = dialog.variable_registry.definition("user.display_name")
        self.assertIsNotNone(definition)
        self.assertEqual(definition.source, "Twitch Context")
        self.assertEqual(definition.data_type.value, "text")

    def test_shoutout_target_is_a_normal_variable_capable_text_field(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.shoutout_user",
            variable_registry=self.variables(),
        )
        target = dialog.field_widgets["twitch.shoutout_user"]["target"]

        self.assertIsInstance(target, QLineEdit)
        self.assertEqual(target.text(), "{command.data}")
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["twitch.shoutout_user"],
            ("target",),
        )
        self.assertIs(dialog.variable_picker_buttons["target"].target, target)

    def test_get_user_clip_editor_defaults_to_trigger_user_and_selection_mode(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.get_user_clip",
            variable_registry=self.variables(),
        )
        fields = dialog.field_widgets["twitch.get_user_clip"]

        self.assertEqual(fields["target"].text(), "{user.id}")
        self.assertEqual(fields["selection_mode"].currentData(), "random")
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["twitch.get_user_clip"],
            ("target",),
        )

    def test_get_user_info_editor_defaults_to_trigger_user(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.get_user_info",
            variable_registry=self.variables(),
        )
        target = dialog.field_widgets["twitch.get_user_info"]["target"]

        self.assertIsInstance(target, QLineEdit)
        self.assertEqual(target.text(), "{user.id}")
        self.assertEqual(
            TaskEditorDialog.TEMPLATED_FIELDS["twitch.get_user_info"],
            ("target",),
        )

    def test_twitch_information_outputs_have_friendly_insertable_labels(self) -> None:
        dialog = TaskEditorDialog(
            "twitch.send_chat_message",
            variables={"automation.stream_title": "Building Streamhouse"},
            variable_registry=self.variables(),
        )
        button = dialog.variable_picker_buttons["message"]
        picker = VariablePickerDialog(button.registry, button.context)
        rows = {
            picker.table.item(row, 0).text(): row
            for row in range(picker.table.rowCount())
        }
        expected_values = {
            "{stream.title}": "Building Streamhouse",
            "{user.display_name}": "Not currently available",
            "{obs.current_scene}": "Gameplay",
        }
        for variable, value in expected_values.items():
            self.assertIn(variable, rows)
            self.assertEqual(
                picker.table.item(rows[variable], 1).text(),
                value,
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
        helper_text = dialog.findChild(QLabel, "pythonScriptHelpers").text()
        self.assertIn('hub.set_output("song", value)', helper_text)
        self.assertIn("{automation.song}", helper_text)
        self.assertIn("hub.get_variable", helper_text)
        self.assertIn("hub.log", helper_text)
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
