import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.task_catalog import BUILTIN_TASK_METADATA
from products.hub.automation.tasks import TaskMetadata, TaskRegistry
from products.hub.ui.automation_task_cards import (
    ElidingLabel,
    QueueCardContent,
    QueueCardWidget,
    RoutineCardContent,
    RoutineCardWidget,
    TASK_CATEGORY_ACCENTS,
    TaskCardContent,
    TaskCardWidget,
    TriggerCardContent,
    TriggerCardWidget,
    task_category_accent,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _metadata(task_type: str) -> TaskMetadata:
    return next(item for item in BUILTIN_TASK_METADATA if item.task_type == task_type)


def test_common_task_summaries_are_metadata_driven() -> None:
    resolver = lambda kind, value: {  # noqa: E731 - compact test resolver
        ("counter", "deaths"): "Deaths",
        ("routine", "raid"): "Incoming Raid",
    }.get((kind, value), value)
    cases = (
        ("core.wait", {"duration": "1.5", "unit": "seconds"}, "1.5 sec"),
        (
            "obs.set_scene_item_enabled",
            {"scene": "BRB", "source": "Overlay", "action": "show"},
            "BRB / Overlay → Show",
        ),
        (
            "twitch.send_chat_message",
            {"message": "Welcome {user.display_name}!"},
            "“Welcome {user.display_name}!”",
        ),
        ("counter.increase", {"counter_id": "deaths", "amount": "1"}, "Deaths +1"),
        ("core.run_routine", {"routine_id": "raid"}, "Incoming Raid"),
        ("core.end_routine", {}, "End this routine here"),
    )

    for task_type, config, expected in cases:
        assert _metadata(task_type).format_card_summary(config, resolver) == expected


def test_registry_requires_card_summary_for_visible_tasks() -> None:
    registry = TaskRegistry()
    metadata = TaskMetadata(
        task_type="test.visible",
        label="Visible",
        short_description="Visible test task.",
        help_text="Detailed help for the visible test task.",
        category="Tests",
    )

    try:
        registry.register_metadata(metadata)
    except ValueError as error:
        assert "card summary" in str(error)
    else:
        raise AssertionError("Visible task metadata without a card summary was accepted.")


def test_category_accents_are_central_and_category_text_remains_visible() -> None:
    app = _app()
    assert task_category_accent("OBS") == TASK_CATEGORY_ACCENTS["OBS"]
    assert task_category_accent("Unknown")
    card = TaskCardWidget(TaskCardContent("OBS", "Change scene", "BRB"))

    assert card.name_label.text() == "OBS — Change scene"
    assert TASK_CATEGORY_ACCENTS["OBS"] in card.accent_bar.styleSheet()
    assert "OBS" in card.accessibleName()
    card.close()
    app.processEvents()


def test_long_summary_elides_without_changing_font_or_full_text() -> None:
    app = _app()
    label = ElidingLabel("A very long chat message that cannot fit in a narrow card")
    original_size = label.font().pointSizeF()
    label.resize(70, 24)
    label.show()
    app.processEvents()

    assert label.full_text.startswith("A very long chat")
    assert label.text().endswith("…")
    assert label.toolTip() == label.full_text
    assert label.font().pointSizeF() == original_size
    label.close()
    app.processEvents()


def test_routine_card_is_one_row_with_only_name_and_queue_content() -> None:
    app = _app()
    card = RoutineCardWidget(
        RoutineCardContent(
            routine_name="Incoming Raid",
            trigger_family="Twitch",
            queue_name="Default Queue",
        )
    )

    assert card.name_label.full_text == "Incoming Raid"
    assert card.queue_label.full_text == "Default Queue"
    assert card.findChild(ElidingLabel, "automationRoutineTrigger") is None
    assert card.findChild(ElidingLabel, "automationRoutineSummary") is None
    assert card.minimumHeight() == 32
    assert card.layout().count() == 2
    card.close()
    app.processEvents()


def test_routine_card_elides_long_names_and_preserves_visual_states() -> None:
    app = _app()
    card = RoutineCardWidget(
        RoutineCardContent(
            routine_name="A routine name that is much too long for a narrow row",
            trigger_family="Manual",
            queue_name="A queue name that is also much too long for a narrow row",
            enabled=False,
            issues=("Routine has no tasks.",),
        )
    )
    card.resize(220, 32)
    card.show()
    app.processEvents()

    assert card.name_label.full_text.startswith("A routine name")
    assert card.queue_label.full_text.startswith("A queue name")
    assert card.queue_label.maximumWidth() == 130
    assert card.queue_label.width() > 0
    assert card.queue_label.text().endswith("…")
    assert "Disabled" in card.toolTip()
    assert "no tasks" in card.toolTip().lower()
    assert not card.warning_label.isHidden()
    assert "disabled" in card.accessibleName()
    assert "needs attention" in card.accessibleName()

    card.set_selected(True)
    assert card.property("selected") is True
    card.close()
    app.processEvents()


def test_trigger_card_is_compact_elides_and_preserves_attention_states() -> None:
    app = _app()
    card = TriggerCardWidget(
        TriggerCardContent(
            title="Twitch — Channel Point Redemption",
            summary="A reward name that is too long for this narrow trigger row",
            family="Twitch",
            enabled=False,
            issues=("The saved reward name is unavailable.",),
        )
    )
    card.resize(280, 38)
    card.show()
    app.processEvents()

    assert card.minimumHeight() == 38
    assert card.title_label.full_text == "Twitch — Channel Point Redemption"
    assert card.summary_label.full_text.startswith("A reward name")
    assert card.summary_label.text().endswith("…")
    assert card.state_label.text() == "Disabled"
    assert not card.warning_label.isHidden()
    assert "reward name" in card.toolTip()
    card.set_selected(True)
    assert card.property("selected") is True
    card.close()
    app.processEvents()


def test_queue_card_keeps_name_primary_and_default_state_compact() -> None:
    app = _app()
    card = QueueCardWidget(
        QueueCardContent(
            name="Default Queue",
            is_default=True,
            active=True,
            pending=2,
        )
    )

    assert card.minimumHeight() == 32
    assert card.name_label.full_text == "Default Queue"
    assert card.state_label.full_text == "Default · Active · 2 pending"
    assert "streamhouse.default.queue" not in card.accessibleName()
    card.set_selected(True)
    assert card.property("selected") is True
    card.close()
    app.processEvents()

    custom = QueueCardWidget(QueueCardContent(name="Alerts"))
    assert custom.name_label.full_text == "Alerts"
    assert custom.state_label.isHidden()
    custom.close()
    app.processEvents()

    narrow = QueueCardWidget(
        QueueCardContent(
            name="A queue name that is far too long for a narrow Automation pane",
            is_default=True,
        )
    )
    narrow.resize(210, 32)
    narrow.show()
    app.processEvents()
    assert narrow.name_label.text().endswith("…")
    assert narrow.name_label.toolTip().startswith("A queue name")
    assert narrow.minimumHeight() == 32
    narrow.close()
    app.processEvents()
