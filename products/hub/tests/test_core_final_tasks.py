from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication

from products.hub.automation.cancellation import AutomationCancellation, cancellation_scope
from products.hub.automation.core_tasks import PlayAudioTask, StopAudioTask
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.http_request_task import HTTP_METHODS, MAX_HTTP_BYTES, HttpRequestTask, validate_http_config
from products.hub.automation.keyboard_press_task import KeyboardPressTask, shortcut_keys
from products.hub.automation.logic_tasks import IfTask
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.task_catalog import CORE_TASK_METADATA
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_outputs import generated_output_definitions
from products.hub.automation.variable_registry import VariableDataType, VariableRegistry
from products.hub.ui.automation_page import TaskEditorDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def server():
    requests = []
    unblock = Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def serve(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            requests.append((self.command, self.path, dict(self.headers), body))
            if self.path == "/drop":
                self.close_connection = True
                return
            if self.path == "/hang":
                unblock.wait(5)
                return
            status = {"/redirect": 302, "/denied": 403}.get(self.path, 200)
            response = {
                "/large": b"x" * (MAX_HTTP_BYTES + 1),
                "/denied": b"private remote error",
                "/invalid-utf8": b"\xff",
            }.get(self.path, '{"value":"ž"}'.encode())
            self.send_response(status)
            self.send_header("Content-Length", str(len(response)))
            if status == 302:
                self.send_header("Location", "/check")
            self.end_headers()
            try:
                self.wfile.write(response)
            except OSError:
                pass  # The client can cancel or close oversized responses.

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = serve

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_port}", requests
    finally:
        unblock.set()
        httpd.shutdown()
        httpd.server_close()
        worker.join()


def execute(handler, config=None, context=None):
    trigger = TriggerEvent("test", "core", "manual", context={} if context is None else context)
    result = handler.execute(TaskDefinition("task", handler.task_type, "Test", config or {}), trigger)
    return result, trigger.context


@pytest.mark.parametrize("method", HTTP_METHODS)
def test_http_method_variables_and_outputs(app, server, method):
    base, requests = server
    result, context = execute(HttpRequestTask(VariableRegistry()), {
        "url": base + "/{automation.endpoint}", "method": method,
        "headers": "Accept: {automation.media}\nContent-Type: application/json",
        "body": "{automation.payload}" if method != "GET" else "",
        "result_prefix": "first",
    }, {"automation.endpoint": "check", "automation.media": "application/json", "automation.payload": '{"sent":true}'})
    assert result.succeeded, result.detail
    assert len(requests) == 1
    seen_method, path, headers, body = requests[0]
    assert (seen_method, path) == (method, "/check")
    assert headers["Accept"] == "application/json"
    assert body == (b"" if method == "GET" else b'{"sent":true}')
    assert context["automation.first_http_status"] == "200"
    assert context["automation.first_http_body"] == '{"value":"ž"}'
    assert context["automation.first_http_success"] == "true"
    assert context["automation.first_http_error"] == ""
    assert "automation.first_http_body" not in dict(AutomationService.safe_execution_context(context))
    assert "ž" not in result.detail


@pytest.mark.parametrize(("path", "detail", "status"), [
    ("/redirect", "redirects are not followed", "302"),
    ("/denied", "request failed", "403"),
    ("/large", "exceeds 256 KiB", "200"),
    ("/invalid-utf8", "not UTF-8", "200"),
    ("/drop", "failed to complete", "0"),
    ("/hang", "timed out", "0"),
])
def test_http_failure_is_bounded_private_and_does_not_retry(app, server, path, detail, status):
    base, requests = server
    result, context = execute(HttpRequestTask(VariableRegistry()), {
        "url": base + path, "method": "POST", "timeout_seconds": 1,
        "body": "{automation.payload}",
    }, {"automation.payload": '{"value":1}'})
    assert not result.succeeded and detail in result.detail
    assert context["automation.http_status"] == status
    assert context["automation.http_body"] == ""
    assert context["automation.http_success"] == "false"
    assert "private remote error" not in result.detail
    assert len(requests) == 1


def test_http_cancellation(app, server):
    base, _ = server
    cancellation = AutomationCancellation()
    QTimer.singleShot(0, cancellation.cancel)
    with cancellation_scope(cancellation):
        result, _ = execute(HttpRequestTask(VariableRegistry()), {"url": base + "/hang"})
    assert not result.succeeded and "cancelled" in result.detail.lower()


@pytest.mark.parametrize("config", [
    {"url": "https://user:secret@example.test"},
    {"url": "https://example.test/status?token=secret"},
    {"url": "http://192.168.0.1"},
    {"url": "https://{automation.host}/"},
    {"headers": "Authorization: Bearer secret"},
    {"headers": "Accept: application/json\nCookie: credential"},
    {"method": "POST", "body": '{"password":"secret"}'},
    {"method": "OPTIONS"},
    {"timeout_seconds": float("nan")},
    {"credentials": {"bearer": "secret"}},
])
def test_http_rejects_unsafe_configs(app, config):
    values = {"url": "https://example.test/", **config}
    with pytest.raises(ValueError):
        validate_http_config(values)
    result, _ = execute(HttpRequestTask(VariableRegistry()), values)
    assert not result.succeeded and "secret" not in result.detail


def test_http_outputs_reach_later_tasks_but_not_other_runs(app, server, tmp_path):
    base, _ = server
    variables = VariableRegistry()
    registry = TaskRegistry()
    registry.register(HttpRequestTask(variables))
    observed = []

    class ReadOutput:
        task_type = "test.read_http_output"

        def execute(self, task, trigger):
            observed.append(trigger.context.get("automation.http_body"))
            return TaskExecutionResult(task.task_id, task.task_type, True)

    registry.register(ReadOutput())
    routines = RoutineStore(tmp_path / "routines.json")
    first, second = (routines.add(name) for name in ("Request", "Unrelated"))
    routines.add_task(
        first.routine_id, task_type="core.if", name="True branch",
        config={"left": "yes", "operator": "equals", "right": "yes"},
        then_tasks=[TaskDefinition("request", "core.http_request", "Call", {"url": base + "/check"})],
    )
    for routine in (first, second):
        routines.add_task(routine.routine_id, task_type=ReadOutput.task_type, name="Read", config={})
    service = AutomationService(
        routines, registry, CustomVariableStore(tmp_path / "variables.json"),
        AutomationQueueManager(AutomationQueueStore(tmp_path / "queues.json")), variables,
    )
    registry.register(IfTask(service.execute_child_tasks))
    run = service.run_routine(first.routine_id)
    assert run.succeeded
    assert "automation.http_body" not in dict(run.routine_results[0].context_values)
    assert service.run_routine(second.routine_id).succeeded
    assert observed == ['{"value":"ž"}', None]
    assert variables.resolve("automation.http_body") is None


def test_keyboard_press_sends_one_balanced_chord_and_releases_partial_input():
    keyboard = Mock(supported=True)
    keyboard.is_down.return_value = False
    keyboard.send.side_effect = [6, 2, 1, 1]
    handler = KeyboardPressTask(keyboard)
    config = {"shortcut": "Ctrl+Shift+F9"}
    assert shortcut_keys("F9") == (0x78,)
    assert execute(handler, config)[0].succeeded
    keyboard.send.assert_called_once_with((
        (0x11, False), (0x10, False), (0x78, False),
        (0x78, True), (0x10, True), (0x11, True),
    ))
    assert not execute(handler, config)[0].succeeded
    assert keyboard.send.call_args_list[-2].args == (((0x10, True), (0x11, True)),)
    assert keyboard.send.call_args_list[-1].args == (((0x11, True),),)


def test_keyboard_press_refuses_held_keys_invalid_keys_and_unsupported_platform():
    keyboard = Mock(supported=True)
    keyboard.is_down.return_value = True
    handler = KeyboardPressTask(keyboard)
    assert "held keys" in execute(handler, {"shortcut": "Ctrl+B"})[0].detail
    assert not execute(handler, {"shortcut": "Meta+B"})[0].succeeded
    keyboard.supported = False
    assert "Windows" in execute(handler, {"shortcut": "Ctrl+B"})[0].detail
    keyboard.send.assert_not_called()


def test_stop_audio_only_stops_owned_playback_and_is_safe_when_idle():
    playback = PlayAudioTask()
    first, second, unrelated = Mock(), Mock(), Mock()
    playback._play_background(first, Mock())
    playback._play_background(second, Mock())
    task = StopAudioTask(playback)
    assert execute(task)[0].succeeded
    first.stop.assert_called_once()
    second.stop.assert_called_once()
    unrelated.stop.assert_not_called()
    assert "No Hub audio" in execute(task)[0].detail
    assert not playback._active_players
    playback._play_background(first, Mock())
    playback._play_background(second, Mock())
    first.stop.side_effect = RuntimeError("private device detail")
    result, _ = execute(task)
    assert not result.succeeded and "private" not in result.detail
    assert second.stop.call_count == 2


def test_new_task_editors_and_output_types(app):
    assert {"core.http_request", "core.keyboard_press", "core.stop_audio"} <= {
        item.task_type for item in CORE_TASK_METADATA
    }
    http = TaskEditorDialog("core.http_request", variable_registry=VariableRegistry())
    http.field_widgets["core.http_request"]["url"].setText("https://example.test/status")
    assert http.values()["config"]["method"] == "GET"
    assert set(http.variable_picker_buttons) == {"url", "headers", "body"}
    types = {item.name: item.data_type for item in generated_output_definitions("core.http_request", {})}
    assert types["automation.http_status"] == VariableDataType.INTEGER
    assert types["automation.http_success"] == VariableDataType.BOOLEAN
    keyboard = TaskEditorDialog("core.keyboard_press")
    keyboard.field_widgets["core.keyboard_press"]["shortcut"].setKeySequence(QKeySequence("Ctrl+Shift+F9"))
    assert keyboard.values()["config"] == {"shortcut": "Ctrl+Shift+F9"}
    assert TaskEditorDialog("core.stop_audio").values()["config"] == {}
