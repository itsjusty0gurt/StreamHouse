from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.file_tasks import register_file_tasks
from products.hub.automation.models import TaskDefinition, TaskExecutionResult, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_outputs import generated_output_definitions
from products.hub.automation.variable_providers import CustomVariableProvider, context_provider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.automation.variable_tasks import RunRoutineTask, register_variable_tasks
from products.hub.twitch.tasks import SendTwitchChatMessageTask


class CaptureContextTask:
    task_type = "test.capture_context"

    def __init__(self) -> None:
        self.contexts: list[dict[str, str]] = []

    def execute(self, task, trigger):
        self.contexts.append(dict(trigger.context))
        return TaskExecutionResult(task.task_id, task.task_type, True, "Captured.")


class CaptureTwitchService:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, message: str, *, as_bot: bool = True) -> bool:
        self.messages.append(message)
        return True


class AutomationVariableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.variable_path = root / "variables.json"
        self.variable_store = CustomVariableStore(self.variable_path)
        self.variable_store.load()
        self.routine_store = RoutineStore(root / "routines.json")
        self.registry = TaskRegistry()
        register_variable_tasks(self.registry, self.variable_store)
        self.capture = CaptureContextTask()
        self.registry.register(self.capture)
        self.variable_registry = VariableRegistry()
        self.variable_registry.register(context_provider())
        self.variable_registry.register(CustomVariableProvider(self.variable_store))
        self.service = AutomationService(
            self.routine_store,
            self.registry,
            self.variable_store,
            variable_registry=self.variable_registry,
        )
        self.registry.register(RunRoutineTask(self.service.run_nested_routine))

    def _register_file_and_chat_tasks(self) -> CaptureTwitchService:
        register_file_tasks(self.registry)
        twitch = CaptureTwitchService()
        self.registry.register(
            SendTwitchChatMessageTask(twitch, self.variable_registry)
        )
        return twitch

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_global_variables_survive_reload_but_session_variables_do_not(self) -> None:
        self.variable_store.set("global", "death_count", "12")
        self.variable_store.set("session", "current_song", "Test Track")

        loaded = CustomVariableStore(self.variable_path)
        loaded.load()

        self.assertEqual(loaded.values(), {"death_count": "12"})
        self.assertEqual(loaded.scope_of("death_count"), "global")
        self.assertEqual(loaded.scope_of("current_song"), "")

    def test_custom_variables_are_isolated_in_the_custom_namespace(self) -> None:
        self.variable_store.set("global", "user", "Someone")
        provider = CustomVariableProvider(self.variable_store)
        self.assertEqual(provider.definitions()[0].name, "custom.user")

    def test_variable_ids_do_not_accept_placeholder_syntax(self) -> None:
        self.assertEqual(
            CustomVariableStore.validate_name("random_line"),
            "random_line",
        )
        with self.assertRaises(ValueError):
            CustomVariableStore.validate_name("{random_line}")

    def test_generated_definitions_cover_output_tasks(self) -> None:
        self.assertEqual(
            tuple(item.name for item in generated_output_definitions(
                "core.file_random_line",
                {"variable": "random_line"},
            )),
            ("automation.random_line",),
        )
        self.assertEqual(
            tuple(item.name for item in generated_output_definitions(
                "core.logic_get_input",
                {"name": "answer"},
            )),
            ("automation.answer", "automation.answer_accepted"),
        )
        self.assertEqual(
            tuple(item.name for item in generated_output_definitions(
                "core.format_duration",
                {"output_variable": "followage"},
            )),
            ("automation.followage", "automation.followage_status"),
        )
        self.assertEqual(
            tuple(item.name for item in generated_output_definitions(
                "core.select_text",
                {"output_variable": "command_response"},
            )),
            ("automation.command_response",),
        )
        self.assertEqual(
            tuple(item.name for item in generated_output_definitions(
                "twitch.get_channel_information_field",
                {"field": "discord_url"},
            )),
            (
                "automation.discord_url",
                "automation.discord_url_status",
                "automation.channel_information_available",
                "automation.channel_information_status",
            ),
        )
        self.assertEqual(
            tuple(item.name for item in generated_output_definitions(
                "twitch.build_social_links_message",
                {},
            )),
            (
                "automation.social_links_message",
                "automation.social_links_message_status",
                "automation.channel_information_available",
                "automation.channel_information_status",
            ),
        )

    def test_file_output_is_available_only_after_producer_and_across_later_tasks(self) -> None:
        twitch = self._register_file_and_chat_tasks()
        path = Path(self.temporary.name) / "responses.txt"
        path.write_text("Yippie!", encoding="utf-8")
        routine = self.routine_store.add("Generated output")
        self.routine_store.add_task(
            routine.routine_id,
            task_type="core.file_random_line",
            name="Choose response",
            config={"path": str(path), "variable": "random_line"},
        )
        for name in ("First message", "Second message"):
            self.routine_store.add_task(
                routine.routine_id,
                task_type="twitch.send_chat_message",
                name=name,
                config={"message": "{automation.random_line}", "as_bot": True},
            )

        supplied_context: dict[str, str] = {}
        result = self.service.run_routine(routine.routine_id, supplied_context)

        self.assertTrue(result.succeeded)
        self.assertEqual(twitch.messages, ["Yippie!", "Yippie!"])
        self.assertNotIn("automation.random_line", supplied_context)

    def test_generated_output_is_unavailable_before_producer_and_never_leaks(self) -> None:
        twitch = self._register_file_and_chat_tasks()
        path = Path(self.temporary.name) / "responses.txt"
        path.write_text("Later", encoding="utf-8")
        before = self.routine_store.add("Before producer")
        self.routine_store.add_task(
            before.routine_id,
            task_type="twitch.send_chat_message",
            name="Too early",
            config={"message": "{automation.random_line}", "as_bot": True},
        )
        self.routine_store.add_task(
            before.routine_id,
            task_type="core.file_random_line",
            name="Choose response",
            config={"path": str(path), "variable": "random_line"},
        )
        next_run = self.routine_store.add("Unrelated run")
        self.routine_store.add_task(
            next_run.routine_id,
            task_type="twitch.send_chat_message",
            name="Must not leak",
            config={"message": "{automation.random_line}", "as_bot": True},
        )

        early_result = self.service.run_routine(before.routine_id)
        next_result = self.service.run_routine(next_run.routine_id)

        self.assertFalse(early_result.succeeded)
        self.assertFalse(next_result.succeeded)
        self.assertEqual(twitch.messages, [])
        self.assertIn(
            "Unknown variable",
            early_result.routine_results[0].task_results[0].detail,
        )

    def test_generated_command_and_keyword_context_can_coexist(self) -> None:
        twitch = self._register_file_and_chat_tasks()
        path = Path(self.temporary.name) / "responses.txt"
        path.write_text("chosen", encoding="utf-8")
        routine = self.routine_store.add("Combined context")
        self.routine_store.add_task(
            routine.routine_id,
            task_type="core.file_random_line",
            name="Choose response",
            config={"path": str(path), "variable": "random_line"},
        )
        self.routine_store.add_task(
            routine.routine_id,
            task_type="twitch.send_chat_message",
            name="Report context",
            config={
                "message": "{command.data}|{keyword.match}|{automation.random_line}",
                "as_bot": True,
            },
        )

        result = self.service.run_routine(
            routine.routine_id,
            {"command_data": "input", "keyword.match": "coffee"},
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(twitch.messages, ["input|coffee|chosen"])

    def test_later_generated_output_predictably_replaces_same_name(self) -> None:
        twitch = self._register_file_and_chat_tasks()
        first = Path(self.temporary.name) / "first.txt"
        second = Path(self.temporary.name) / "second.txt"
        first.write_text("first", encoding="utf-8")
        second.write_text("second", encoding="utf-8")
        routine = self.routine_store.add("Overwrite output")
        for path in (first, second):
            self.routine_store.add_task(
                routine.routine_id,
                task_type="core.file_random_line",
                name=f"Read {path.stem}",
                config={"path": str(path), "variable": "choice"},
            )
        self.routine_store.add_task(
            routine.routine_id,
            task_type="twitch.send_chat_message",
            name="Use latest",
            config={"message": "{automation.choice}", "as_bot": True},
        )

        result = self.service.run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(twitch.messages, ["second"])

    def test_nested_routine_shares_routine_variables_with_parent(self) -> None:
        child = self.routine_store.add("Child")
        self.routine_store.add_task(
            child.routine_id,
            task_type="core.adjust_variable",
            name="Add one",
            config={"name": "automation.score", "amount": 1},
        )
        parent = self.routine_store.add("Parent")
        self.routine_store.add_task(
            parent.routine_id,
            task_type="core.create_routine_variable",
            name="Create score",
            config={"name": "score", "value": "1"},
        )
        self.routine_store.add_task(
            parent.routine_id,
            task_type="core.run_routine",
            name="Run child",
            config={"routine_id": child.routine_id, "stop_on_failure": True},
        )
        self.routine_store.add_task(
            parent.routine_id,
            task_type=self.capture.task_type,
            name="Read score",
        )

        result = self.service.run_routine(parent.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(self.capture.contexts[-1]["automation.score"], "2")
        self.assertNotIn("score", self.variable_store.values())

    def test_global_variable_is_available_to_later_executions(self) -> None:
        create = self.routine_store.add("Create")
        self.routine_store.add_task(
            create.routine_id,
            task_type="core.create_global_variable",
            name="Remember winner",
            config={"name": "last_winner", "value": "{user.display_name}"},
        )
        read = self.routine_store.add("Read")
        self.routine_store.add_task(
            read.routine_id,
            task_type=self.capture.task_type,
            name="Read winner",
        )

        self.assertTrue(
            self.service.run_routine(create.routine_id, {"user": "ViewerOne"}).succeeded
        )
        self.assertTrue(self.service.run_routine(read.routine_id).succeeded)

        self.assertEqual(self.capture.contexts[-1]["custom.last_winner"], "ViewerOne")
        self.assertEqual(self.variable_store.global_values["last_winner"], "ViewerOne")

    def test_recursive_routine_call_is_blocked(self) -> None:
        routine = self.routine_store.add("Loop")
        self.routine_store.add_task(
            routine.routine_id,
            task_type="core.run_routine",
            name="Run itself",
            config={"routine_id": routine.routine_id, "stop_on_failure": True},
        )

        result = self.service.run_routine(routine.routine_id)

        self.assertFalse(result.succeeded)
        self.assertIn(
            "Routine call loop blocked",
            result.routine_results[0].task_results[0].detail,
        )

    def test_routine_variables_do_not_leak_to_sibling_routines(self) -> None:
        first = self.routine_store.add("First", trigger_id="shared-trigger")
        self.routine_store.add_task(
            first.routine_id,
            task_type="core.create_routine_variable",
            name="Temporary value",
            config={"name": "temporary_value", "value": "secret"},
        )
        second = self.routine_store.add("Second", trigger_id="shared-trigger")
        self.routine_store.add_task(
            second.routine_id,
            task_type=self.capture.task_type,
            name="Capture",
        )

        result = self.service.publish_trigger(
            TriggerEvent("shared-trigger", "test", "shared", {})
        )

        self.assertTrue(result.succeeded)
        self.assertNotIn("automation.temporary_value", self.capture.contexts[-1])

    def test_trigger_context_is_available_to_every_task_and_never_leaks(self) -> None:
        routine = self.routine_store.add("Context lifecycle", trigger_id="context")
        self.routine_store.add_task(
            routine.routine_id,
            task_type=self.capture.task_type,
            name="First capture",
        )
        self.routine_store.add_task(
            routine.routine_id,
            task_type=self.capture.task_type,
            name="Second capture",
        )

        command = self.service.publish_trigger(
            TriggerEvent(
                "context",
                "twitch",
                "command",
                {"command": "title", "command_data": "Tonight we play"},
            )
        )
        unrelated = self.service.publish_trigger(
            TriggerEvent(
                "context",
                "twitch",
                "keyword_phrase",
                {"keyword.match": "coffee"},
            )
        )

        self.assertTrue(command.succeeded and unrelated.succeeded)
        self.assertEqual(
            [item["command.data"] for item in self.capture.contexts[:2]],
            ["Tonight we play", "Tonight we play"],
        )
        self.assertNotIn("command.data", self.capture.contexts[2])
        self.assertNotIn("command.name", self.capture.contexts[3])
        self.assertEqual(self.capture.contexts[2]["keyword.match"], "coffee")


if __name__ == "__main__":
    unittest.main()
