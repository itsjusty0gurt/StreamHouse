from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.logic_tasks import (
    GetInputTask,
    GetRandomNumberTask,
    RandomChoiceTask,
    evaluate_condition,
    register_logic_tasks,
)
from products.hub.automation.models import TaskExecutionResult, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_tasks import register_variable_tasks


class CaptureTask:
    task_type = "test.capture"

    def __init__(self) -> None:
        self.contexts: list[dict[str, str]] = []

    def execute(self, task, trigger):
        self.contexts.append(dict(trigger.context))
        return TaskExecutionResult(task.task_id, task.task_type, True, "Captured.")


class AutomationLogicTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.variables = CustomVariableStore(root / "variables.json")
        self.variables.load()
        self.routines = RoutineStore(root / "routines.json")
        self.registry = TaskRegistry()
        register_variable_tasks(self.registry, self.variables)
        self.capture = CaptureTask()
        self.registry.register(self.capture)
        self.service = AutomationService(self.routines, self.registry, self.variables)
        register_logic_tasks(self.registry, self.service)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_task(self, routine_id: str, task_type: str, config=None, name="Task"):
        return self.routines.add_task(
            routine_id,
            task_type=task_type,
            name=name,
            config=config or {},
        )

    def test_condition_comparisons_render_variables(self) -> None:
        context = {"automation.score": "12", "automation.name": "Sally"}
        self.assertTrue(evaluate_condition("{automation.score}", "greater_than", "10", context))
        self.assertTrue(evaluate_condition("{automation.name}", "equals_ignore_case", "sally", context))
        self.assertTrue(evaluate_condition("automation.score", "exists", "", context))
        self.assertTrue(evaluate_condition("automation.missing", "not_exists", "", context))

    def test_break_stops_remaining_tasks_successfully(self) -> None:
        routine = self.routines.add("Break")
        self.add_task(routine.routine_id, "core.logic_break")
        self.add_task(routine.routine_id, self.capture.task_type)

        result = self.service.run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(result.routine_results[0].flow_action, "break")
        self.assertEqual(self.capture.contexts, [])

    def test_false_if_continues_unless_break_is_enabled(self) -> None:
        routine = self.routines.add("Condition")
        condition = self.add_task(
            routine.routine_id,
            "core.logic_if_else",
            {"left": "1", "operator": "equals", "right": "2"},
        )
        self.add_task(routine.routine_id, self.capture.task_type)

        self.assertTrue(self.service.run_routine(routine.routine_id).succeeded)
        self.assertEqual(len(self.capture.contexts), 1)

        self.routines.update_task(
            routine.routine_id,
            condition.task_id,
            config={
                "left": "1",
                "operator": "equals",
                "right": "2",
                "break_if_false": True,
            },
        )
        self.capture.contexts.clear()
        result = self.service.run_routine(routine.routine_id)
        self.assertTrue(result.succeeded)
        self.assertEqual(self.capture.contexts, [])

    def test_if_else_runs_selected_routine_with_shared_variables(self) -> None:
        true_branch = self.routines.add("True branch")
        self.add_task(
            true_branch.routine_id,
            "core.create_routine_variable",
            {"name": "answer", "value": "yes"},
        )
        parent = self.routines.add("Parent")
        self.add_task(
            parent.routine_id,
            "core.logic_if_else",
            {
                "left": "{automation.enabled_flag}",
                "operator": "is_true",
                "right": "",
                "true_routine_id": true_branch.routine_id,
            },
        )
        self.add_task(parent.routine_id, self.capture.task_type)

        result = self.service.run_routine(parent.routine_id, {"automation.enabled_flag": "true"})

        self.assertTrue(result.succeeded)
        self.assertEqual(self.capture.contexts[-1]["automation.answer"], "yes")

    def test_switch_runs_first_matching_case(self) -> None:
        hydrate = self.routines.add("Hydrate")
        self.add_task(
            hydrate.routine_id,
            "core.create_routine_variable",
            {"name": "selected_case", "value": "hydrate"},
        )
        parent = self.routines.add("Switch")
        self.add_task(
            parent.routine_id,
            "core.logic_switch",
            {
                "input": "{event.reward}",
                "cases": {"Hydrate": hydrate.routine_id},
                "ignore_case": True,
            },
        )
        self.add_task(parent.routine_id, self.capture.task_type)

        result = self.service.run_routine(parent.routine_id, {"event.reward": "HYDRATE"})

        self.assertTrue(result.succeeded)
        self.assertEqual(self.capture.contexts[-1]["automation.selected_case"], "hydrate")

    def test_random_choice_runs_a_weighted_routine_with_shared_variables(self) -> None:
        selected = self.routines.add("Rare sound")
        self.add_task(
            selected.routine_id,
            "core.create_routine_variable",
            {"name": "selected_choice", "value": "rare"},
        )
        parent = self.routines.add("Random choice")
        definition = self.add_task(
            parent.routine_id,
            "core.logic_random_choice",
            {
                "choices": [
                    {
                        "label": "Rare",
                        "weight": 5,
                        "routine_id": selected.routine_id,
                    }
                ]
            },
        )
        self.add_task(parent.routine_id, self.capture.task_type)

        result = RandomChoiceTask(
            self.service.run_nested_routine,
            self.service.routine_name,
            random.Random(1),
        ).execute(
            definition,
            TriggerEvent("manual", "test", "manual", {}),
        )

        self.assertTrue(result.succeeded)
        self.assertIn('selected "Rare"', result.detail)

        run_result = self.service.run_routine(parent.routine_id)
        self.assertTrue(run_result.succeeded)
        self.assertEqual(self.capture.contexts[-1]["automation.selected_choice"], "rare")

    def test_while_repeats_routine_until_condition_is_false(self) -> None:
        body = self.routines.add("Increment")
        self.add_task(
            body.routine_id,
            "core.adjust_variable",
            {"name": "automation.counter", "amount": 1},
        )
        parent = self.routines.add("Loop")
        self.add_task(
            parent.routine_id,
            "core.create_routine_variable",
            {"name": "counter", "value": "0"},
        )
        self.add_task(
            parent.routine_id,
            "core.logic_while",
            {
                "left": "{automation.counter}",
                "operator": "less_than",
                "right": "3",
                "routine_id": body.routine_id,
                "max_iterations": 10,
                "timeout_seconds": 2,
            },
        )
        self.add_task(parent.routine_id, self.capture.task_type)

        result = self.service.run_routine(parent.routine_id)

        self.assertTrue(result.succeeded)
        self.assertEqual(self.capture.contexts[-1]["automation.counter"], "3")

    def test_get_input_and_random_number_create_routine_variables(self) -> None:
        trigger = TriggerEvent("manual", "test", "manual", {})
        input_task = self.routines.add("Input")
        definition = self.add_task(
            input_task.routine_id,
            "core.logic_get_input",
            {"name": "viewer_choice", "title": "Test", "prompt": "Value"},
        )
        handler = GetInputTask(lambda *_args, **_kwargs: ("coffee", True))
        result = handler.execute(definition, trigger)
        self.assertTrue(result.succeeded)
        self.assertEqual(trigger.context["automation.viewer_choice"], "coffee")

        random_definition = self.add_task(
            input_task.routine_id,
            "core.logic_random_number",
            {"name": "roll", "mode": "integer", "minimum": 5, "maximum": 5},
        )
        random_result = GetRandomNumberTask(random.Random(1)).execute(
            random_definition,
            trigger,
        )
        self.assertTrue(random_result.succeeded)
        self.assertEqual(trigger.context["automation.roll"], "5")


if __name__ == "__main__":
    unittest.main()
