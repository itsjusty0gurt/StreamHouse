from __future__ import annotations

import tempfile
from copy import deepcopy
from pathlib import Path

from products.hub.automation.core_tasks import CommentTask
from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.task_catalog import CORE_TASK_METADATA
from products.hub.automation.tasks import TaskRegistry


def test_comment_task_is_registered_and_succeeds_without_mutating_inputs() -> None:
    registry = TaskRegistry(CORE_TASK_METADATA)
    registry.register(CommentTask())
    task = TaskDefinition(
        "comment-task",
        "core.comment",
        "Comment",
        {"comment": "Stretch break starts here"},
    )
    trigger = TriggerEvent(
        "manual",
        "streamhouse",
        "manual",
        {"custom.mode": "BRB"},
    )
    original_config = deepcopy(task.config)
    original_context = deepcopy(trigger.context)

    result = registry.execute(task, trigger)

    assert result.succeeded
    assert result.detail == "Stretch break starts here"
    assert task.config == original_config
    assert trigger.context == original_context
    metadata = registry.metadata("core.comment")
    assert metadata is not None
    assert metadata.label == "Comment"
    assert metadata.category == "Core"
    assert metadata.format_card_summary(
        {"comment": "Only run this branch\nduring BRB"}
    ) == "Only run this branch during BRB"


def test_comment_task_allows_empty_text() -> None:
    result = CommentTask().execute(
        TaskDefinition("comment-task", "core.comment", "Comment", {"comment": ""}),
        TriggerEvent("manual", "streamhouse", "manual", {}),
    )

    assert result.succeeded
    assert result.detail == "Comment."


def test_comment_text_and_ids_round_trip_at_root_and_inside_if_branch() -> None:
    with tempfile.TemporaryDirectory() as directory:
        store = RoutineStore(Path(directory) / "routines.json")
        routine = store.add("Documented routine")
        root_comment = store.add_task(
            routine.routine_id,
            task_type="core.comment",
            name="Comment",
            config={"comment": "Routine starts here"},
        )
        condition = store.add_task(
            routine.routine_id,
            task_type="core.if",
            name="If BRB",
            config={"left": "{custom.mode}", "operator": "equals", "right": "BRB"},
            then_tasks=[
                TaskDefinition(
                    "branch-comment",
                    "core.comment",
                    "Comment",
                    {"comment": "Only run this branch during BRB"},
                )
            ],
        )

        reloaded = RoutineStore(store.path)
        reloaded.load()
        saved = reloaded.get(routine.routine_id)

        assert saved is not None
        assert saved.tasks[0].task_id == root_comment.task_id
        assert saved.tasks[0].config["comment"] == "Routine starts here"
        assert saved.tasks[1].task_id == condition.task_id
        assert saved.tasks[1].then_tasks[0].task_id == "branch-comment"
        assert (
            saved.tasks[1].then_tasks[0].config["comment"]
            == "Only run this branch during BRB"
        )
