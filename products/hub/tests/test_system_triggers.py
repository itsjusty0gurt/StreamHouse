from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QDate, QDateTime, QEvent, QObject, QTime, QTimeZone, Qt, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QLabel

from products.hub.automation.core_triggers import CORE_TRIGGER_TYPES, CoreTriggerStore
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import TaskExecutionResult
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.system_trigger_config import CronSchedule, hotkey_codes, system_config
from products.hub.automation.system_trigger_runtime import SystemTriggerRuntime, file_change, file_state
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.trigger_state import TriggerState
from products.hub.automation.variable_providers import core_context_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.modules.contributions import TriggerProviderRegistry
from products.hub.ui.system_trigger_dialog import SystemTriggerDialog


def stamp(year, month, day, hour, minute, zone):
    return QDateTime(QDate(year, month, day), QTime(hour, minute), zone).toMSecsSinceEpoch()


def flush(runtime):
    for _ in range(3):
        QCoreApplication.sendPostedEvents(runtime, QEvent.Type.MetaCall)


def test_cron_calendar_steps_and_weekday_alternatives():
    zone = QTimeZone.utc()
    assert CronSchedule.parse("0 8 * * MON-FRI").next_after(
        stamp(2026, 11, 20, 8, 0, zone), zone,
    ) == stamp(2026, 11, 23, 8, 0, zone)
    assert CronSchedule.parse("*/15 8 * * *").next_after(
        stamp(2026, 11, 20, 8, 1, zone), zone,
    ) == stamp(2026, 11, 20, 8, 15, zone)
    assert CronSchedule.parse("0 8 1 * MON").next_after(
        stamp(2026, 11, 29, 8, 0, zone), zone,
    ) == stamp(2026, 11, 30, 8, 0, zone)


@pytest.mark.parametrize("value", ["", "@daily", "*/0 * * * *", "0 24 * * *", "0 8 30 FEB *", "0 8 * * 8"])
def test_invalid_cron(value):
    with pytest.raises(ValueError):
        CronSchedule.parse(value)


def test_cron_dst_skips_spring_gap_and_chooses_first_fall_fold():
    # Historical transitions keep the scheduler test independent of changes to
    # the host's timezone or future regional DST rules.
    local, utc = QTimeZone(b"America/New_York"), QTimeZone.utc()
    assert local.isValid()
    assert CronSchedule.parse("30 2 * * *").next_after(
        stamp(2024, 3, 10, 5, 0, utc), local,
    ) == stamp(2024, 3, 11, 6, 30, utc)
    schedule = CronSchedule.parse("30 1 * * *")
    first = schedule.next_after(stamp(2024, 11, 3, 4, 0, utc), local)
    assert first == stamp(2024, 11, 3, 5, 30, utc)
    assert schedule.next_after(first, local) == stamp(2024, 11, 4, 6, 30, utc)


class Hotkeys:
    supported = True

    def __init__(self):
        self.ids = {}
        self.calls = []

    def register(self, identifier, modifiers, key):
        self.calls.append((identifier, modifiers, key))
        if (modifiers, key) in self.ids.values():
            return False
        self.ids[identifier] = modifiers, key
        return True

    def unregister(self, identifier):
        del self.ids[identifier]


class Watcher(QObject):
    fileChanged = Signal(str)
    directoryChanged = Signal(str)

    def __init__(self, parent):
        super().__init__(parent)
        self.paths = set()

    def files(self):
        return [path for path in self.paths if Path(path).is_file()]

    def directories(self):
        return [path for path in self.paths if Path(path).is_dir()]

    def addPaths(self, paths):
        self.paths.update(paths)
        return []

    def removePaths(self, paths):
        self.paths.difference_update(paths)
        return []


@pytest.fixture
def rig(tmp_path):
    app = QApplication.instance() or QApplication([])
    routines = RoutineStore(tmp_path / "routines.json")
    routine = routines.add("Target")
    store = CoreTriggerStore(tmp_path / "core.json", routines)
    hotkeys = Hotkeys()
    events = []
    time = Mock(return_value=stamp(2026, 11, 22, 8, 0, QTimeZone.utc()))
    runtime = SystemTriggerRuntime(store, lambda event, label: events.append(event), hotkeys,
                                   clock=time, zone=QTimeZone.utc, snapshot=lambda: {}, watcher_factory=Watcher)
    yield app, routine, store, hotkeys, events, time, runtime
    runtime.stop()
    flush(runtime)


def test_scheduler_and_editor_use_os_local_time(rig):
    _, _, store, hotkeys, _, _, _ = rig
    runtime = SystemTriggerRuntime(store, lambda *_: None, hotkeys, watcher_factory=Watcher)
    zone = runtime.zone()
    assert zone.timeSpec() == Qt.TimeSpec.LocalTime
    schedule = CronSchedule.parse("30 20 * * *")
    for month in (6, 11):
        instant = stamp(2026, month, 22, 12, 0, QTimeZone.utc())
        local = datetime.fromtimestamp(instant / 1000).astimezone()
        converted = QDateTime.fromMSecsSinceEpoch(instant, zone)
        assert converted.offsetFromUtc() == int(local.utcoffset().total_seconds())
        assert converted.toString("yyyy-MM-dd HH:mm") == local.strftime("%Y-%m-%d %H:%M")
        before = int(datetime(2026, month, 22, 20).timestamp() * 1000)
        expected = int(datetime(2026, month, 22, 20, 30).timestamp() * 1000)
        assert schedule.next_after(before, zone) == expected

    dialog = SystemTriggerDialog("scheduled_time")
    zone_name = bytes(QTimeZone.systemTimeZoneId()).decode("utf-8", errors="replace")
    assert f"System local time ({zone_name})" in {label.text() for label in dialog.findChildren(QLabel)}


def test_schedule_dispatch_disable_clock_change_and_restart(rig):
    _, routine, store, _, events, clock, runtime = rig
    trigger = store.add(routine.routine_id, "scheduled_time", system_config={"expression": "* * * * *"})
    runtime.start()
    flush(runtime)
    assert not events
    clock.return_value += 60_000
    runtime.check_schedules()
    runtime.check_schedules()
    flush(runtime)
    assert len(events) == 1
    assert events[0].context["core.schedule.time"] == "08:01"
    store.set_enabled(trigger.trigger_id, False)
    flush(runtime)
    assert not runtime.schedule_timer.isActive()
    store.set_enabled(trigger.trigger_id, True)
    flush(runtime)
    clock.return_value += 20 * 60_000
    runtime.native_message(0x0218, 0x12)
    flush(runtime)
    runtime.check_schedules()
    assert len(events) == 1
    clock.return_value -= 21 * 60_000
    runtime.replan()
    clock.return_value += 60_000
    runtime.check_schedules()
    flush(runtime)
    assert len(events) == 1
    runtime.stop()
    runtime.start()
    flush(runtime)
    assert len(events) == 1


def test_hotkey_conflicts_recording_set_state_and_shutdown(rig):
    _, routine, store, hotkeys, events, _, runtime = rig
    first = store.add(routine.routine_id, "hotkey", system_config={"shortcut": "Alt+Ctrl+B"})
    second = store.add(routine.routine_id, "hotkey", system_config={"shortcut": "Ctrl+Alt+B"})
    runtime.start()
    assert store.get(first.trigger_id).system_config == {"shortcut": "Ctrl+Alt+B"}
    assert set(runtime.shortcut_ids) == {first.trigger_id}
    assert second.trigger_id in runtime.shortcut_failures
    first_native = runtime.shortcut_ids[first.trigger_id]
    assert runtime.native_message(0x0312, first_native)
    flush(runtime)
    assert [event.trigger_id for event in events] == [first.trigger_id]
    runtime.set_recording(True)
    assert not hotkeys.ids and not runtime.native_message(0x0312, first_native)
    runtime.set_recording(False)
    state = TriggerState(store.routine_store, TriggerProviderRegistry(), (("Core", store, CORE_TRIGGER_TYPES),), lambda: None)
    state.set_state(routine.routine_id, first.trigger_id, "disable")
    flush(runtime)
    assert set(runtime.shortcut_ids) == {second.trigger_id}
    store.delete(second.trigger_id)
    flush(runtime)
    assert not hotkeys.ids
    runtime.stop()
    assert not runtime.native_message(0x0312, first_native)


def test_unsupported_hotkey_never_becomes_active(rig):
    _, routine, store, hotkeys, _, _, runtime = rig
    hotkeys.supported = False
    hotkeys.register = Mock(return_value=False)
    trigger = store.add(routine.routine_id, "hotkey", system_config={"shortcut": "Ctrl+B"})
    runtime.start()
    assert trigger.trigger_id in runtime.shortcut_failures
    assert not runtime.shortcut_ids


def test_process_baseline_and_multiple_instances(rig, monkeypatch):
    _, routine, store, _, events, _, runtime = rig
    monkeypatch.setattr(runtime, "request_scan", lambda: None)
    for kind in ("process_started", "process_stopped"):
        store.add(routine.routine_id, kind, system_config={"name": "notepad.exe"})
    runtime.start()
    assert runtime.process_timer.isActive()
    for snapshot in ({1: "NOTEPAD.EXE"}, {1: "NOTEPAD.EXE", 2: "notepad.exe"},
                     {1: "NOTEPAD.EXE", 2: "notepad.exe"}, None, {2: "notepad.exe"}):
        runtime._scanned(dict(runtime.targets), snapshot, {})
        flush(runtime)
    assert [(item.trigger_type, item.context["core.process.pid"]) for item in events] == [
        ("process_started", "2"), ("process_stopped", "1"),
    ]
    variables = VariableRegistry()
    variables.register(core_context_provider())
    assert variables.render("{core.process.name}:{core.process.pid}", events[1].context) == "NOTEPAD.EXE:1"
    for target in list(runtime.targets):
        store.set_enabled(target, False)
    flush(runtime)
    assert not runtime.process_timer.isActive()
    assert not runtime.process_baselines


def test_file_watch_recovers_and_detects_atomic_replacement(tmp_path):
    folder = tmp_path / "pending"
    target = folder / "target.txt"
    absent = file_state(target, "file")
    assert absent.watches == (str(tmp_path),)
    folder.mkdir()
    assert file_state(target, "file").watches == (str(folder),)
    target.write_text("first")
    first = file_state(target, "file")
    assert file_change(absent, first) == "created"
    replacement = folder / "replacement.txt"
    replacement.write_text("second")
    replacement.replace(target)
    second = file_state(target, "file")
    assert file_change(first, second) == "modified"
    assert file_change(second, file_state(target, "file")) is None
    target.unlink()
    assert file_change(second, file_state(target, "file")) == "deleted"
    target.write_text("recreated")
    assert file_change(absent, file_state(target, "file")) == "created"


def test_file_runtime_coalesces_notices_and_rejects_stale_result(rig, tmp_path, monkeypatch):
    _, routine, store, _, events, _, runtime = rig
    monkeypatch.setattr(runtime, "request_scan", lambda: None)
    path = tmp_path / "watched.txt"
    trigger = store.add(routine.routine_id, "file_watcher", system_config={"path": str(path), "event": "created"})
    runtime.start()
    generation = runtime.targets[trigger.trigger_id][1]
    runtime._scanned({}, None, {trigger.trigger_id: (generation, file_state(path, "file"))})
    assert str(tmp_path) in runtime.watcher.directories()
    runtime._file_changed(str(tmp_path))
    runtime._file_changed(str(tmp_path))
    assert runtime.file_timer.isActive()
    path.write_text("test")
    sample = {trigger.trigger_id: (generation, file_state(path, "file"))}
    runtime._scanned({}, None, sample)
    runtime._scanned({}, None, sample)
    flush(runtime)
    assert len(events) == 1 and events[0].context["core.file.name"] == "watched.txt"
    store.set_enabled(trigger.trigger_id, False)
    flush(runtime)
    runtime._scanned({}, None, sample)
    flush(runtime)
    assert len(events) == 1 and not runtime.watcher.directories() and not runtime.watcher.files()


def test_editor_serializes_simple_schedule_and_shortcut(rig):
    schedule = SystemTriggerDialog("scheduled_time")
    assert schedule.values()["system_config"] == {"mode": "simple", "expression": "0 8 * * *"}
    schedule.weekdays[1].setChecked(True)
    schedule.weekdays[5].setChecked(True)
    assert schedule.values()["system_config"]["expression"] == "0 8 * * 1,5"
    hotkey = SystemTriggerDialog("hotkey")
    hotkey.shortcut.setKeySequence(QKeySequence("Ctrl+Shift+7"))
    assert hotkey.values()["system_config"]["shortcut"] == "Ctrl+Shift+7"
    with pytest.raises(ValueError):
        hotkey_codes("Alt+F4")
    with pytest.raises(ValueError):
        system_config("process_started", {"name": str(Path("sample") / "notepad.exe")})


def test_schedule_uses_automation_queue_and_drops_event_if_disabled_before_delivery(rig, tmp_path):
    _, _, store, _, _, clock, runtime = rig
    queues = AutomationQueueStore(tmp_path / "queues.json")
    queue = queues.add("Scheduled", duplicate_policy="ignore")
    queues.update(queue.queue_id, paused=True)
    routines = store.routine_store
    routine = routines.add("Scheduled", queue_id=queue.queue_id)
    variables = VariableRegistry()
    variables.register(core_context_provider())
    names = []

    class Capture:
        task_type = "test.schedule"

        def execute(self, task, trigger):
            names.append(variables.render("{core.schedule.time}", trigger.context))
            return TaskExecutionResult(task.task_id, task.task_type, True)

    registry = TaskRegistry()
    registry.register(Capture())
    automation = AutomationService(
        routines, registry, CustomVariableStore(tmp_path / "variables.json"),
        AutomationQueueManager(queues), variables,
    )
    runtime.publish = lambda event, _label: automation.publish_trigger(event)
    routines.add_task(routine.routine_id, task_type="test.schedule", name="Capture", config={})
    trigger = store.add(routine.routine_id, "scheduled_time", system_config={"expression": "* * * * *"})
    runtime.start()
    for _ in range(2):
        clock.return_value += 60_000
        runtime.check_schedules()
        flush(runtime)
    assert not names
    queues.update(queue.queue_id, paused=False)
    assert len(automation.process_queues()) == 1 and names == ["08:01"]
    assert automation.process_queues() == ()
    clock.return_value += 60_000
    runtime.check_schedules()
    store.set_enabled(trigger.trigger_id, False)
    flush(runtime)
    assert names == ["08:01"]
