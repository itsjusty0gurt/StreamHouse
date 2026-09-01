from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from products.hub.automation.core_tasks import PlayAudioTask
from products.hub.automation.models import (
    DEFAULT_AUTOMATION_QUEUE_ID,
    TaskDefinition,
    TaskExecutionResult,
)
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class FakeAudioOutput:
    def __init__(self) -> None:
        self.volume = 0.0

    def setVolume(self, volume: float) -> None:
        self.volume = volume


class FakePlayer:
    def __init__(self, name: str, outcome: str, delay_ms: int, events: list[str]) -> None:
        self.name = name
        self.outcome = outcome
        self.delay_ms = delay_ms
        self.events = events
        self.playbackStateChanged = FakeSignal()
        self.mediaStatusChanged = FakeSignal()
        self.errorOccurred = FakeSignal()
        self._media_status = QMediaPlayer.MediaStatus.LoadedMedia
        self.stopped = False
        self.source = None
        self.audio_output = None

    def setAudioOutput(self, output) -> None:
        self.audio_output = output

    def setSource(self, source) -> None:
        self.source = source

    def mediaStatus(self):
        return self._media_status

    def errorString(self) -> str:
        return "Decoder failed" if self.outcome == "error" else ""

    def play(self) -> None:
        self.events.append(f"play:{self.name}")
        if self.outcome == "complete":
            QTimer.singleShot(self.delay_ms, self._complete)
        elif self.outcome == "error":
            QTimer.singleShot(
                self.delay_ms,
                lambda: self.errorOccurred.emit(None, "Decoder failed"),
            )

    def _complete(self) -> None:
        self.events.append(f"complete:{self.name}")
        self._media_status = QMediaPlayer.MediaStatus.EndOfMedia
        self.mediaStatusChanged.emit(self._media_status)
        self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.StoppedState)

    def stop(self) -> None:
        self.stopped = True
        self.events.append(f"stop:{self.name}")
        self.playbackStateChanged.emit(QMediaPlayer.PlaybackState.StoppedState)


class PlannedPlayerFactory:
    def __init__(self, plans: list[tuple[str, str, int]], events: list[str]) -> None:
        self.plans = plans
        self.events = events
        self.players: list[FakePlayer] = []

    def __call__(self) -> FakePlayer:
        name, outcome, delay_ms = self.plans.pop(0)
        player = FakePlayer(name, outcome, delay_ms, self.events)
        self.players.append(player)
        return player


class MarkerTask:
    task_type = "test.marker"

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def execute(self, task, trigger) -> TaskExecutionResult:
        self.events.append(f"marker:{task.name}")
        return TaskExecutionResult(task.task_id, task.task_type, True, "Marked.")


class AudioPlaybackTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.audio_file = self.root / "sound.mp3"
        self.audio_file.write_bytes(b"fake audio")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def audio_task(self, wait: bool, *, timeout: float = 1.0) -> TaskDefinition:
        return TaskDefinition(
            "audio",
            "core.play_audio",
            "Audio",
            {
                "file": str(self.audio_file),
                "volume": 35,
                "wait_for_completion": wait,
                "timeout_seconds": timeout,
            },
        )

    def service_with_audio(
        self,
        factory: PlannedPlayerFactory,
        events: list[str],
    ) -> tuple[AutomationService, RoutineStore, AutomationQueueManager]:
        store = RoutineStore(self.root / "routines.json")
        manager = AutomationQueueManager(AutomationQueueStore(self.root / "queues.json"))
        registry = TaskRegistry()
        registry.register(
            PlayAudioTask(
                player_factory=factory,
                audio_output_factory=FakeAudioOutput,
            )
        )
        registry.register(MarkerTask(events))
        return AutomationService(store, registry, queue_manager=manager), store, manager

    def test_unchecked_playback_continues_immediately(self) -> None:
        events: list[str] = []
        factory = PlannedPlayerFactory([("background", "complete", 50)], events)
        service, store, _manager = self.service_with_audio(factory, events)
        routine = store.add("Immediate audio")
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Play",
            config=self.audio_task(False).config,
        )
        store.add_task(routine.routine_id, task_type="test.marker", name="next")

        result = service.run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(events, ["play:background", "marker:next"])
        QTest.qWait(150)
        self.assertIn("complete:background", events)

    def test_checked_playback_waits_for_its_own_completion_and_keeps_ui_responsive(self) -> None:
        events: list[str] = []
        factory = PlannedPlayerFactory([("waited", "complete", 35)], events)
        service, store, _manager = self.service_with_audio(factory, events)
        routine = store.add("Waited audio")
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Play",
            config=self.audio_task(True).config,
        )
        store.add_task(routine.routine_id, task_type="test.marker", name="next")
        QTimer.singleShot(5, lambda: events.append("ui:event"))

        result = service.run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(
            events,
            ["play:waited", "ui:event", "complete:waited", "marker:next"],
        )

    def test_other_sound_completion_does_not_release_waiter(self) -> None:
        events: list[str] = []
        factory = PlannedPlayerFactory(
            [
                ("background", "complete", 10),
                ("waited", "complete", 50),
            ],
            events,
        )
        service, store, _manager = self.service_with_audio(factory, events)
        routine = store.add("Overlapping audio")
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Background",
            config=self.audio_task(False).config,
        )
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Waited",
            config=self.audio_task(True).config,
        )
        store.add_task(routine.routine_id, task_type="test.marker", name="next")

        result = service.run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        self.assertLess(events.index("complete:background"), events.index("complete:waited"))
        self.assertLess(events.index("complete:waited"), events.index("marker:next"))

    def test_waiting_playback_failure_stops_routine(self) -> None:
        events: list[str] = []
        factory = PlannedPlayerFactory([("broken", "error", 5)], events)
        service, store, _manager = self.service_with_audio(factory, events)
        routine = store.add("Broken audio")
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Play",
            config=self.audio_task(True).config,
        )
        store.add_task(routine.routine_id, task_type="test.marker", name="next")

        result = service.run_routine(routine.routine_id).routine_results[0]

        self.assertFalse(result.succeeded)
        self.assertIn("Decoder failed", result.task_results[0].detail)
        self.assertNotIn("marker:next", events)

    def test_waiting_playback_times_out_instead_of_hanging(self) -> None:
        events: list[str] = []
        factory = PlannedPlayerFactory([("stalled", "never", 0)], events)
        service, store, _manager = self.service_with_audio(factory, events)
        routine = store.add("Stalled audio")
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Play",
            config=self.audio_task(True, timeout=0.02).config,
        )

        started = time.perf_counter()
        result = service.run_routine(routine.routine_id).routine_results[0]

        self.assertFalse(result.succeeded)
        self.assertIn("timed out", result.task_results[0].detail.lower())
        self.assertTrue(factory.players[0].stopped)
        self.assertLess(time.perf_counter() - started, 0.5)

    def test_queue_stop_cancels_only_waiting_playback_instance(self) -> None:
        events: list[str] = []
        factory = PlannedPlayerFactory(
            [
                ("background", "complete", 1_000),
                ("long", "complete", 1_000),
            ],
            events,
        )
        service, store, manager = self.service_with_audio(factory, events)
        routine = store.add("Cancelled audio")
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Background",
            config=self.audio_task(False).config,
        )
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Play",
            config=self.audio_task(True, timeout=2).config,
        )
        store.add_task(routine.routine_id, task_type="test.marker", name="next")
        QTimer.singleShot(
            15,
            lambda: manager.stop(DEFAULT_AUTOMATION_QUEUE_ID),
        )

        started = time.perf_counter()
        result = service.run_routine(routine.routine_id).routine_results[0]

        self.assertTrue(result.cancelled)
        self.assertFalse(result.succeeded)
        self.assertFalse(factory.players[0].stopped)
        self.assertTrue(factory.players[1].stopped)
        self.assertNotIn("marker:next", events)
        self.assertLess(time.perf_counter() - started, 0.5)
        factory.players[0].stop()

    def test_wait_option_round_trips_in_routine_storage(self) -> None:
        store = RoutineStore(self.root / "routines.json")
        routine = store.add("Persist audio")
        store.add_task(
            routine.routine_id,
            task_type="core.play_audio",
            name="Play",
            config=self.audio_task(True).config,
        )

        loaded = RoutineStore(store.path)
        loaded.load()

        self.assertTrue(
            loaded.get(routine.routine_id).tasks[0].config["wait_for_completion"]
        )


if __name__ == "__main__":
    unittest.main()
