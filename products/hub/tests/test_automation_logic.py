from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.cancellation import current_cancellation
from products.hub.automation.logic_tasks import (
    GetInputTask,
    GetRandomNumberTask,
    RandomChoiceTask,
    evaluate_condition,
    evaluate_if_condition,
    register_logic_tasks,
)
from products.hub.automation.models import (
    END_ROUTINE_ACTION,
    TaskDefinition,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_tasks import RunRoutineTask, register_variable_tasks


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
        self.registry.register(
            RunRoutineTask(self.service.run_nested_routine, self.service.routine_name)
        )
        register_logic_tasks(self.registry, self.service)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_task(
        self,
        routine_id: str,
        task_type: str,
        config=None,
        name="Task",
        **task_values,
    ):
        return self.routines.add_task(
            routine_id,
            task_type=task_type,
            name=name,
            config=config or {},
            **task_values,
        )

    def test_condition_comparisons_render_variables(self) -> None:
        context = {"automation.score": "12", "automation.name": "Streamhouse"}
        self.assertTrue(evaluate_condition("{automation.score}", "greater_than", "10", context))
        self.assertTrue(evaluate_condition("{automation.name}", "equals_ignore_case", "streamhouse", context))
        self.assertTrue(evaluate_condition("automation.score", "exists", "", context))
        self.assertTrue(evaluate_condition("automation.missing", "not_exists", "", context))

    def test_end_routine_stops_remaining_tasks_successfully(self) -> None:
        routine = self.routines.add("End early")
        self.add_task(routine.routine_id, "core.end_routine")
        self.add_task(routine.routine_id, self.capture.task_type)

        result = self.service.run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        routine_result = result.routine_results[0]
        self.assertEqual(routine_result.flow_action, END_ROUTINE_ACTION)
        self.assertFalse(routine_result.cancelled)
        self.assertIn("completed early", routine_result.detail.casefold())
        self.assertEqual(self.capture.contexts, [])

    def test_if_comparisons_support_text_decimal_and_empty_values(self) -> None:
        context = {
            "command.data": "Coffee Time",
            "automation.amount": "0.3000000000000000001",
            "automation.threshold": "0.3",
        }
        comparisons = (
            ("same", "equals", "same", False),
            ("same", "not_equals", "different", False),
            ("Coffee Time", "contains", "fee T", False),
            ("Coffee Time", "not_contains", "tea", True),
            ("Coffee Time", "starts_with", "coffee", True),
            ("Coffee Time", "ends_with", "TIME", True),
            ("2", "greater_than", "1", False),
            ("2", "greater_or_equal", "2", False),
            ("1", "less_than", "2", False),
            ("2", "less_or_equal", "2", False),
            ("  ", "is_empty", "ignored", False),
            ("value", "is_not_empty", "ignored", False),
        )
        for left, operator, right, ignore_case in comparisons:
            with self.subTest(operator=operator):
                self.assertTrue(
                    evaluate_if_condition(
                        left,
                        operator,
                        right,
                        context,
                        ignore_case=ignore_case,
                    )
                )
        self.assertTrue(
            evaluate_if_condition(
                "{command.data}",
                "equals",
                "coffee time",
                context,
                ignore_case=True,
            )
        )
        self.assertTrue(
            evaluate_if_condition(
                "{automation.amount}",
                "greater_than",
                "{automation.threshold}",
                context,
            )
        )
        with self.assertRaisesRegex(ValueError, "requires numbers"):
            evaluate_if_condition("coffee", "greater_than", "2", context)
        with self.assertRaisesRegex(ValueError, "not available"):
            evaluate_if_condition("{command.missing}", "equals", "", context)

    def test_false_if_runs_empty_else_and_continues(self) -> None:
        routine = self.routines.add("Condition")
        self.add_task(
            routine.routine_id,
            "core.if",
            {"left": "1", "operator": "equals", "right": "2"},
        )
        self.add_task(routine.routine_id, self.capture.task_type)

        execution = self.service.run_routine(routine.routine_id)

        self.assertTrue(execution.succeeded)
        condition_result = execution.routine_results[0].task_results[0]
        self.assertEqual(condition_result.selected_branch, "else")
        self.assertEqual(condition_result.child_results, ())
        self.assertEqual(len(self.capture.contexts), 1)

    def test_if_runs_only_selected_nested_tasks_with_shared_context(self) -> None:
        parent = self.routines.add("Parent")
        self.add_task(
            parent.routine_id,
            "core.if",
            {
                "left": "{command.data}",
                "operator": "equals",
                "right": "coffee",
                "ignore_case": True,
            },
            then_tasks=[
                TaskDefinition(
                    "then-output",
                    "core.create_routine_variable",
                    "Then output",
                    {"name": "answer", "value": "yes"},
                )
            ],
            else_tasks=[
                TaskDefinition(
                    "else-output",
                    "core.create_routine_variable",
                    "Else output",
                    {"name": "wrong_branch", "value": "no"},
                )
            ],
        )
        self.add_task(parent.routine_id, self.capture.task_type)

        result = self.service.run_routine(parent.routine_id, {"command.data": "COFFEE"})

        self.assertTrue(result.succeeded)
        self.assertEqual(self.capture.contexts[-1]["automation.answer"], "yes")
        self.assertNotIn("automation.wrong_branch", self.capture.contexts[-1])
        condition_result = result.routine_results[0].task_results[0]
        self.assertEqual(condition_result.selected_branch, "then")
        self.assertEqual([child.task_id for child in condition_result.child_results], ["then-output"])

    def test_nested_if_failure_stops_parent_sequence(self) -> None:
        class FailTask:
            task_type = "test.fail"

            def execute(self, task, trigger):
                return TaskExecutionResult(task.task_id, task.task_type, False, "Branch failed.")

        self.registry.register(FailTask())
        routine = self.routines.add("Failing condition")
        self.add_task(
            routine.routine_id,
            "core.if",
            {"left": "1", "operator": "equals", "right": "1"},
            then_tasks=[TaskDefinition("failure", "test.fail", "Fail")],
        )
        self.add_task(routine.routine_id, self.capture.task_type)

        result = self.service.run_routine(routine.routine_id)

        self.assertFalse(result.succeeded)
        self.assertEqual(result.routine_results[0].task_results[0].detail, "Branch failed.")
        self.assertEqual(self.capture.contexts, [])

    def test_nested_if_cancellation_propagates_to_the_root_routine(self) -> None:
        class CancelTask:
            task_type = "test.cancel"

            def execute(self, task, trigger):
                current_cancellation().cancel("Stopped in branch.")
                return TaskExecutionResult(task.task_id, task.task_type, True, "Requested.")

        self.registry.register(CancelTask())
        routine = self.routines.add("Cancelled condition")
        self.add_task(
            routine.routine_id,
            "core.if",
            {"left": "1", "operator": "equals", "right": "1"},
            then_tasks=[TaskDefinition("cancel", "test.cancel", "Cancel")],
        )
        self.add_task(routine.routine_id, self.capture.task_type)

        result = self.service.run_routine(routine.routine_id)

        routine_result = result.routine_results[0]
        self.assertFalse(result.succeeded)
        self.assertTrue(routine_result.cancelled)
        self.assertTrue(routine_result.task_results[0].cancelled)
        self.assertTrue(routine_result.task_results[0].child_results[0].cancelled)
        self.assertEqual(self.capture.contexts, [])

    def test_if_can_nest_directly_inside_a_branch(self) -> None:
        inner = TaskDefinition(
            "inner-if",
            "core.if",
            "Inner If",
            {"left": "{command.data}", "operator": "contains", "right": "coffee"},
            then_tasks=[TaskDefinition("capture", self.capture.task_type, "Capture")],
        )
        routine = self.routines.add("Nested condition")
        self.add_task(
            routine.routine_id,
            "core.if",
            {"left": "yes", "operator": "equals", "right": "yes"},
            then_tasks=[inner],
        )

        result = self.service.run_routine(routine.routine_id, {"command.data": "more coffee"})

        self.assertTrue(result.succeeded)
        outer_result = result.routine_results[0].task_results[0]
        self.assertEqual(outer_result.child_results[0].selected_branch, "then")
        self.assertEqual(len(self.capture.contexts), 1)

    def test_if_can_nest_inside_else_with_unambiguous_ownership(self) -> None:
        inner = TaskDefinition(
            "inner-else-if",
            "core.if",
            "Inner Else If",
            {"left": "x", "operator": "equals", "right": "x"},
            then_tasks=[TaskDefinition("else-capture", self.capture.task_type, "Capture")],
        )
        routine = self.routines.add("Nested else")
        condition = self.add_task(
            routine.routine_id,
            "core.if",
            {"left": "no", "operator": "equals", "right": "yes"},
            else_tasks=[inner],
        )

        result = self.service.run_routine(routine.routine_id)

        self.assertTrue(result.succeeded)
        outer = result.routine_results[0].task_results[0]
        self.assertEqual(outer.selected_branch, "else")
        self.assertEqual(outer.child_results[0].task_id, "inner-else-if")
        self.assertEqual(condition.then_tasks, [])
        self.assertEqual(condition.else_tasks[0].then_tasks[0].task_id, "else-capture")

    def test_end_routine_escapes_then_and_else_branches(self) -> None:
        for selected_branch, left, then_tasks, else_tasks in (
            (
                "then",
                "yes",
                [
                    TaskDefinition("then-capture", self.capture.task_type, "Before end"),
                    TaskDefinition("then-end", "core.end_routine", "End Routine"),
                    TaskDefinition("then-skipped", self.capture.task_type, "Skipped child"),
                ],
                [],
            ),
            (
                "else",
                "no",
                [],
                [
                    TaskDefinition("else-capture", self.capture.task_type, "Before end"),
                    TaskDefinition("else-end", "core.end_routine", "End Routine"),
                    TaskDefinition("else-skipped", self.capture.task_type, "Skipped child"),
                ],
            ),
        ):
            with self.subTest(branch=selected_branch):
                routine = self.routines.add(f"End in {selected_branch}")
                self.add_task(
                    routine.routine_id,
                    "core.if",
                    {"left": left, "operator": "equals", "right": "yes"},
                    then_tasks=then_tasks,
                    else_tasks=else_tasks,
                )
                self.add_task(routine.routine_id, self.capture.task_type, name="Skipped parent")
                self.capture.contexts.clear()

                result = self.service.run_routine(routine.routine_id).routine_results[0]

                self.assertTrue(result.succeeded)
                self.assertEqual(result.flow_action, END_ROUTINE_ACTION)
                self.assertEqual(len(self.capture.contexts), 1)
                condition_result = result.task_results[0]
                self.assertEqual(condition_result.selected_branch, selected_branch)
                self.assertEqual(
                    condition_result.child_results[-1].task_type,
                    "core.end_routine",
                )

    def test_end_routine_propagates_through_nested_if(self) -> None:
        inner = TaskDefinition(
            "inner",
            "core.if",
            "Inner If",
            {"left": "1", "operator": "equals", "right": "1"},
            then_tasks=[
                TaskDefinition("inner-end", "core.end_routine", "End Routine"),
                TaskDefinition("inner-skipped", self.capture.task_type, "Skipped inner"),
            ],
        )
        routine = self.routines.add("Nested end")
        self.add_task(
            routine.routine_id,
            "core.if",
            {"left": "1", "operator": "equals", "right": "1"},
            then_tasks=[inner, TaskDefinition("outer-skipped", self.capture.task_type, "Skipped outer")],
        )
        self.add_task(routine.routine_id, self.capture.task_type, name="Skipped root")

        result = self.service.run_routine(routine.routine_id).routine_results[0]

        self.assertTrue(result.succeeded)
        self.assertEqual(result.flow_action, END_ROUTINE_ACTION)
        self.assertEqual(self.capture.contexts, [])

    def test_child_end_routine_is_consumed_and_parent_continues(self) -> None:
        child = self.routines.add("Child")
        self.add_task(child.routine_id, self.capture.task_type, name="Child before")
        self.add_task(child.routine_id, "core.end_routine", name="End child")
        self.add_task(child.routine_id, self.capture.task_type, name="Child skipped")
        parent = self.routines.add("Parent")
        self.add_task(
            parent.routine_id,
            "core.run_routine",
            {"routine_id": child.routine_id, "stop_on_failure": True},
            name="Run child",
        )
        self.add_task(parent.routine_id, self.capture.task_type, name="Parent continues")

        result = self.service.run_routine(parent.routine_id).routine_results[0]

        self.assertTrue(result.succeeded)
        self.assertEqual(result.flow_action, "")
        self.assertEqual(len(self.capture.contexts), 2)
        nested = result.task_results[0].nested_results[0]
        self.assertTrue(nested.succeeded)
        self.assertEqual(nested.flow_action, END_ROUTINE_ACTION)
        self.assertEqual([task.task_type for task in nested.task_results], [
            self.capture.task_type,
            "core.end_routine",
        ])

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
