import json
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from products.hub.automation.core_tasks import PlayAudioTask
from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.custom_event_tasks import LogMessageTask
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import DEFAULT_AUTOMATION_QUEUE_ID, TriggerEvent
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.integrations.music_triggers import MusicTriggerStore
from products.hub.modules.bundled.twitch.module import TwitchModule
from products.hub.modules import TriggerProviderContribution, TriggerProviderRegistry
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES, ObsTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.twitch.models import TwitchEvent, TwitchEventTransport
from products.hub.ui.automation_page import AutomationPage


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def event(number=0, trigger_id="burst"):
    return TriggerEvent(trigger_id, "twitch", "eventsub", {"event.number": str(number)})


@pytest.fixture
def queue_environment(tmp_path):
    routines = RoutineStore(tmp_path / "routines.json")
    tasks = TaskRegistry()
    store = AutomationQueueStore(tmp_path / "queues.json")
    manager = AutomationQueueManager(store)
    variables = VariableRegistry()
    service = AutomationService(routines, tasks, CustomVariableStore(tmp_path / "vars.json"), manager, variables)
    return routines, tasks, store, manager, service, variables


def test_queue_configuration_preserves_explicit_limits_and_restart_pause(tmp_path):
    path = tmp_path / "queues.json"
    store = AutomationQueueStore(path)
    assert store.default().max_length == 25
    old = {"version": 1, "queues": [
        {"queue_id": DEFAULT_AUTOMATION_QUEUE_ID, "name": "Default Queue", "max_length": 100},
        {"queue_id": "saved", "name": "Old alerts", "paused": True, "max_length": 73,
         "duplicate_policy": "ignore", "delay_seconds": 2},
    ]}
    path.write_text(json.dumps(old), encoding="utf-8")
    store.load()
    assert store.default().max_length == 100
    assert store.default().overflow_policy == "reject_new"
    store.update("saved", overflow_policy="drop_oldest")
    saved = AutomationQueueStore(path).load()[1]
    assert (saved.queue_id, saved.paused, saved.max_length, saved.duplicate_policy,
            saved.delay_seconds, saved.overflow_policy) == ("saved", True, 73, "ignore", 2, "drop_oldest")
    assert not store.delete(DEFAULT_AUTOMATION_QUEUE_ID)
    assert not AutomationQueueManager(store).count("saved")


@pytest.mark.parametrize(("policy", "accepted", "pending", "counts"), [
    ("reject_new", False, [1, 2], (1, 0)),
    ("drop_oldest", True, [2, 3], (0, 1)),
    ("keep_latest", True, [3], (0, 2)),
])
def test_overflow_preserves_active_and_accounts_for_every_item(
    queue_environment, policy, accepted, pending, counts,
):
    _, _, store, manager, *_ = queue_environment
    queue = store.add("Alerts", max_length=2, overflow_policy=policy)
    send = lambda n: manager.enqueue(queue.queue_id, "routine", "Alert", event(n))
    active = send(0).item
    assert manager.take_ready(queue.queue_id) == active
    assert send(1).accepted and send(2).accepted
    result = send(3)
    assert result.accepted is accepted
    assert manager.state(queue.queue_id)[0] == active
    assert [int(item.trigger.context["event.number"]) for item in manager.state(queue.queue_id)[1]] == pending
    assert manager.outcome_counts(queue.queue_id) == counts
    dispositions = manager.drain_dispositions()
    assert len(dispositions) == counts[1]
    assert all(item.status == "Superseded" and item.event_id for item in dispositions)
    assert len({item.event_id for item in dispositions}) == len(dispositions)


def test_clear_pending_pause_resume_and_stop_all(queue_environment):
    _, _, store, manager, *_ = queue_environment
    queue = store.add("Alerts")
    active = manager.enqueue(queue.queue_id, "r", "r", event()).item
    assert manager.take_ready(queue.queue_id) == active
    cancel = manager.cancellation_for(active)
    store.update(queue.queue_id, paused=True)
    waiting = manager.enqueue(queue.queue_id, "r", "r", event(1)).item
    assert manager.clear(queue.queue_id) == 1
    assert not cancel.cancelled and manager.state(queue.queue_id)[0] == active
    assert manager.drain_dispositions()[0].event_id == waiting.trigger.event_id
    manager.enqueue(queue.queue_id, "r", "r", event(2))
    assert manager.stop(queue.queue_id) == (True, 1)
    assert cancel.cancelled and manager.state(queue.queue_id)[0] == active
    assert store.get(queue.queue_id).paused
    manager.complete(queue.queue_id)
    waiting = manager.enqueue(queue.queue_id, "r", "r", event(3)).item
    assert manager.take_ready(queue.queue_id) is None
    store.update(queue.queue_id, paused=False)
    assert manager.take_ready(queue.queue_id) == waiting


def test_queue_fallback_and_round_robin(queue_environment, monkeypatch):
    routines, _, store, manager, service, _ = queue_environment
    other = store.add("Other")
    lost = store.add("Lost")
    no_assignment = routines.add("Default", trigger_id="default")
    missing = routines.add("Missing", trigger_id="missing", queue_id=lost.queue_id)
    custom = routines.add("Custom", trigger_id="custom", queue_id=other.queue_id)
    assert store.delete(lost.queue_id)
    warning = Mock()
    monkeypatch.setattr("products.hub.automation.service.Logger.warning", warning)
    for trigger in ("default", "missing", "missing", "custom"):
        service.publish_trigger(event(trigger_id=trigger), defer=True)
    warning.assert_called_once()
    assert manager.count(DEFAULT_AUTOMATION_QUEUE_ID) == 3
    assert manager.count(other.queue_id) == 1
    seen = [service.process_queues(max_items=1)[0].routine_results[0].routine_id for _ in range(4)]
    assert seen == [no_assignment.routine_id, custom.routine_id, missing.routine_id, missing.routine_id]


@pytest.mark.parametrize("size", [25, 100, 200])
@pytest.mark.parametrize("audio", [False, True], ids=["log-message", "mocked-audio"])
def test_deferred_burst_yields_between_tasks_and_retains_fifo(
    app, tmp_path, queue_environment, monkeypatch, size, audio,
):
    routines, tasks, store, manager, service, variables = queue_environment
    store.update(DEFAULT_AUTOMATION_QUEUE_ID, max_length=size)
    captures = []
    if audio:
        factory = Mock(side_effect=lambda: Mock())
        handler = PlayAudioTask(player_factory=factory, audio_output_factory=Mock)
        file = tmp_path / "silent-fixture.ogg"
        file.touch()  # No media backend is invoked by this test.
        config = {"file": str(file), "volume": 0}
    else:
        handler = LogMessageTask(variables)
        monkeypatch.setitem(LogMessageTask.LEVELS, "info", Mock())
        config = {"message": "Burst fixture"}
    run = handler.execute
    def capture(task, trigger):
        captures.append(int(trigger.context["event.number"]))
        return run(task, trigger)
    handler.execute = capture
    tasks.register(handler)
    routine = routines.add("Burst", trigger_id="burst")
    routines.add_task(routine.routine_id, task_type=handler.task_type, name="Fixture", config=config)
    heartbeats = []
    QTimer.singleShot(0, lambda: heartbeats.append(len(captures)))
    for i in range(size):
        assert service.publish_trigger(event(i), defer=True).succeeded
    assert not captures and manager.count(DEFAULT_AUTOMATION_QUEUE_ID) == size
    app.processEvents()
    assert heartbeats == [0]
    for i in range(size):
        QTimer.singleShot(0, lambda: heartbeats.append(len(captures)))
        done, = service.process_queues(max_items=1)
        assert done.succeeded
        app.processEvents()
        assert heartbeats[-1] == i + 1
    assert captures == list(range(size))
    assert not service.process_queues(max_items=1)
    if audio:
        assert factory.call_count == size
        assert handler.stop_all() == size


def test_twitch_websocket_defers_but_simulation_keeps_immediate_contract(app, queue_environment, monkeypatch):
    routines, tasks, _, manager, service, variables = queue_environment
    routine = routines.add("Release")
    handler = LogMessageTask(variables)
    logger = Mock()
    monkeypatch.setitem(LogMessageTask.LEVELS, "info", logger)
    tasks.register(handler)
    routines.add_task(routine.routine_id, task_type=handler.task_type, name="Log", config={"message": "Received"})
    trigger_store = TwitchEventTriggerStore(routines.path.with_name("twitch.json"), routines)
    trigger_store.add(routine.routine_id, "channel.channel_points_custom_reward_redemption.update")
    module = TwitchModule()
    module.trigger_store = trigger_store
    module._started = True
    recorder = Mock()
    module._context = SimpleNamespace(automation=service, execution_recorder=recorder)
    twitch_event = TwitchEvent(
        subscription_type="channel.channel_points_custom_reward_redemption.update",
        version="1", received_at=datetime.now(timezone.utc), message_id="live",
        broadcaster_user_id="channel", broadcaster_user_login="streamer",
        broadcaster_user_name="Streamer", transport=TwitchEventTransport.WEBSOCKET,
        payload={"event": {"id": "redemption"}},
    )
    module._publish_event(twitch_event)
    recorder.assert_called_once()
    assert manager.count(DEFAULT_AUTOMATION_QUEUE_ID) == 1
    assert manager.state(DEFAULT_AUTOMATION_QUEUE_ID)[0] is None
    logger.assert_not_called()
    assert service.process_queues(max_items=1)[0].succeeded
    module._publish_event(replace(twitch_event, message_id="sim", transport=TwitchEventTransport.SIMULATOR))
    assert recorder.call_count == 2
    assert not manager.count(DEFAULT_AUTOMATION_QUEUE_ID)
    assert logger.call_count == 2


def test_queue_ui_coalesces_rejections_and_reports_clear(app, tmp_path, queue_environment, monkeypatch):
    routines, tasks, store, manager, service, variables = queue_environment
    monkeypatch.setenv("STREAMHOUSE_DATA_DIR", str(tmp_path))
    routine = routines.add("Burst", trigger_id="burst")
    providers = TriggerProviderRegistry()
    providers.register(TriggerProviderContribution(
        provider_id="obs.automation", owner_module_id="streamhouse.obs", display_name="OBS",
        store=ObsTriggerStore(tmp_path / "obs.json", routines), event_types=OBS_TRIGGER_TYPES,
    ))
    page = AutomationPage(
        routines, TwitchCommandTriggerStore(tmp_path / "commands.json", routines),
        TwitchEventTriggerStore(tmp_path / "twitch.json", routines),
        CoreTriggerStore(tmp_path / "core.json", routines),
        MusicTriggerStore(tmp_path / "music.json", routines), tasks, service,
        trigger_provider_registry=providers, queue_store=store,
        queue_manager=manager, variable_registry=variables,
    )
    page.queue_timer.stop()
    try:
        for number in range(100):
            page.record_execution(service.publish_trigger(event(number), defer=True), "Burst")
        assert len(page.history) == 75 and page.history_table.rowCount() == 0
        assert {row["result"] for row in page.history} == {"Rejected"}
        page.select_routine(routine.routine_id)
        app.processEvents()
        assert page.history_table.rowCount() == page.routine_history_table.rowCount() == 75
        page._clear_pending()
        assert "Cleared 25" in page.queue_feedback_label.text()
        assert "Pending: 0" in page.queue_status_label.text()
        assert "Rejected: 75" in page.queue_status_label.text()
        page._poll_queues()
        app.processEvents()
        assert page.history_table.rowCount() == page.routine_history_table.rowCount() == 100
        assert sum(row["result"] == "Cancelled" for row in page.history) == 25
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_shutdown_disposes_backlog_without_replay(queue_environment):
    _, _, store, manager, *_ = queue_environment
    for i in range(25):
        assert manager.enqueue(DEFAULT_AUTOMATION_QUEUE_ID, "r", "r", event(i)).accepted
    assert manager.clear_all_pending("Hub is shutting down.") == 25
    assert manager.outcome_counts(DEFAULT_AUTOMATION_QUEUE_ID) == (0, 25)
    assert not manager.take_ready(DEFAULT_AUTOMATION_QUEUE_ID)
    assert not AutomationQueueManager(store).count(DEFAULT_AUTOMATION_QUEUE_ID)
