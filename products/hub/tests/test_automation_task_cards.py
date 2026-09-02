import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.task_catalog import BUILTIN_TASK_METADATA
from products.hub.automation.tasks import TaskMetadata, TaskRegistry
from products.hub.ui.automation_task_cards import (
    ElidingLabel,
    TASK_CATEGORY_ACCENTS,
    TaskCardContent,
    TaskCardWidget,
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
