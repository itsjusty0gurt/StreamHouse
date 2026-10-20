from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox

from products.hub.obs_service.triggers import ObsAutomationTrigger
from products.hub.ui.automation_page import ObsTriggerDialog


class FakeObsService:
    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected
        self.requests: list[tuple[str, dict[str, object]]] = []

    def send_request(self, request_type, request_data=None, callback=None):
        data = dict(request_data or {})
        self.requests.append((request_type, data))
        responses = {
            "GetSceneList": {
                "scenes": [
                    {"sceneName": "Gameplay"},
                    {"sceneName": "Starting Soon"},
                ]
            },
            "GetInputList": {
                "inputs": [
                    {"inputName": "Mic/Aux", "inputKind": "wasapi_input_capture"},
                    {"inputName": "Clip Player", "inputKind": "ffmpeg_source"},
                    {"inputName": "Playlist", "inputKind": "vlc_source"},
                ]
            },
            "GetSceneItemList": {
                "sceneItems": [
                    {"sourceName": "Camera", "sceneItemId": 7},
                    {"sourceName": "Game Capture", "sceneItemId": 12},
                ]
            },
        }
        result = SimpleNamespace(
            succeeded=True,
            response_data=responses[request_type],
        )
        if callback is not None:
            callback(result)
        return "request-id"


class ObsTriggerDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_input_mute_uses_live_input_and_supported_state_controls(self) -> None:
        service = FakeObsService()
        dialog = ObsTriggerDialog(
            obs_service=service,
            initial_event_type="InputMuteStateChanged",
        )

        self.assertEqual(set(dialog.filter_widgets), {"inputName", "inputMuted"})
        input_combo = dialog.filter_widgets["inputName"]
        state_combo = dialog.filter_widgets["inputMuted"]
        self.assertTrue(input_combo.isEditable())
        self.assertGreaterEqual(input_combo.findText("Mic/Aux"), 0)
        self.assertEqual(
            [state_combo.itemText(index) for index in range(state_combo.count())],
            ["Any", "Muted", "Unmuted"],
        )
        self.assertEqual(
            [state_combo.itemData(index) for index in range(state_combo.count())],
            ["", "true", "false"],
        )

        input_combo.setCurrentText("Mic/Aux")
        state_combo.setCurrentIndex(state_combo.findData("false"))
        self.assertEqual(
            dialog.values()["filters"],
            {"inputName": "Mic/Aux", "inputMuted": "false"},
        )

    def test_scene_trigger_has_only_editable_scene_selector(self) -> None:
        dialog = ObsTriggerDialog(
            obs_service=FakeObsService(),
            initial_event_type="CurrentProgramSceneChanged",
        )

        self.assertEqual(set(dialog.filter_widgets), {"sceneName"})
        scene = dialog.filter_widgets["sceneName"]
        self.assertTrue(scene.isEditable())
        self.assertGreaterEqual(scene.findText("Gameplay"), 0)
        scene.setCurrentIndex(0)
        self.assertEqual(dialog.values()["filters"], {})

    def test_source_visibility_resolves_scene_items_and_preserves_unknown_filters(self) -> None:
        trigger = ObsAutomationTrigger(
            "trigger-id",
            "routine-id",
            "SceneItemEnableStateChanged",
            {
                "sceneName": "Gameplay",
                "sceneItemId": "7",
                "sceneItemEnabled": "true",
                "sceneUuid": "saved-uuid",
            },
        )
        dialog = ObsTriggerDialog(
            trigger=trigger,
            obs_service=FakeObsService(),
        )

        self.assertEqual(
            set(dialog.filter_widgets),
            {"sceneName", "sceneItemId", "sceneItemEnabled"},
        )
        source = dialog.filter_widgets["sceneItemId"]
        self.assertEqual(source.currentText(), "Camera")
        self.assertEqual(source.currentData(), "7")
        self.assertTrue(dialog.advanced_toggle.isChecked())
        self.assertIn("sceneUuid=saved-uuid", dialog.filters_edit.text())
        self.assertEqual(dialog.values()["filters"], trigger.filters)

        dialog.filter_widgets["sceneItemEnabled"].setCurrentIndex(0)
        values = dialog.values()["filters"]
        self.assertNotIn("sceneItemEnabled", values)
        self.assertEqual(values["sceneUuid"], "saved-uuid")

    def test_media_events_offer_only_discovered_media_sources(self) -> None:
        for event_type in (
            "MediaInputPlaybackStarted",
            "MediaInputPlaybackEnded",
        ):
            with self.subTest(event_type=event_type):
                dialog = ObsTriggerDialog(
                    obs_service=FakeObsService(),
                    initial_event_type=event_type,
                )
                media = dialog.filter_widgets["inputName"]
                self.assertGreaterEqual(media.findText("Clip Player"), 0)
                self.assertGreaterEqual(media.findText("Playlist"), 0)
                self.assertEqual(media.findText("Mic/Aux"), -1)

    def test_output_and_boolean_state_filters_use_canonical_values(self) -> None:
        cases = {
            "StreamStateChanged": (
                "OBS_WEBSOCKET_OUTPUT_RECONNECTING",
                "OBS_WEBSOCKET_OUTPUT_RECONNECTED",
            ),
            "RecordStateChanged": (
                "OBS_WEBSOCKET_OUTPUT_PAUSED",
                "OBS_WEBSOCKET_OUTPUT_RESUMED",
            ),
            "ReplayBufferStateChanged": (
                "OBS_WEBSOCKET_OUTPUT_STARTED",
                "OBS_WEBSOCKET_OUTPUT_STOPPED",
            ),
            "StudioModeStateChanged": ("true", "false"),
        }
        for event_type, expected in cases.items():
            with self.subTest(event_type=event_type):
                dialog = ObsTriggerDialog(initial_event_type=event_type)
                state = next(iter(dialog.filter_widgets.values()))
                values = {
                    str(state.itemData(index)) for index in range(state.count())
                }
                self.assertIn("", values)
                self.assertTrue(set(expected).issubset(values))

    def test_offline_saved_values_remain_editable_and_round_trip(self) -> None:
        trigger = ObsAutomationTrigger(
            "trigger-id",
            "routine-id",
            "InputVolumeChanged",
            {"inputName": "Offline Input", "customField": "custom-value"},
        )
        dialog = ObsTriggerDialog(
            trigger=trigger,
            obs_service=FakeObsService(connected=False),
        )

        input_combo = dialog.filter_widgets["inputName"]
        self.assertTrue(input_combo.isEditable())
        self.assertEqual(input_combo.currentText(), "Offline Input")
        self.assertIn("disconnected", dialog.discovery_status.text().casefold())
        self.assertEqual(dialog.values()["filters"], trigger.filters)

    def test_future_state_value_is_preserved_as_advanced_filter(self) -> None:
        trigger = ObsAutomationTrigger(
            "trigger-id",
            "routine-id",
            "StreamStateChanged",
            {"outputState": "OBS_WEBSOCKET_OUTPUT_FUTURE"},
        )
        dialog = ObsTriggerDialog(trigger=trigger)

        state = dialog.filter_widgets["outputState"]
        self.assertEqual(state.currentData(), "")
        self.assertTrue(dialog.advanced_toggle.isChecked())
        self.assertEqual(dialog.values()["filters"], trigger.filters)

    def test_events_without_common_filters_remain_advanced_capable(self) -> None:
        dialog = ObsTriggerDialog(initial_event_type="ConnectionOpened")

        self.assertEqual(dialog.filter_widgets, {})
        self.assertTrue(dialog.filter_group.isHidden())
        dialog.advanced_toggle.setChecked(True)
        dialog.filters_edit.setText("negotiatedRpcVersion=1")
        self.assertEqual(
            dialog.values()["filters"],
            {"negotiatedRpcVersion": "1"},
        )

    def test_invalid_advanced_filter_keeps_dialog_open_with_clear_error(self) -> None:
        dialog = ObsTriggerDialog(initial_event_type="ConnectionOpened")
        dialog.advanced_toggle.setChecked(True)
        dialog.filters_edit.setText("not-a-key-value-filter")

        with patch(
            "products.hub.ui.automation_page.QMessageBox.warning"
        ) as warning:
            dialog._accept_values()

        self.assertEqual(dialog.result(), 0)
        self.assertEqual(dialog.filters_edit.text(), "not-a-key-value-filter")
        warning.assert_called_once()
        self.assertEqual(warning.call_args.args[1], "Invalid OBS Filters")


if __name__ == "__main__":
    unittest.main()
