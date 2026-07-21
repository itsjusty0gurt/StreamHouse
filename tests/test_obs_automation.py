from __future__ import annotations

import base64
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from automation.core_tasks import DelayTask, PlayAudioTask, WaitForServiceTask
from automation.models import TaskDefinition, TriggerEvent
from automation.routines import RoutineStore
from automation.tasks import TaskRegistry
from obs_service.models import ObsConnectionState, ObsEvent, ObsRequestResult
from obs_service.config import ObsConnectionConfig
from obs_service.service import ObsWebSocketService
from obs_service.tasks import OBS_TASK_LABELS, register_obs_tasks
from obs_service.triggers import ObsTriggerStore


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

    def test_mute_context_is_human_readable(self) -> None:
        muted = ObsTriggerStore.context_for(
            ObsEvent("InputMuteStateChanged", {"inputMuted": True})
        )
        unmuted = ObsTriggerStore.context_for(
            ObsEvent("InputMuteStateChanged", {"inputMuted": False})
        )

        self.assertEqual(muted["muted"], "Muted")
        self.assertEqual(muted["mute"], "Muted")
        self.assertEqual(unmuted["muted"], "Not Muted")
        self.assertEqual(unmuted["mute"], "Not Muted")


class ObsTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = FakeObsService()
        self.registry = TaskRegistry()
        register_obs_tasks(self.registry, self.service)
        self.trigger = TriggerEvent("manual", "sally", "manual", {})

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

    def test_raw_request_rejects_non_object_json(self) -> None:
        self.assertFalse(self.run_task("obs.raw_request", {"request_type": "GetVersion", "request_data": "[]"}))


class CoreTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_delay_and_immediate_service_wait(self) -> None:
        trigger = TriggerEvent("manual", "sally", "manual", {})
        delay = TaskDefinition("delay", "core.delay", "Delay", {"seconds": 0})
        wait = TaskDefinition("wait", "core.wait_for_service", "Wait", {"service": "obs", "timeout_seconds": 1})
        self.assertTrue(DelayTask().execute(delay, trigger).succeeded)
        self.assertTrue(WaitForServiceTask(lambda name: name == "obs").execute(wait, trigger).succeeded)

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

            trigger = TriggerEvent("manual", "sally", "manual", {})
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
            trigger = TriggerEvent("manual", "sally", "manual", {})
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
