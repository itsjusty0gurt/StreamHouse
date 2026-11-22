import logging
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from products.hub.automation.condition_trigger_runtime import CoreIfTriggerRuntime
from products.hub.automation.core_triggers import CORE_TRIGGER_TYPES, CoreTriggerStore
from products.hub.automation.custom_event_tasks import (
    CustomEventDispatcher, LogMessageTask, SendCustomEventTask,
)
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.task_catalog import CORE_TASK_METADATA
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.timer_scheduler import AutomationTimerScheduler
from products.hub.automation.trigger_state import SetTriggerStateTask, TriggerState
from products.hub.automation.variable_providers import core_context_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.integrations.music_triggers import MUSIC_TRIGGER_TYPES, MusicTriggerStore
from products.hub.modules.contracts import Availability
from products.hub.modules.contributions import TriggerProviderContribution, TriggerProviderRegistry
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES, ObsTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.ui.automation_page import CustomEventTriggerDialog, TaskEditorDialog
from shared.streamhouse_runtime.logger import Logger


class Capture:
    task_type = "test.capture_event"

    def __init__(self):
        self.calls = []

    def execute(self, task, trigger):
        self.calls.append((task.name, dict(trigger.context)))
        return TaskExecutionResult(task.task_id, task.task_type, True)


@pytest.fixture
def runtime(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    routines = RoutineStore(tmp_path / "routines.json")
    core = CoreTriggerStore(tmp_path / "core.json", routines)
    variables = VariableRegistry()
    variables.register(core_context_provider())
    queues = AutomationQueueStore(tmp_path / "queues.json")
    registry = TaskRegistry(CORE_TASK_METADATA)
    capture = Capture()
    registry.register(capture)
    automation = AutomationService(
        routines, registry, CustomVariableStore(tmp_path / "variables.json"),
        AutomationQueueManager(queues), variables,
    )
    dispatcher = CustomEventDispatcher(core, automation, lambda *_: None)
    for task in (SendCustomEventTask(variables, dispatcher), LogMessageTask(variables)):
        registry.register(task)
    yield app, routines, core, variables, queues, registry, capture, automation, dispatcher
    dispatcher.stop()
    QCoreApplication.sendPostedEvents(dispatcher, QEvent.Type.MetaCall)


def add_task(routines, routine, task_type, name=None, **config):
    return routines.add_task(routine.routine_id, task_type=task_type, name=name or task_type, config=config)


def deliver(dispatcher):
    QCoreApplication.sendPostedEvents(dispatcher, QEvent.Type.MetaCall)


def test_custom_event_is_deferred_casefolded_and_isolated(runtime):
    _, routines, core, variables, _, _, capture, automation, dispatcher = runtime
    sender = routines.add("Announcer")
    add_task(routines, sender, "core.send_custom_event", event_name="{event.name}", event_data="{user.name}")
    add_task(routines, sender, Capture.task_type, "sender finished")
    for name, event, enabled, routine_enabled in (
        ("first listener", "Break    Started", True, True),
        ("second listener", "BREAK STARTED", True, True),
        ("disabled trigger", "break started", False, True),
        ("disabled routine", "break started", True, False),
        ("other event", "break ended", True, True),
    ):
        routine = routines.add(name, enabled=routine_enabled)
        core.add(routine.routine_id, "custom_event", event_name=event, enabled=enabled)
        add_task(routines, routine, Capture.task_type, name)
    reloaded = CoreTriggerStore(core.path, routines)
    assert reloaded.load() == core.triggers
    assert core.evaluate("application.started") == core.evaluate("custom_event") == ()

    result = automation.run_routine(sender.routine_id, {
        "event.name": "  break   started   ", "user.name": "Viewer", "sender.only": "private",
    })
    assert result.succeeded
    assert [name for name, _ in capture.calls] == ["sender finished"]
    deliver(dispatcher)
    assert [name for name, _ in capture.calls] == ["sender finished", "first listener", "second listener"]
    for _, context in capture.calls[1:]:
        assert variables.render(
            "{core.custom_event.name}|{core.custom_event.data}|{core.custom_event.source_routine}",
            context,
        ) == "break started|Viewer|Announcer"
        assert "sender.only" not in context and "user.name" not in context
    assert not variables.resolve("core.custom_event.name").available


def test_custom_events_respect_paused_queues_and_duplicate_policy(runtime):
    _, routines, core, _, queues, _, capture, automation, dispatcher = runtime
    queue = queues.add("Listeners", duplicate_policy="ignore")
    queues.update(queue.queue_id, paused=True)
    sender = routines.add("Sender")
    listener = routines.add("Listener", queue_id=queue.queue_id)
    core.add(listener.routine_id, "custom_event", event_name="ping")
    add_task(routines, listener, Capture.task_type)
    add_task(routines, sender, "core.send_custom_event", event_name="ping")
    for _ in range(2):
        assert automation.run_routine(sender.routine_id).succeeded
        deliver(dispatcher)
    assert capture.calls == []
    queues.update(queue.queue_id, paused=False)
    assert len(automation.process_queues()) == 1
    assert capture.calls[0][1]["core.custom_event.data"] == ""
    assert automation.process_queues() == ()
    with pytest.raises(ValueError, match="name is required"):
        core.add(sender.routine_id, "custom_event", event_name="  ")


def test_custom_event_cycles_block_ancestors_but_allow_independent_sends(runtime):
    _, routines, core, _, _, _, capture, automation, dispatcher = runtime
    a, b = routines.add("A"), routines.add("B")
    for routine, received, sent in ((a, "return", "forward"), (b, "forward", "return")):
        core.add(routine.routine_id, "custom_event", event_name=received)
        add_task(routines, routine, Capture.task_type, routine.name)
        add_task(routines, routine, "core.send_custom_event", event_name=sent)
    core.add(a.routine_id, "custom_event", event_name="forward")
    for _ in range(2):
        assert automation.run_routine(a.routine_id).succeeded
        for _ in range(3):
            deliver(dispatcher)
    assert [name for name, _ in capture.calls] == ["A", "B", "A", "B"]
    bad = add_task(routines, a, "core.send_custom_event", event_name="{missing.variable}")
    assert not automation.run_task(a.routine_id, bad.task_id).succeeded
    orphan = add_task(routines, a, "core.send_custom_event", event_name="no listeners")
    assert automation.run_task(a.routine_id, orphan.task_id).succeeded
    deliver(dispatcher)
    assert len(capture.calls) == 4


def test_trigger_state_selects_exact_provider_and_preserves_timer_lifecycle(runtime, tmp_path):
    _, routines, core, _, _, registry, capture, automation, _ = runtime
    target = routines.add("Target")
    caller = routines.add("Caller")
    timer = core.add_timer(
        target.routine_id, timer_mode="fixed", timer_minimum="1", timer_minimum_unit="seconds",
    )
    other = core.add(target.routine_id, "application.started")
    music = MusicTriggerStore(tmp_path / "music.json", routines)
    music_target = music.add(target.routine_id, "track.changed")
    obs = ObsTriggerStore(tmp_path / "obs.json", routines)
    obs_target = obs.add(
        target.routine_id, "CurrentProgramSceneChanged", filters={"sceneName": "Gameplay"}
    )
    twitch = TwitchEventTriggerStore(tmp_path / "twitch.json", routines)
    twitch_target = twitch.add(target.routine_id, "channel.follow")
    providers = TriggerProviderRegistry()
    providers.register(TriggerProviderContribution(
        "obs.test", "test.obs", "OBS", obs, OBS_TRIGGER_TYPES,
    ))
    availability = {"enabled": True}
    providers.register(TriggerProviderContribution(
        "twitch.test", "test.twitch", "Twitch", twitch,
        availability=lambda: Availability(availability["enabled"]),
    ))
    changes = []
    state = TriggerState(routines, providers, (
        ("Core", core, CORE_TRIGGER_TYPES), ("Music", music, MUSIC_TRIGGER_TYPES),
    ), lambda: changes.append(True))
    handler = SetTriggerStateTask(state)
    registry.register(handler)
    timers = AutomationTimerScheduler(core, lambda *_: pytest.fail("Unexpected timer fire"), auto_arm=False)
    timers.start()
    try:
        assert timer.trigger_id in timers.schedules
        for store, selected in ((core, timer), (music, music_target), (obs, obs_target), (twitch, twitch_target)):
            assert selected.trigger_id in {key for _, key in state.options(target.routine_id)}
            assert state.set_state(target.routine_id, selected.trigger_id, "disable").startswith("Disabled")
            assert not store.get(selected.trigger_id).enabled
        assert timer.trigger_id not in timers.schedules
        assert core.get(other.trigger_id).enabled
        assert obs.get(obs_target.trigger_id).filters == obs_target.filters
        assert ObsTriggerStore(obs.path, routines).load()[0].enabled is False
        state.set_state(target.routine_id, timer.trigger_id, "enable")
        assert timer.trigger_id in timers.schedules
        state.set_state(target.routine_id, timer.trigger_id, "toggle")
        assert timer.trigger_id not in timers.schedules
        with pytest.raises(ValueError, match="missing, unavailable"):
            state.set_state(caller.routine_id, other.trigger_id, "disable")
        availability["enabled"] = False
        with pytest.raises(ValueError, match="unavailable"):
            state.set_state(target.routine_id, twitch_target.trigger_id, "enable")
        add_task(routines, target, "core.set_trigger_state",
                 routine_id=target.routine_id, trigger_id=other.trigger_id, action="disable")
        add_task(routines, target, Capture.task_type, "after disabling own trigger")
        event, = core.evaluate("application.started")
        assert automation.publish_trigger(event).succeeded
        assert len(changes) == 7
        assert capture.calls[0][0] == "after disabling own trigger"
        assert core.evaluate("application.started") == ()
    finally:
        timers.shutdown()


def test_stateful_if_trigger_rebaselines_without_firing_on_enable(runtime):
    _, routines, core, variables, _, _, _, _, _ = runtime
    routine = routines.add("Conditional")
    target = core.add_if(routine.routine_id, condition_join="and",
                         conditions=[{"left": "ready", "operator": "equals", "right": "ready"}],
                         firing_mode="becomes_true")
    fired = []
    condition_runtime = CoreIfTriggerRuntime(core, variables, lambda *args: fired.append(args))
    condition_runtime.start()
    state = TriggerState(routines, TriggerProviderRegistry(), (("Core", core, CORE_TRIGGER_TYPES),), lambda: None)
    try:
        for action in ("disable", "enable", "toggle", "toggle"):
            state.set_state(routine.routine_id, target.trigger_id, action)
            condition_runtime.reevaluate()
        assert core.get(target.trigger_id).enabled
        assert fired == []
    finally:
        condition_runtime.stop()


def test_log_message_resolves_multiline_and_redacts_without_stopping(runtime, caplog, monkeypatch):
    _, routines, _, _, _, _, capture, automation, _ = runtime
    logger = logging.getLogger("automation-test")
    monkeypatch.setattr(Logger, "_logger", logger)
    caplog.set_level(logging.DEBUG, logger=logger.name)
    routine = routines.add("Night shift")
    for level in ("debug", "info", "warning", "error"):
        add_task(routines, routine, "core.log_message", level=level,
                 message="Hello {user.name}\nAuthorization: Bearer fake-test-secret")
    add_task(routines, routine, Capture.task_type)
    result = automation.run_routine(routine.routine_id, {"user.name": "Viewer"})
    assert result.succeeded and len(capture.calls) == 1
    messages = [record for record in caplog.records if record.name == logger.name]
    assert [record.levelname for record in messages] == ["DEBUG", "INFO", "WARNING", "ERROR"]
    assert all(record.source == "AUTOMATION"[:Logger.SOURCE_WIDTH] for record in messages)
    assert all("Night shift: Hello Viewer\n" in record.getMessage() for record in messages)
    assert "fake-test-secret" not in caplog.text
    assert all("fake-test-secret" not in item.detail for item in result.routine_results[0].task_results)


def test_new_editors_register_templated_fields_and_require_explicit_trigger_link(runtime):
    _, routines, core, variables, _, registry, _, _, _ = runtime
    trigger = CustomEventTriggerDialog()
    with pytest.raises(ValueError, match="required"):
        trigger.values()
    trigger.event_name_edit.setText("  Start   Break  ")
    assert trigger.values()["event_name"] == "Start Break"
    for task_type, fields in (
        ("core.send_custom_event", {"event_name", "event_data"}),
        ("core.log_message", {"message"}),
    ):
        dialog = TaskEditorDialog(task_type, variable_registry=variables)
        assert fields == set(dialog.variable_picker_buttons)
        assert registry.metadata(task_type).short_description
    routine = routines.add("Target")
    target = core.add(routine.routine_id, "application.started")
    task = TaskDefinition("t", "core.set_trigger_state", "Enable", {
        "routine_id": routine.routine_id, "trigger_id": target.trigger_id, "requires_relink": True,
    })
    dialog = TaskEditorDialog(
        task.task_type, task=task, routine_store=routines,
        trigger_options=lambda routine_id: (("Started", target.trigger_id),)
        if routine_id == routine.routine_id else (),
    )
    trigger_combo = dialog.field_widgets[task.task_type]["trigger_id"]
    with pytest.raises(ValueError, match="Trigger is required"):
        dialog.values()
    trigger_combo.setCurrentIndex(trigger_combo.findData(target.trigger_id))
    assert dialog.values()["config"]["trigger_id"] == target.trigger_id
    assert "requires_relink" not in dialog.values()["config"]
