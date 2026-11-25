from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication

from products.hub.automation.cancellation import current_cancellation
from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.history_variables import (
    ExecutionContext, MAX_SNAPSHOT_CHARS, MAX_VALUE_CHARS, MAX_VARIABLES,
)
from products.hub.automation.logic_tasks import IfTask
from products.hub.automation.models import (
    DEFAULT_AUTOMATION_QUEUE_ID, TaskDefinition, TaskExecutionResult, TriggerEvent,
)
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_providers import CustomVariableProvider, runtime_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.automation.variable_tasks import RunRoutineTask
from products.hub.integrations.music_triggers import MusicTriggerStore
from products.hub.modules import TriggerProviderContribution, TriggerProviderRegistry
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES, ObsTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.ui.automation_page import AutomationPage, RunHistoryDetailsDialog
from shared.streamhouse_runtime.logger import Logger


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def automation(tmp_path):
    variables = VariableRegistry()
    routines = RoutineStore(tmp_path / "routines.json")
    tasks = TaskRegistry()
    custom = CustomVariableStore(tmp_path / "variables.json")
    queues = AutomationQueueStore(tmp_path / "queues.json")
    service = AutomationService(routines, tasks, custom, AutomationQueueManager(queues), variables)
    return service


class WriteOutput:
    task_type = "test.history_write"

    def __init__(self, write):
        self.write = write

    def execute(self, task, trigger):
        return TaskExecutionResult(task.task_id, task.task_type, self.write(trigger.context) is not False)


def rows(view):
    return {item.name: item for item in view.variables}


def add_writer(service, write, *, trigger_id="", queue_id=DEFAULT_AUTOMATION_QUEUE_ID):
    service.task_registry.register(WriteOutput(write))
    routine = service.routine_store.add("Snapshot", trigger_id=trigger_id, queue_id=queue_id)
    service.routine_store.add_task(routine.routine_id, name="Write", task_type=WriteOutput.task_type)
    return routine


def test_captures_only_materialized_values_at_execution_boundaries(automation):
    provider = Mock(return_value="00:00:12")
    automation.variable_registry.register(runtime_provider(lambda: {}, hub_uptime=provider))
    automation.variable_registry.register(CustomVariableProvider(automation.variable_store))
    automation.variable_store.set("global", "label", "first")

    def write(context):
        context["event.title"] = "Edited title"
        context["automation.output"] = "Task result"
        context["automation.unavailable"] = None
        context.pop("event.removed")

    routine = add_writer(automation, write)
    run = automation.run_routine(routine.routine_id, {
        "event.title": "Original title", "event.empty": "", "event.removed": "before",
    })
    assert provider.call_count == 2  # Existing preparation and before-task refresh; no history reads.
    result, = run.routine_results
    before, after = rows(result.initial_variables), rows(result.final_variables)
    assert before["event.title"].source == "Trigger"
    assert before["event.title"].value == "Original title"
    assert before["event.empty"].state == "Empty"
    assert before["hub.uptime"].source == "Built-in context"
    assert before["custom.label"].source == "Custom Variable"
    assert before["custom.label"].value == "first"
    assert "automation.output" not in before
    assert "event.removed" not in after
    assert after["automation.unavailable"].state == "Unavailable"
    assert after["automation.output"].value == "Task result"
    assert after["event.title"].source == "Task"
    assert after["event.title"].value == "Edited title"
    assert "stream.title" not in after
    assert result.execution_id and result.routine_id == routine.routine_id
    automation.variable_store.set("global", "label", "second")
    second = automation.run_routine(routine.routine_id, {
        "event.title": "New run", "event.empty": "", "event.removed": "before",
    }).routine_results[0]
    assert second.execution_id != result.execution_id
    assert rows(second.final_variables)["custom.label"].value == "second"
    assert after["custom.label"].value == "first"


def test_nested_if_and_routine_outputs_belong_to_root(automation):
    child = add_writer(automation, lambda context: context.update({"automation.nested": "yes"}))
    automation.task_registry.register(IfTask(automation.execute_child_tasks))
    automation.task_registry.register(RunRoutineTask(automation.run_nested_routine))
    parent = automation.routine_store.add("Parent", queue_id=DEFAULT_AUTOMATION_QUEUE_ID)
    automation.routine_store.add_task(
        parent.routine_id, name="Branch", task_type="core.if",
        config={"left": "yes", "operator": "equals", "right": "yes"},
        then_tasks=[TaskDefinition("child", "core.run_routine", "Nested", {"routine_id": child.routine_id})],
    )
    result, = automation.run_routine(parent.routine_id).routine_results
    assert result.succeeded
    assert not rows(result.initial_variables)
    assert rows(result.final_variables)["automation.nested"].source == "Task"
    nested, = result.task_results[0].child_results[0].nested_results
    assert nested.initial_variables is nested.final_variables is None


def test_concurrent_queues_have_isolated_context_and_execution_ids(automation):
    gate = Barrier(2)
    outputs = {}

    def write(context):
        gate.wait(timeout=5)
        context["automation.output"] = context["user.id"]
        gate.wait(timeout=5)

    one = add_writer(automation, write)
    queue = automation.queue_manager.store.add("Other")
    two = automation.routine_store.add("Other", queue_id=queue.queue_id)
    automation.routine_store.add_task(two.routine_id, name="Write", task_type=WriteOutput.task_type)
    with ThreadPoolExecutor(max_workers=2) as worker:
        runs = {
            name: worker.submit(automation.run_routine, routine.routine_id, {"user.id": name})
            for name, routine in (("first", one), ("second", two))
        }
        for name, future in runs.items():
            result, = future.result(timeout=10).routine_results
            assert result.succeeded
            outputs[name] = result.execution_id
            assert rows(result.final_variables)["automation.output"].value == name
    assert len(set(outputs.values())) == 2


@pytest.mark.parametrize("cancel", [False, True])
def test_failure_and_cancellation_keep_written_context(automation, cancel):
    def write(context):
        context["automation.before_stop"] = "Retained"
        if cancel:
            current_cancellation().cancel()
        return False

    routine = add_writer(automation, write)
    result, = automation.run_routine(routine.routine_id).routine_results
    assert not result.succeeded and result.cancelled is cancel
    assert not rows(result.initial_variables)
    assert rows(result.final_variables)["automation.before_stop"].value == "Retained"


def test_sensitive_values_are_removed_before_snapshot_and_never_logged(monkeypatch):
    log = Mock()
    monkeypatch.setattr(Logger, "debug", log)
    monkeypatch.setattr(Logger, "info", log)
    body = "sample private response"
    context = ExecutionContext({
        "event.oauth_access_token": "fixture-one",
        "custom.vendor_api_key": "fixture-two",
        "custom.database_password": "fixture-three",
        "chat.message": "private chat text",
        "event.event_data": {"nested": {"value": "private object"}},
    })
    context.update({
        "automation.http_body": body,
        "automation.http_status": "200",
        "automation.http_success": "true",
        "automation.echo": body,
        "automation.embedded": f"Server said: {body}.",
        "automation.header": "Authorization: Bearer fixture-four",
        "automation.password_text": "password=fixture-five",
    })
    first = context.snapshot()
    context["automation.http_body"] = "later response"
    second = context.snapshot()
    for snapshot in (first, second):
        captured = rows(snapshot)
        for name in ("event.oauth_access_token", "custom.vendor_api_key", "custom.database_password",
                     "chat.message", "automation.http_body", "automation.header",
                     "automation.password_text", "automation.echo"):
            assert captured[name].state == "Redacted" and captured[name].value is None
        assert captured["automation.http_status"].value == "200"
        assert captured["automation.http_success"].value == "true"
        assert captured["event.event_data"].state == "Omitted"
    assert rows(first)["automation.embedded"].state == rows(second)["automation.embedded"].state == "Redacted"
    assert all(text not in repr(first) for text in (body, "fixture-", "private chat text", "private object"))
    log.assert_not_called()


def test_snapshot_size_limits_flag_omissions():
    context = ExecutionContext({"automation.a_large": "x" * (MAX_VALUE_CHARS + 1)})
    context.update({f"automation.payload_{i}": "z" * MAX_VALUE_CHARS for i in range(280)})
    snapshot = context.snapshot()
    assert snapshot.limited
    assert rows(snapshot)["automation.a_large"].state == "Omitted"
    assert len(snapshot.variables) <= MAX_VARIABLES
    assert sum(len(row.name) + len(row.source) + len(row.value or "") for row in snapshot.variables) <= MAX_SNAPSHOT_CHARS


def test_details_filter_view_change_full_value_and_clipboard(app, monkeypatch):
    initial = ExecutionContext({"event.text": "Original", "event.empty": ""})
    text = "Long line " * 20 + "\nSecond line"
    final = ExecutionContext({
        "event.text": text, "event.secret": "fixture-not-for-clipboard", "automation.added": "New",
    })
    dialog = RunHistoryDetailsDialog({
        "routine": "Snapshot", "trigger": "Fixture", "execution_id": "one",
        "initial_variables": initial.snapshot(), "final_variables": final.snapshot(),
    })
    panel = dialog.variable_inspector
    clipboard = Mock()
    monkeypatch.setattr(QApplication, "clipboard", lambda: clipboard)
    try:
        panel.search.setText("EVENT.TEXT")
        assert panel.table.rowCount() == 1
        assert panel.table.item(0, 1).text().endswith("…")
        assert panel.table.item(0, 3).text() == "Changed"
        panel.table.selectRow(0)
        assert panel.detail.toPlainText() == text
        panel.copy_name.click()
        clipboard.setText.assert_called_once_with("event.text")
        panel.copy_value.click()
        clipboard.setText.assert_called_with(text)
        panel.view_combo.setCurrentText("Initial")
        assert panel.detail.toPlainText() == "Original"
        panel.search.setText("automation.added")
        assert panel.table.item(0, 1).text() == "Not captured"
        panel.view_combo.setCurrentText("Final")
        panel.search.setText("event.secret")
        panel.table.selectRow(0)
        assert panel.detail.toPlainText() == "Redacted" and not panel.copy_value.isEnabled()
        panel._copy_value()  # Guard is enforced even without the disabled button.
        assert clipboard.setText.call_count == 2
        assert dialog.summary_labels["Execution ID"].text() == "one"
    finally:
        dialog.close()


@pytest.fixture
def page(app, automation, tmp_path, monkeypatch):
    monkeypatch.setenv("STREAMHOUSE_DATA_DIR", str(tmp_path))
    routines = automation.routine_store
    providers = TriggerProviderRegistry()
    providers.register(TriggerProviderContribution(
        provider_id="obs.automation", owner_module_id="streamhouse.obs", display_name="OBS",
        store=ObsTriggerStore(tmp_path / "obs.json", routines), event_types=OBS_TRIGGER_TYPES,
    ))
    page = AutomationPage(
        routines, TwitchCommandTriggerStore(tmp_path / "commands.json", routines),
        TwitchEventTriggerStore(tmp_path / "twitch.json", routines),
        CoreTriggerStore(tmp_path / "core.json", routines),
        MusicTriggerStore(tmp_path / "music.json", routines), automation.task_registry, automation,
        trigger_provider_registry=providers, queue_store=automation.queue_manager.store,
        queue_manager=automation.queue_manager, variable_registry=automation.variable_registry,
    )
    page.queue_timer.stop()
    try:
        yield page
    finally:
        page.close()
        page.deleteLater()
        app.processEvents()


def test_queue_outcomes_without_runs_have_no_snapshot(app, automation, page):
    add_writer(automation, lambda context: context.update({"automation.invented": "not run"}), trigger_id="source")
    store = automation.queue_manager.store
    store.update(DEFAULT_AUTOMATION_QUEUE_ID, paused=True, max_length=1)

    def admit():
        trigger = TriggerEvent("source", "test", "event", {"event.name": "Queued"})
        result = automation.publish_trigger(trigger, defer=True)
        page.record_execution(result, "Fixture")

    admit()
    assert not page.history
    admit()
    store.update(DEFAULT_AUTOMATION_QUEUE_ID, overflow_policy="keep_latest")
    admit()
    page._poll_queues()
    app.processEvents()
    assert {entry["result"] for entry in page.history} == {"Superseded", "Rejected"}
    for entry in page.history:
        dialog = RunHistoryDetailsDialog(entry)
        assert dialog.context_table.rowCount() == 0
        assert "No captured execution context" in dialog.variable_inspector.notice.text()
        dialog.close()


@pytest.mark.parametrize("size", [25, 100, 200])
def test_burst_captures_incrementally_and_retains_only_recent_runs(app, automation, page, size):
    routine = add_writer(
        automation, lambda context: context.update({"automation.number": context["event.index"]}),
        trigger_id="source",
    )
    queue = automation.queue_manager.store.default()
    automation.queue_manager.store.update(queue.queue_id, max_length=size)
    page.select_routine(routine.routine_id)
    root_rows = []
    prepend = page._prepend_history_rows

    def track(table, entries, keys, count):
        if table is page.history_table:
            root_rows.append(len(entries))
        return prepend(table, entries, keys, count)

    page._prepend_history_rows = track
    for index in range(size):
        result = automation.publish_trigger(
            TriggerEvent("source", "test", "burst", {"event.index": str(index)}), defer=True,
        )
        page.record_execution(result)
    assert not page.history
    ticks = []
    for index in range(size):
        QTimer.singleShot(0, lambda: ticks.append(len(page.history)))
        page._poll_queues()
        app.processEvents()
        assert ticks[-1] == index + 1
    assert sum(root_rows) == size and max(root_rows) == 1
    assert len({entry["execution_id"] for entry in page.history}) == size
    entry = page.history_table.item(0, 0).data(Qt.ItemDataRole.UserRole)
    assert entry["event_id"] and entry["execution_id"]
    assert rows(entry["final_variables"])["automation.number"].value == str(size - 1)
    assert "automation.number" not in rows(entry["initial_variables"])
    if size == 200:
        oldest = page.history[-1]["execution_id"]
        execution = automation.run_routine(routine.routine_id, {"event.index": "next"})
        page.record_execution(execution)
        assert page.history_table.rowCount() == len(page.history) == 200
        assert all(entry["execution_id"] != oldest for entry in page.history)
        assert not page._pending_history_rows
