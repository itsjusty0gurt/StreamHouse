from __future__ import annotations

import json
from pathlib import Path

from products.hub.automation.routines import RoutineStore
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.integrations.music_player import (
    MUSIC_EVENT_PLAYBACK_PAUSED,
    MUSIC_EVENT_PLAYBACK_STARTED,
    MUSIC_EVENT_PLAYBACK_STOPPED,
    MUSIC_EVENT_PLAYER_CONNECTED,
    MUSIC_EVENT_PLAYER_DISCONNECTED,
    MUSIC_EVENT_TRACK_CHANGED,
    MUSIC_EVENT_VOLUME_CHANGED,
    MusicPlayerEvent,
    MusicPlayerService,
    MusicVariableProvider,
)
from products.hub.integrations.music_triggers import MusicTriggerStore
from products.hub.tests.test_music_player import _connect, _service, _state


def _emit(socket, message: dict[str, object]) -> None:
    socket.textMessageReceived.emit(json.dumps(message))


def test_initial_snapshot_is_baseline_and_real_transitions_fire_once() -> None:
    service, socket = _service()
    events: list[MusicPlayerEvent] = []
    service.automation_event.connect(events.append)
    _connect(service, socket)
    assert [event.event_type for event in events] == [MUSIC_EVENT_PLAYER_CONNECTED]

    _emit(socket, _state(1))
    assert [event.event_type for event in events] == [MUSIC_EVENT_PLAYER_CONNECTED]

    paused = _state(2)
    paused["playback"] = {**paused["playback"], "status": "paused"}
    _emit(socket, paused)
    _emit(socket, {**paused, "sequence": 3, "reason": "position_sync"})
    stopped = _state(4)
    stopped["playback"] = {**stopped["playback"], "status": "stopped"}
    _emit(socket, stopped)
    started = _state(5)
    _emit(socket, started)
    assert [event.event_type for event in events] == [
        MUSIC_EVENT_PLAYER_CONNECTED,
        MUSIC_EVENT_PLAYBACK_PAUSED,
        MUSIC_EVENT_PLAYBACK_STOPPED,
        MUSIC_EVENT_PLAYBACK_STARTED,
    ]
    service.shutdown()


def test_track_identity_volume_mute_and_incomplete_metadata_are_normalized() -> None:
    service, socket = _service()
    events: list[MusicPlayerEvent] = []
    service.automation_event.connect(events.append)
    _connect(service, socket)
    _emit(socket, _state(1))

    position_only = _state(2)
    position_only["reason"] = "position_sync"
    _emit(socket, position_only)
    incomplete = _state(3)
    incomplete["metadata_available"] = False
    incomplete["track"] = None
    _emit(socket, incomplete)
    changed = _state(4)
    changed["track"] = {
        "title": "Next Song",
        "artist": "Next Artist",
        "album": "Next Album",
        "media_id": "media-2",
        "artwork": {"url": "https://images.example/next.jpg"},
    }
    _emit(socket, changed)
    louder = {**changed, "sequence": 5, "audio": {"volume": 80, "muted": False}}
    _emit(socket, louder)
    muted = {**louder, "sequence": 6, "audio": {"volume": 80, "muted": True}}
    _emit(socket, muted)
    assert [event.event_type for event in events].count(MUSIC_EVENT_TRACK_CHANGED) == 1
    assert [event.event_type for event in events].count(MUSIC_EVENT_VOLUME_CHANGED) == 2
    track_event = next(event for event in events if event.event_type == MUSIC_EVENT_TRACK_CHANGED)
    assert track_event.context["music.title"] == "Next Song"
    service.shutdown()


def test_connection_loss_and_reconnect_are_edge_triggered() -> None:
    service, socket = _service()
    events: list[MusicPlayerEvent] = []
    service.automation_event.connect(events.append)
    _connect(service, socket)
    _emit(socket, _state(1))
    socket.disconnected.emit()
    socket.disconnected.emit()
    assert [event.event_type for event in events].count(MUSIC_EVENT_PLAYER_DISCONNECTED) == 1
    service._reconnect_timer.stop()
    service._open_socket()
    socket.connected.emit()
    from products.hub.tests.test_music_player import _hello
    _hello(socket)
    _emit(socket, _state(1))
    assert [event.event_type for event in events].count(MUSIC_EVENT_PLAYER_CONNECTED) == 2
    assert [event.event_type for event in events].count(MUSIC_EVENT_TRACK_CHANGED) == 0
    service.shutdown()


def test_track_fallback_identity_and_rapid_changes_remain_distinct() -> None:
    service, socket = _service()
    events: list[MusicPlayerEvent] = []
    service.automation_event.connect(events.append)
    _connect(service, socket)
    initial = _state(1)
    initial["track"] = {
        "title": "First",
        "artist": "Artist",
        "album": "Album",
        "media_id": None,
        "artwork": {},
    }
    _emit(socket, initial)
    second = {**initial, "sequence": 2, "track": {**initial["track"], "title": "Second"}}
    third = {**initial, "sequence": 3, "track": {**initial["track"], "title": "Third"}}
    _emit(socket, second)
    _emit(socket, third)

    changed = [event for event in events if event.event_type == MUSIC_EVENT_TRACK_CHANGED]
    assert [event.context["music.title"] for event in changed] == ["Second", "Third"]
    service.shutdown()


def test_store_persists_evaluates_and_causal_context_overrides_live_state(tmp_path: Path) -> None:
    routines = RoutineStore(tmp_path / "routines.json")
    routine = routines.add("Now Playing")
    store = MusicTriggerStore(tmp_path / "music_triggers.json", routines)
    trigger = store.add(routine.routine_id, MUSIC_EVENT_TRACK_CHANGED)
    event = MusicPlayerEvent(MUSIC_EVENT_TRACK_CHANGED, {"music.title": "Causal Song"})
    evaluated = store.evaluate(event)
    assert len(evaluated) == 1
    assert evaluated[0].trigger_id == trigger.trigger_id
    assert evaluated[0].context["music.title"] == "Causal Song"

    loaded = MusicTriggerStore(store.path, routines)
    loaded.load()
    assert loaded.get(trigger.trigger_id) is not None

    service, _socket = _service()
    provider = MusicVariableProvider(service)
    snapshot = provider.resolve("music.title", evaluated[0].context)
    assert snapshot.available
    assert snapshot.value == "Causal Song"
    unavailable_album = provider.resolve(
        "music.album",
        {"__music_event_snapshot__": "true", "music.title": "Causal Song"},
    )
    assert not unavailable_album.available
    service.shutdown()


def test_disabled_music_trigger_does_not_evaluate(tmp_path: Path) -> None:
    routines = RoutineStore(tmp_path / "routines.json")
    routine = routines.add("Muted")
    store = MusicTriggerStore(tmp_path / "music_triggers.json", routines)
    store.add(routine.routine_id, MUSIC_EVENT_VOLUME_CHANGED, enabled=False)
    assert store.evaluate(MusicPlayerEvent(MUSIC_EVENT_VOLUME_CHANGED, {})) == ()


def test_store_update_and_delete_preserve_routine_links(tmp_path: Path) -> None:
    routines = RoutineStore(tmp_path / "routines.json")
    routine = routines.add("Player status")
    store = MusicTriggerStore(tmp_path / "music_triggers.json", routines)
    trigger = store.add(routine.routine_id, MUSIC_EVENT_PLAYER_CONNECTED)

    updated = store.update(
        trigger.trigger_id,
        event_type=MUSIC_EVENT_PLAYER_DISCONNECTED,
        enabled=False,
    )
    assert updated.trigger_id in routines.get(routine.routine_id).trigger_ids
    assert not updated.enabled
    assert store.delete(updated.trigger_id)
    assert updated.trigger_id not in routines.get(routine.routine_id).trigger_ids


def test_music_trigger_uses_the_normal_routine_queue(tmp_path: Path) -> None:
    routines = RoutineStore(tmp_path / "routines.json")
    queues = AutomationQueueStore(tmp_path / "queues.json")
    queues.reset()
    queue = queues.add("Music events")
    queues.update(queue.queue_id, paused=True)
    routine = routines.add("Queued music routine", queue_id=queue.queue_id)
    triggers = MusicTriggerStore(tmp_path / "music_triggers.json", routines)
    triggers.add(routine.routine_id, MUSIC_EVENT_TRACK_CHANGED)
    service = AutomationService(
        routines,
        TaskRegistry(),
        queue_manager=AutomationQueueManager(queues),
    )

    event = MusicPlayerEvent(
        MUSIC_EVENT_TRACK_CHANGED,
        {"__music_event_snapshot__": "true", "music.title": "Queued Song"},
    )
    result = service.publish_trigger(triggers.evaluate(event)[0])

    assert result.handled
    assert service.queue_manager.count(queue.queue_id) == 1
    queued = service.queue_manager.state(queue.queue_id)[1][0]
    assert queued.trigger.context["music.title"] == "Queued Song"
