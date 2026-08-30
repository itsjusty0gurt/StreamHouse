from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_tasks import (
    DelayTask,
    DesktopNotificationTask,
    PlayAudioTask,
    RandomDelayTask,
    WaitForServiceTask,
)
from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.tasks import TaskRegistry
from products.hub.obs_service.models import ObsConnectionState, ObsEvent, ObsRequestResult
from products.hub.obs_service.config import ObsConnectionConfig
from products.hub.obs_service.service import ObsWebSocketService
from products.hub.obs_service.tasks import OBS_TASK_LABELS, register_obs_tasks
from products.hub.obs_service.triggers import ObsTriggerStore


class FakeObsService:
    def __init__(self) -> None:
        self.connected = True
        self.requests: list[tuple[str, dict[str, object]]] = []

    def send_request(self, request_type, request_data=None, callback=None):
        self.requests.append((request_type, request_data or {}))
        return "request-id"

    def set_scene_item_enabled(self, scene, source, action):
        self.requests.append(("resolved-scene-item", {"scene": scene, "source": source, "action": action}))
        return "request-id"

    def set_source_filter_enabled(self, source, filter_name, action):
        self.requests.append(("source-filter", {"source": source, "filter": filter_name, "action": action}))
        return "request-id"


class ObsServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_authentication_matches_obs_websocket_formula(self) -> None:
        password, salt, challenge = "secret", "salt-value", "challenge-value"
        secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
        expected = base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()
        self.assertEqual(ObsWebSocketService.authentication(password, salt, challenge), expected)

    def test_startup_auto_connect_defaults_off(self) -> None:
        self.assertFalse(ObsConnectionConfig().auto_connect)
        self.assertFalse(ObsConnectionConfig.from_dict({}).auto_connect)
        self.assertEqual(ObsConnectionConfig.from_dict({}).default_mute_input, "")
        self.assertEqual(
            ObsConnectionConfig.from_dict(
                {"default_mute_input": "  Mic/Aux  "}
            ).default_mute_input,
            "Mic/Aux",
        )

    def test_identified_message_marks_service_connected(self) -> None:
        service = ObsWebSocketService()
        service._receive_text('{"op":2,"d":{"negotiatedRpcVersion":1}}')
        self.assertEqual(service.state, ObsConnectionState.CONNECTED)
        self.assertTrue(service.connected)
        service.disconnect()

    def test_unexpected_disconnect_reports_waiting_without_connected_status(self) -> None:
        service = ObsWebSocketService()
        states: list[tuple[ObsConnectionState, str]] = []
        service.state_changed.connect(lambda state, detail: states.append((state, detail)))
        service.state = ObsConnectionState.CONNECTED
        service._identified = True
        service._intentional_close = False

        service._disconnected()

        self.assertEqual(service.state, ObsConnectionState.DISCONNECTED)
        self.assertEqual(states[-1], (ObsConnectionState.DISCONNECTED, "Waiting for OBS to open."))
        service.disconnect()

    def test_current_mute_state_queries_preferred_microphone(self) -> None:
        service = ObsWebSocketService()
        service.state = ObsConnectionState.CONNECTED
        service._identified = True
        service._request_sync = Mock(
            side_effect=(
                ObsRequestResult(
                    "list",
                    "GetInputList",
                    True,
                    100,
                    response_data={
                        "inputs": [
                            {
                                "inputName": "Desktop Audio",
                                "inputKind": "wasapi_output_capture",
                            },
                            {
                                "inputName": "Mic/Aux",
                                "inputKind": "wasapi_input_capture",
                            },
                        ]
                    },
                ),
                ObsRequestResult(
                    "mute",
                    "GetInputMute",
                    True,
                    100,
                    response_data={"inputMuted": True},
                ),
            )
        )

        self.assertEqual(service.current_mute_state(), ("Mic/Aux", True))
        self.assertEqual(
            service._request_sync.call_args_list[1].args,
            ("GetInputMute", {"inputName": "Mic/Aux"}),
        )
        service.disconnect()


class ObsTriggerStoreTests(unittest.TestCase):
    def test_round_trip_and_filter_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routines = RoutineStore(root / "automation" / "routines.json")
            routine = routines.add("Gameplay scene")
            store = ObsTriggerStore(root / "obs" / "triggers.json", routines)
            stored = store.add(routine.routine_id, "CurrentProgramSceneChanged", filters={"sceneName": "Gameplay"})
            loaded = ObsTriggerStore(store.path, routines)
            loaded.load()
            events = loaded.evaluate(ObsEvent("CurrentProgramSceneChanged", {"sceneName": "gameplay"}))
            self.assertEqual(events[0].trigger_id, stored.trigger_id)
            self.assertEqual(events[0].context["scene"], "gameplay")

    def test_unknown_event_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            routines = RoutineStore(Path(directory) / "routines.json")
            routine = routines.add("Unknown")
            store = ObsTriggerStore(Path(directory) / "obs.json", routines)
            with self.assertRaises(ValueError):
                store.add(routine.routine_id, "MadeUpEvent")

    def test_obsolete_or_unversioned_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routines = RoutineStore(root / "routines.json")
            store = ObsTriggerStore(root / "triggers.json", routines)
            for payload in (
                {"triggers": []},
                {"version": 0, "triggers": []},
                {"version": "1", "triggers": []},
            ):
                with self.subTest(payload=payload):
                    store.path.write_text(json.dumps(payload), encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "Unsupported OBS trigger"):
                        store.load()

    def test_current_schema_does_not_invent_missing_trigger_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routines = RoutineStore(root / "routines.json")
            routine = routines.add("OBS Connected")
            store = ObsTriggerStore(root / "triggers.json", routines)
            store.path.write_text(
                json.dumps(
                    {
                        "version": store.VERSION,
                        "triggers": [
                            {
                                "routine_id": routine.routine_id,
                                "event_type": "ConnectionOpened",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(store.load(), [])

    def test_mute_context_is_typed_and_canonicalizable(self) -> None:
        muted = ObsTriggerStore.context_for(
            ObsEvent("InputMuteStateChanged", {"inputMuted": True})
        )
        unmuted = ObsTriggerStore.context_for(
            ObsEvent("InputMuteStateChanged", {"inputMuted": False})
        )

        self.assertEqual(muted["muted"], "true")
        self.assertNotIn("mute", muted)
        self.assertEqual(unmuted["muted"], "false")
        self.assertNotIn("mute", unmuted)


class ObsTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeObsService()
        self.registry = TaskRegistry()
        register_obs_tasks(self.registry, self.service)
        self.trigger = TriggerEvent("manual", "test", "manual", {})

    def run_task(self, task_type: str, config: dict) -> bool:
        task = TaskDefinition("task", task_type, task_type, config)
        return self.registry.execute(task, self.trigger).succeeded

    def test_all_advertised_obs_tasks_are_registered(self) -> None:
        self.assertEqual(set(OBS_TASK_LABELS), set(self.registry.registered_types()))

    def test_scene_and_input_tasks_map_to_obs_requests(self) -> None:
        self.assertTrue(self.run_task("obs.set_program_scene", {"scene": "Gameplay"}))
        self.assertTrue(self.run_task("obs.set_input_mute", {"input": "Mic/Aux", "action": "toggle"}))
        self.assertEqual(self.service.requests[0][0], "SetCurrentProgramScene")
        self.assertEqual(self.service.requests[1][0], "ToggleInputMute")

    def test_source_visibility_uses_scene_item_resolution(self) -> None:
        self.assertTrue(self.run_task("obs.set_scene_item_enabled", {"scene": "Gameplay", "source": "Camera", "action": "hide"}))
        self.assertEqual(self.service.requests[0][0], "resolved-scene-item")

    def test_filter_tasks_use_source_filter_state_request(self) -> None:
        self.assertTrue(
            self.run_task(
                "obs.set_source_filter_state",
                {"source": "Camera", "filter": "Blur", "action": "enable"},
            )
        )
        self.assertTrue(
            self.run_task(
                "obs.set_scene_filter_state",
                {"scene": "Gameplay", "filter": "Color", "action": "toggle"},
            )
        )

        self.assertEqual(
            self.service.requests,
            [
                (
                    "source-filter",
                    {"source": "Camera", "filter": "Blur", "action": "enable"},
                ),
                (
                    "source-filter",
                    {"source": "Gameplay", "filter": "Color", "action": "toggle"},
                ),
            ],
        )

    def test_text_and_image_sources_use_overlay_input_settings(self) -> None:
        self.trigger = TriggerEvent(
            "manual",
            "test",
            "manual",
            {"stream.category": "Portal 2", "automation.image": "C:/art/portal.png"},
        )
        self.assertTrue(
            self.run_task(
                "obs.set_text_source",
                {"input": "Now Playing", "text": "Playing {stream.category}"},
            )
        )
        self.assertTrue(
            self.run_task(
                "obs.set_image_source",
                {"input": "Game Art", "file": "{automation.image}"},
            )
        )
        self.assertEqual(
            self.service.requests[0],
            (
                "SetInputSettings",
                {
                    "inputName": "Now Playing",
                    "inputSettings": {"text": "Playing Portal 2"},
                    "overlay": True,
                },
            ),
        )
        self.assertEqual(
            self.service.requests[1][1]["inputSettings"],
            {"file": "C:/art/portal.png"},
        )

    def test_raw_request_rejects_non_object_json(self) -> None:
        self.assertFalse(self.run_task("obs.raw_request", {"request_type": "GetVersion", "request_data": "[]"}))


class CoreTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_delay_and_immediate_service_wait(self) -> None:
        trigger = TriggerEvent("manual", "test", "manual", {})
        delay = TaskDefinition("delay", "core.delay", "Delay", {"seconds": 0})
        wait = TaskDefinition("wait", "core.wait_for_service", "Wait", {"service": "obs", "timeout_seconds": 1})
        self.assertTrue(DelayTask().execute(delay, trigger).succeeded)
        self.assertTrue(WaitForServiceTask(lambda name: name == "obs").execute(wait, trigger).succeeded)

    def test_random_delay_uses_a_duration_inside_the_configured_range(self) -> None:
        waited: list[float] = []
        rng = Mock()
        rng.uniform.return_value = 2.75
        task = TaskDefinition(
            "random-delay",
            "core.random_delay",
            "Random delay",
            {"minimum_seconds": 2, "maximum_seconds": 4},
        )

        result = RandomDelayTask(rng=rng, wait=waited.append).execute(
            task,
            TriggerEvent("manual", "test", "manual", {}),
        )

        self.assertTrue(result.succeeded)
        rng.uniform.assert_called_once_with(2.0, 4.0)
        self.assertEqual(waited, [2.75])

    def test_desktop_notification_renders_context_without_showing_ui(self) -> None:
        notifications: list[tuple[str, str, str, int]] = []

        def notify(title, message, icon, duration_ms):
            notifications.append((title, message, icon, duration_ms))
            return True

        task = TaskDefinition(
            "notification",
            "core.show_notification",
            "Notification",
            {
                "title": "Streamhouse alert for {user.display_name}",
                "message": "{user.display_name} redeemed {event.reward}",
                "icon": "warning",
                "duration_seconds": 8,
            },
        )
        trigger = TriggerEvent(
            "reward",
            "twitch",
            "channel_points",
            {"user.display_name": "Viewer", "event.reward": "Hydrate"},
        )

        result = DesktopNotificationTask(notifier=notify).execute(task, trigger)

        self.assertTrue(result.succeeded)
        self.assertEqual(
            notifications,
            [
                (
                    "Streamhouse alert for Viewer",
                    "Viewer redeemed Hydrate",
                    "warning",
                    8000,
                )
            ],
        )

    def test_play_audio_validates_file_and_starts_with_volume(self) -> None:
        class Signal:
            def connect(self, callback):
                self.callback = callback

        class FakePlayer:
            def __init__(self):
                self.playbackStateChanged = Signal()
                self.errorOccurred = Signal()
                self.played = False
                self.stopped = False
                self.source = None
                self.audio_output = None

            def setAudioOutput(self, output):
                self.audio_output = output

            def setSource(self, source):
                self.source = source

            def play(self):
                self.played = True

            def stop(self):
                self.stopped = True

        class FakeAudioOutput:
            def __init__(self):
                self.volume = None

            def setVolume(self, volume):
                self.volume = volume

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sound.mp3"
            path.write_bytes(b"fake")
            players: list[FakePlayer] = []
            outputs: list[FakeAudioOutput] = []

            def player_factory():
                player = FakePlayer()
                players.append(player)
                return player

            def output_factory():
                output = FakeAudioOutput()
                outputs.append(output)
                return output

            trigger = TriggerEvent("manual", "test", "manual", {})
            task = TaskDefinition(
                "audio",
                "core.play_audio",
                "Audio",
                {"file": str(path), "volume": 35},
            )

            audio_task = PlayAudioTask(
                player_factory=player_factory,
                audio_output_factory=output_factory,
            )
            result = audio_task.execute(task, trigger)
            audio_task.stop_all()

        self.assertTrue(result.succeeded)
        self.assertTrue(players[0].played)
        self.assertTrue(players[0].stopped)
        self.assertEqual(outputs[0].volume, 0.35)
        self.assertIn("Started audio", result.detail)

    def test_play_audio_rejects_unsupported_extension(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sound.txt"
            path.write_text("fake", encoding="utf-8")
            trigger = TriggerEvent("manual", "test", "manual", {})
            task = TaskDefinition(
                "audio",
                "core.play_audio",
                "Audio",
                {"file": str(path)},
            )

            result = PlayAudioTask().execute(task, trigger)

        self.assertFalse(result.succeeded)
        self.assertIn(".ogg, .mp3, or .wav", result.detail)


if __name__ == "__main__":
    unittest.main()
