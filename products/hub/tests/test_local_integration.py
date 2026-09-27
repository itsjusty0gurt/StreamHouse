from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import threading
import time
from urllib.request import Request, urlopen

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.integrations.local_api import (
    HUB_INTEGRATION_HOST,
    HUB_INTEGRATION_PATH,
    HUB_INTEGRATION_PROTOCOL_VERSION,
    HubIntegrationController,
    LocalHubIntegrationServer,
)


class _Task:
    task_type = "test.integration"

    def __init__(self) -> None:
        self.triggers: list[TriggerEvent] = []
        self.threads: list[QThread] = []

    def execute(
        self,
        task: TaskDefinition,
        trigger: TriggerEvent,
    ) -> TaskExecutionResult:
        self.triggers.append(trigger)
        self.threads.append(QThread.currentThread())
        return TaskExecutionResult(task.task_id, task.task_type, True)


def _controller(root: Path) -> tuple[HubIntegrationController, RoutineStore, _Task]:
    QApplication.instance() or QApplication([])
    routines = RoutineStore(root / "routines.json")
    queues = AutomationQueueStore(root / "queues.json")
    queues.load()
    handler = _Task()
    registry = TaskRegistry()
    registry.register(handler)
    service = AutomationService(
        routines,
        registry,
        queue_manager=AutomationQueueManager(queues),
    )
    return HubIntegrationController(service), routines, handler


def _request(request_type: str, **values: object) -> dict[str, object]:
    return {
        "protocol_version": HUB_INTEGRATION_PROTOCOL_VERSION,
        "type": request_type,
        **values,
    }


def test_list_uses_stable_ids_and_tracks_rename_delete() -> None:
    with tempfile.TemporaryDirectory() as directory:
        controller, store, _handler = _controller(Path(directory))
        routine = store.add("Before rename")
        routine_id = routine.routine_id

        listed = controller.dispatch(_request("list_routines"))["routines"]
        assert listed == [{"id": routine_id, "name": "Before rename", "group": ""}]

        store.update(routine_id, name="After rename")
        listed = controller.dispatch(_request("list_routines"))["routines"]
        assert listed == [{"id": routine_id, "name": "After rename", "group": ""}]

        store.delete(routine_id)
        assert controller.dispatch(_request("list_routines"))["routines"] == []


def test_run_uses_normal_queue_path_and_integration_origin() -> None:
    with tempfile.TemporaryDirectory() as directory:
        controller, store, handler = _controller(Path(directory))
        routine = store.add("External")
        store.add_task(
            routine.routine_id,
            task_type=_Task.task_type,
            name="Action",
        )

        result = controller.dispatch(_request("run_routine", routine_id=routine.routine_id))

        assert result["success"] is True
        assert len(handler.triggers) == 1
        assert handler.triggers[0].service == "integration"
        assert handler.triggers[0].trigger_type == "touch_portal"
        assert handler.triggers[0].context == {}


def test_server_thread_request_is_marshaled_to_the_qt_owner_thread() -> None:
    with tempfile.TemporaryDirectory() as directory:
        application = QApplication.instance() or QApplication([])
        controller, store, handler = _controller(Path(directory))
        routine = store.add("Threaded")
        store.add_task(
            routine.routine_id,
            task_type=_Task.task_type,
            name="Action",
        )
        responses: list[dict[str, object]] = []
        worker = threading.Thread(
            target=lambda: responses.append(
                controller.dispatch(
                    _request("run_routine", routine_id=routine.routine_id)
                )
            )
        )
        worker.start()
        deadline = time.monotonic() + 2
        while worker.is_alive() and time.monotonic() < deadline:
            application.processEvents()
            time.sleep(0.005)
        worker.join(timeout=1)

        assert responses[0]["success"] is True
        assert handler.threads == [application.thread()]


def test_unknown_disabled_malformed_and_protocol_requests_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as directory:
        controller, store, handler = _controller(Path(directory))
        routine = store.add("Disabled")
        store.update(routine.routine_id, enabled=False)

        cases = (
            (_request("run_routine", routine_id="missing"), "unknown_routine"),
            (_request("run_routine", routine_id=routine.routine_id), "routine_disabled"),
            (_request("run_routine"), "malformed_request"),
            ({"protocol_version": 99, "type": "list_routines"}, "unsupported_protocol"),
        )
        for payload, code in cases:
            result = controller.dispatch(payload)
            assert result["success"] is False
            assert result["error"]["code"] == code
        assert handler.triggers == []


def test_server_binds_loopback_and_stops_cleanly() -> None:
    observed: list[dict[str, object]] = []

    def dispatch(payload: dict[str, object]) -> dict[str, object]:
        observed.append(payload)
        return {
            "protocol_version": 1,
            "type": "routines",
            "success": True,
            "routines": [],
        }

    server = LocalHubIntegrationServer(dispatch, port=0)
    server.start()
    try:
        request = Request(
            f"http://{HUB_INTEGRATION_HOST}:{server.port}{HUB_INTEGRATION_PATH}",
            data=json.dumps(_request("list_routines")).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=2) as response:
            result = json.loads(response.read().decode("utf-8"))
        assert result["type"] == "routines"
        assert observed == [_request("list_routines")]
    finally:
        server.stop()
    assert server.running is False


def test_non_loopback_binding_is_rejected_and_shutdown_rejects_late_work() -> None:
    with tempfile.TemporaryDirectory() as directory:
        controller, _store, _handler = _controller(Path(directory))
        controller.shutdown()
        result = controller.dispatch(_request("list_routines"))
        assert result["error"]["code"] == "hub_shutting_down"

    try:
        LocalHubIntegrationServer(lambda _payload: {}, host="0.0.0.0")
    except ValueError as error:
        assert "loopback-only" in str(error)
    else:
        raise AssertionError("A non-loopback listener was accepted.")


def test_occupied_port_fails_cleanly_and_rebinds_after_shutdown() -> None:
    first = LocalHubIntegrationServer(lambda _payload: {}, port=0)
    first.start()
    port = first.port
    second = LocalHubIntegrationServer(lambda _payload: {}, port=port)
    try:
        try:
            second.start()
        except OSError:
            pass
        else:
            raise AssertionError("A live integration listener was stolen.")
    finally:
        first.stop()

    second.start()
    assert second.running is True
    second.stop()
