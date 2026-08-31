from __future__ import annotations

import threading
import time
import tempfile
import unittest
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QEventLoop, QThread, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from products.hub.automation.core_tasks import WaitTask
from products.hub.automation.models import (
    DEFAULT_AUTOMATION_QUEUE_ID,
    TaskExecutionResult,
)
from products.hub.automation.queues import AutomationQueueManager, AutomationQueueStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.obs_service.models import ObsConnectionState, ObsRequestResult
from products.hub.obs_service.service import ObsWebSocketService
from products.hub.obs_service.tasks import register_obs_tasks
from products.hub.twitch.commands import (
    TwitchCommandTriggerOutcome,
    TwitchCommandTriggerResult,
)
from products.hub.twitch.models import TwitchMessage
from products.hub.ui.command_worker import CommandExecutionWorker


class AutoRespondingObsService(ObsWebSocketService):
    def __init__(self) -> None:
        super().__init__()
        self.state = ObsConnectionState.CONNECTED
        self._identified = True
        self.auto_respond = True
        self.response_delay_ms = 1
        self.requests: list[tuple[str, dict[str, object], str]] = []
        self.response_scripts: dict[str, deque[tuple[bool, dict[str, object], int, str]]] = defaultdict(deque)
        self.owner_thread_checks: list[bool] = []
        self.events: list[str] = []

    def queue_response(
        self,
        request_type: str,
        *,
        succeeded: bool = True,
        response_data: dict[str, object] | None = None,
        code: int = 100,
        comment: str = "",
    ) -> None:
        self.response_scripts[request_type].append(
            (succeeded, dict(response_data or {}), code, comment)
        )

    def send_request(self, request_type, request_data=None, callback=None):
        self.owner_thread_checks.append(QThread.currentThread() == self.thread())
        request_id = uuid4().hex
        data = dict(request_data or {})
        self.requests.append((request_type, data, request_id))
        self.events.append(f"send:{request_type}")
        if callback is not None:
            self._callbacks[request_id] = callback
            self._request_types[request_id] = request_type
        if self.response_scripts[request_type]:
            succeeded, response, code, comment = self.response_scripts[request_type].popleft()
        else:
            succeeded, response, code, comment = True, {}, 100, ""
        if self.auto_respond:
            QTimer.singleShot(
                self.response_delay_ms,
                lambda: self.respond(
                    request_id,
                    request_type,
                    succeeded=succeeded,
                    response_data=response,
                    code=code,
                    comment=comment,
                ),
            )
        return request_id

    def respond(
        self,
        request_id: str,
        request_type: str,
        *,
        succeeded: bool = True,
        response_data: dict[str, object] | None = None,
        code: int = 100,
        comment: str = "",
    ) -> None:
        self.events.append(f"response:{request_type}")
        self._request_response(
            {
                "requestId": request_id,
                "requestType": request_type,
                "requestStatus": {
                    "result": succeeded,
                    "code": code,
                    "comment": comment,
                },
                "responseData": dict(response_data or {}),
            }
        )


class MarkerTask:
    task_type = "test.marker"

    def __init__(self, events: list[str]) -> None:
        self.events = events

    def execute(self, task, trigger):
        self.events.append("marker")
        return TaskExecutionResult(task.task_id, task.task_type, True, "Marked.")


class ObsCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def process_until(predicate, timeout: float = 1.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate() and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.001)
        if not predicate():
            raise AssertionError("Timed out while processing Qt events in the test.")

    def test_success_and_failure_responses_complete_with_matching_data(self) -> None:
        service = AutoRespondingObsService()
        service.queue_response("GetVersion", response_data={"obsVersion": "31.0"})
        service.queue_response(
            "SetCurrentProgramScene",
            succeeded=False,
            code=600,
            comment='No scene was found with the name "BRB2".',
        )

        success = service.request_and_wait("GetVersion", timeout_ms=100)
        failure = service.request_and_wait(
            "SetCurrentProgramScene",
            {"sceneName": "BRB2"},
            timeout_ms=100,
        )

        self.assertTrue(success.succeeded)
        self.assertEqual(success.response_data["obsVersion"], "31.0")
        self.assertFalse(failure.succeeded)
        self.assertEqual(failure.code, 600)
        self.assertIn("BRB2", failure.comment)
        self.assertNotEqual(success.request_id, failure.request_id)

    def test_multiple_worker_requests_do_not_cross_complete(self) -> None:
        service = AutoRespondingObsService()
        service.response_delay_ms = 5
        service.queue_response("RequestA", response_data={"value": "A"})
        service.queue_response("RequestB", response_data={"value": "B"})
        results: dict[str, ObsRequestResult] = {}
        threads = [
            threading.Thread(
                target=lambda name=name: results.setdefault(
                    name,
                    service.request_and_wait(name, timeout_ms=250),
                )
            )
            for name in ("RequestA", "RequestB")
        ]

        for thread in threads:
            thread.start()
        self.process_until(lambda: all(not thread.is_alive() for thread in threads))
        for thread in threads:
            thread.join()

        self.assertEqual(results["RequestA"].response_data["value"], "A")
        self.assertEqual(results["RequestB"].response_data["value"], "B")
        self.assertTrue(all(service.owner_thread_checks))

    def test_timeout_removes_waiter_and_late_response_is_ignored(self) -> None:
        service = AutoRespondingObsService()
        service.auto_respond = False

        result = service.request_and_wait("NeverReplies", timeout_ms=15)
        request_type, _data, request_id = service.requests[-1]

        self.assertFalse(result.succeeded)
        self.assertIn("Timed out", result.comment)
        self.assertNotIn(request_id, service._callbacks)
        self.assertNotIn(request_id, service._request_types)

        service.respond(request_id, request_type, response_data={"late": True})
        self.assertEqual(result.response_data, {})
        self.assertNotIn(request_id, service._callbacks)

    def test_disconnect_and_shutdown_release_worker_waits(self) -> None:
        for detail in (
            "OBS disconnected before the request completed.",
            "Hub is shutting down.",
        ):
            service = AutoRespondingObsService()
            service.auto_respond = False
            results: list[ObsRequestResult] = []
            thread = threading.Thread(
                target=lambda: results.append(
                    service.request_and_wait("LongRequest", timeout_ms=500)
                )
            )
            thread.start()
            self.process_until(lambda: bool(service._callbacks))

            service.cancel_pending_requests(detail)
            self.process_until(lambda: not thread.is_alive())
            thread.join()

            self.assertFalse(results[0].succeeded)
            self.assertEqual(results[0].comment, detail)
            self.assertEqual(service._callbacks, {})

    def test_shutdown_rejects_new_automation_requests(self) -> None:
        service = AutoRespondingObsService()
        service.cancel_pending_requests("Hub is shutting down.", stop_new=True)

        result = service.request_and_wait("GetVersion", timeout_ms=100)

        self.assertFalse(result.succeeded)
        self.assertEqual(result.comment, "Hub is shutting down.")
        self.assertEqual(service.requests, [])

    def test_queue_cancellation_releases_worker_obs_wait_and_callback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            obs = AutoRespondingObsService()
            obs.auto_respond = False
            store = RoutineStore(root / "routines.json")
            routine = store.add("Worker OBS")
            store.add_task(
                routine.routine_id,
                task_type="obs.set_program_scene",
                name="BRB",
                config={"scene": "BRB"},
            )
            store.add_task(
                routine.routine_id,
                task_type="test.marker",
                name="Must not run",
            )
            registry = TaskRegistry()
            register_obs_tasks(registry, obs)
            events: list[str] = []
            registry.register(MarkerTask(events))
            manager = AutomationQueueManager(
                AutomationQueueStore(root / "queues.json")
            )
            automation = AutomationService(
                store,
                registry,
                queue_manager=manager,
            )
            results = []
            thread = threading.Thread(
                target=lambda: results.append(
                    automation.run_routine(routine.routine_id)
                )
            )
            thread.start()
            self.process_until(
                lambda: bool(obs._callbacks)
                and manager.state(DEFAULT_AUTOMATION_QUEUE_ID)[0] is not None
            )

            self.assertTrue(
                manager.cancel_current(DEFAULT_AUTOMATION_QUEUE_ID)
            )
            self.process_until(lambda: not thread.is_alive())
            thread.join()

        routine_result = results[0].routine_results[0]
        self.assertTrue(routine_result.cancelled)
        self.assertFalse(routine_result.succeeded)
        self.assertTrue(routine_result.task_results[0].cancelled)
        self.assertEqual(events, [])
        self.assertEqual(obs._callbacks, {})
        self.assertEqual(obs._request_types, {})

    def test_reconnect_does_not_inherit_stale_waiters(self) -> None:
        service = AutoRespondingObsService()
        service.auto_respond = False
        results: list[ObsRequestResult] = []
        thread = threading.Thread(
            target=lambda: results.append(
                service.request_and_wait("OldRequest", timeout_ms=500)
            )
        )
        thread.start()
        self.process_until(lambda: bool(service._callbacks))
        ObsWebSocketService.disconnect(service)
        self.process_until(lambda: not thread.is_alive())
        thread.join()

        service.state = ObsConnectionState.CONNECTED
        service._identified = True
        service.auto_respond = True
        fresh = service.request_and_wait("FreshRequest", timeout_ms=100)

        self.assertFalse(results[0].succeeded)
        self.assertTrue(fresh.succeeded)
        self.assertEqual(service._callbacks, {})

    def test_source_visibility_confirms_lookup_and_final_explicit_state(self) -> None:
        service = AutoRespondingObsService()
        service.queue_response("GetSceneItemId", response_data={"sceneItemId": 42})

        shown = service.set_scene_item_enabled(
            "Gameplay", "NUKE", "show", timeout_ms=100
        )

        self.assertTrue(shown.succeeded)
        self.assertEqual(
            [(kind, data) for kind, data, _request_id in service.requests],
            [
                ("GetSceneItemId", {"sceneName": "Gameplay", "sourceName": "NUKE"}),
                (
                    "SetSceneItemEnabled",
                    {
                        "sceneName": "Gameplay",
                        "sceneItemId": 42,
                        "sceneItemEnabled": True,
                    },
                ),
            ],
        )

        service.requests.clear()
        service.queue_response("GetSceneItemId", response_data={"sceneItemId": 42})
        hidden = service.set_scene_item_enabled(
            "Gameplay", "NUKE", "hide", timeout_ms=100
        )
        self.assertTrue(hidden.succeeded)
        self.assertFalse(service.requests[-1][1]["sceneItemEnabled"])

    def test_source_visibility_stops_on_lookup_or_set_failure(self) -> None:
        service = AutoRespondingObsService()
        service.queue_response(
            "GetSceneItemId",
            succeeded=False,
            code=600,
            comment="Source not found.",
        )

        lookup_failure = service.set_scene_item_enabled(
            "Gameplay", "Missing", "show", timeout_ms=100
        )

        self.assertFalse(lookup_failure.succeeded)
        self.assertEqual([item[0] for item in service.requests], ["GetSceneItemId"])

        service.requests.clear()
        service.queue_response("GetSceneItemId", response_data={"sceneItemId": 42})
        service.queue_response(
            "SetSceneItemEnabled",
            succeeded=False,
            code=500,
            comment="Could not change scene item state.",
        )
        set_failure = service.set_scene_item_enabled(
            "Gameplay", "NUKE", "show", timeout_ms=100
        )
        self.assertFalse(set_failure.succeeded)
        self.assertEqual(
            [item[0] for item in service.requests],
            ["GetSceneItemId", "SetSceneItemEnabled"],
        )

    def test_filter_enable_disable_and_failure_are_confirmed(self) -> None:
        service = AutoRespondingObsService()
        enabled = service.set_source_filter_enabled(
            "Camera", "Blur", "enable", timeout_ms=100
        )
        disabled = service.set_source_filter_enabled(
            "Camera", "Blur", "disable", timeout_ms=100
        )
        service.queue_response(
            "SetSourceFilterEnabled",
            succeeded=False,
            code=601,
            comment="Filter not found.",
        )
        failed = service.set_source_filter_enabled(
            "Camera", "Missing", "enable", timeout_ms=100
        )

        self.assertTrue(enabled.succeeded)
        self.assertTrue(disabled.succeeded)
        self.assertFalse(failed.succeeded)
        self.assertTrue(service.requests[0][1]["filterEnabled"])
        self.assertFalse(service.requests[1][1]["filterEnabled"])

    def test_scene_task_waits_for_confirmation_before_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            service = AutoRespondingObsService()
            service.events = events
            service.response_delay_ms = 10
            store = RoutineStore(Path(directory) / "routines.json")
            routine = store.add("Change scene")
            store.add_task(
                routine.routine_id,
                task_type="obs.set_program_scene",
                name="BRB",
                config={"scene": "BRB"},
            )
            store.add_task(routine.routine_id, task_type="test.marker", name="Marker")
            registry = TaskRegistry()
            register_obs_tasks(registry, service)
            registry.register(MarkerTask(events))

            result = AutomationService(store, registry).run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(
            events,
            [
                "send:SetCurrentProgramScene",
                "response:SetCurrentProgramScene",
                "marker",
            ],
        )

    def test_failed_scene_task_stops_routine_before_marker(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            events: list[str] = []
            service = AutoRespondingObsService()
            service.events = events
            service.queue_response(
                "SetCurrentProgramScene",
                succeeded=False,
                code=600,
                comment="Scene not found.",
            )
            store = RoutineStore(Path(directory) / "routines.json")
            routine = store.add("Missing scene")
            store.add_task(
                routine.routine_id,
                task_type="obs.set_program_scene",
                name="Missing",
                config={"scene": "Missing"},
            )
            store.add_task(routine.routine_id, task_type="test.marker", name="Marker")
            registry = TaskRegistry()
            register_obs_tasks(registry, service)
            registry.register(MarkerTask(events))

            result = AutomationService(store, registry).run_routine(routine.routine_id)

        self.assertFalse(result.succeeded)
        self.assertNotIn("marker", events)

    def test_show_wait_hide_preserves_confirmation_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = AutoRespondingObsService()
            service.queue_response("GetSceneItemId", response_data={"sceneItemId": 7})
            service.queue_response("GetSceneItemId", response_data={"sceneItemId": 7})
            store = RoutineStore(Path(directory) / "routines.json")
            routine = store.add("Overlay")
            for action in ("show", "hide"):
                store.add_task(
                    routine.routine_id,
                    task_type="obs.set_scene_item_enabled",
                    name=action.title(),
                    config={"scene": "Gameplay", "source": "NUKE", "action": action},
                )
                if action == "show":
                    store.add_task(
                        routine.routine_id,
                        task_type="core.wait",
                        name="Wait",
                        config={"duration": "20", "unit": "milliseconds"},
                    )
            registry = TaskRegistry()
            register_obs_tasks(registry, service)
            registry.register(
                WaitTask(wait=lambda _milliseconds: service.events.append("wait") or True)
            )

            result = AutomationService(store, registry).run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(
            service.events,
            [
                "send:GetSceneItemId",
                "response:GetSceneItemId",
                "send:SetSceneItemEnabled",
                "response:SetSceneItemEnabled",
                "wait",
                "send:GetSceneItemId",
                "response:GetSceneItemId",
                "send:SetSceneItemEnabled",
                "response:SetSceneItemEnabled",
            ],
        )

    def test_command_execution_worker_marshals_obs_send_to_owner_thread(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = AutoRespondingObsService()
            store = RoutineStore(Path(directory) / "routines.json")
            routine = store.add("Worker OBS", trigger_id="command.worker")
            store.add_task(
                routine.routine_id,
                task_type="obs.set_program_scene",
                name="BRB",
                config={"scene": "BRB"},
            )
            registry = TaskRegistry()
            register_obs_tasks(registry, service)
            automation = AutomationService(store, registry)
            command = TwitchCommandTriggerResult(
                TwitchCommandTriggerOutcome.READY,
                invocation="worker",
                trigger_id="command.worker",
                routine_id=routine.routine_id,
            )
            message = TwitchMessage("viewer", "!worker", datetime.now())
            worker = CommandExecutionWorker(automation, command, message)
            outcomes = []
            worker.signals.completed.connect(outcomes.append)
            pool = QThreadPool()
            pool.setMaxThreadCount(1)
            pool.start(worker)

            self.process_until(lambda: bool(outcomes))
            pool.waitForDone(500)

        self.assertTrue(outcomes[0].execution.succeeded)
        self.assertTrue(service.owner_thread_checks)
        self.assertTrue(all(service.owner_thread_checks))


if __name__ == "__main__":
    unittest.main()
