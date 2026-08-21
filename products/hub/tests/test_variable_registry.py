import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.models import TaskExecutionResult
from products.hub.automation.logic_tasks import comparison_choices_for_type
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.variable_providers import (
    CounterVariableProvider,
    CustomVariableProvider,
    context_provider,
)
from products.hub.automation.variable_outputs import generated_output_definitions
from products.hub.automation.variable_registry import (
    CallbackVariableProvider,
    VariableAvailability,
    VariableDataType,
    VariableDefinition,
    VariableRegistry,
)
from products.hub.counters.models import CounterDefinition
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.ui.variable_picker import VariablePickerDialog
from products.hub.ui.variables_page import VariablesPage
from products.hub.ui.automation_page import TaskEditorDialog


def definition(name: str = "Deaths") -> CounterDefinition:
    return CounterDefinition("deaths", name, "death", "deaths")


def test_registry_registration_resolution_collision_and_read_only() -> None:
    item = VariableDefinition(
        "hub.example",
        "Example",
        "Example value.",
        VariableDataType.INTEGER,
        "Hub",
        "Hub",
    )
    provider = CallbackVariableProvider(
        "Hub", (item,), lambda _name, _context: (True, 42, "")
    )
    registry = VariableRegistry()
    registry.register(provider)

    assert registry.resolve("hub.example").display_value == "42"
    with pytest.raises(ValueError, match="already registered"):
        registry.register(provider)
    with pytest.raises(PermissionError, match="read-only"):
        registry.set_value("hub.example", 7)


def test_contextual_resolution_and_safe_placeholder_rendering() -> None:
    registry = VariableRegistry()
    registry.register(context_provider())

    missing = registry.resolve("chat.message", {})
    assert missing is not None and not missing.available
    context = {"user": "Viewer", "message": "hello", "user_is_mod": "true"}
    assert registry.resolve("user.name", context).display_value == "Viewer"
    assert registry.resolve("user.is_mod", context).value is True
    assert registry.render(
        "{user.name}: {chat.message} / {chat.message_id}", context
    ) == "Viewer: hello / {chat.message_id}"
    assert registry.render("{chat.message_id}", context, fallback="--") == "--"


def test_custom_metadata_type_persistence_deletion_and_reserved_names() -> None:
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "variables.json"
        store = CustomVariableStore(path)
        store.load()
        store.set(
            "global",
            "custom.game_mode",
            "hardcore",
            data_type="text",
            description="Current game mode.",
        )
        store.set("global", "wins", "3.0", data_type="integer")

        loaded = CustomVariableStore(path)
        loaded.load()
        assert loaded.values() == {"game_mode": "hardcore", "wins": "3"}
        assert loaded.type_of("custom.game_mode") == "text"
        assert loaded.description_of("game_mode") == "Current game mode."
        assert CustomVariableProvider(loaded).resolve(
            "custom.game_mode", {}
        ).display_value == "hardcore"
        assert loaded.delete("custom.game_mode")
        assert "game_mode" not in loaded.values()
        deleted = CustomVariableStore(path)
        deleted.load()
        assert "game_mode" not in deleted.values()
        with pytest.raises(ValueError, match="custom namespace"):
            loaded.validate_custom_name("stream.title")
        with pytest.raises(ValueError):
            loaded.validate_custom_name("bad-name")


def test_counter_variable_uses_stable_id_and_domain_service_for_writes() -> None:
    with TemporaryDirectory() as temporary:
        service = CounterService(CounterStore(Path(temporary) / "counters"))
        service.create_counter(definition(), 5)
        provider = CounterVariableProvider(service)
        registry = VariableRegistry()
        registry.register(provider)

        assert registry.resolve("counter.deaths.stream").display_value == "5"
        assert registry.resolve("counter.deaths") is None
        registry.set_value("counter.deaths.stream", 9)
        assert service.get_values("deaths").channel_total == 9
        assert not registry.resolve("counter.deaths.viewer", {}).available
        service.set_value("deaths", "viewer_total", 3, user_id="111")
        viewer = registry.resolve("counter.deaths.viewer", {"user_id": "111"})
        assert viewer.available and viewer.value == 3
        with pytest.raises(PermissionError, match="read-only"):
            registry.set_value("counter.deaths.viewer", 4)
        service.update_counter("deaths", display_name="Boss Deaths")
        assert provider.definitions()[0].name == "counter.deaths.stream"
        assert registry.resolve("counter.deaths.stream").definition.display_name.startswith("Boss Deaths")


def test_alias_metadata_collisions_and_loop_prevention() -> None:
    canonical = VariableDefinition(
        "hub.value", "Value", "A value.", VariableDataType.INTEGER, "Hub", "Hub"
    )
    registry = VariableRegistry()
    registry.register(
        CallbackVariableProvider("Hub", (canonical,), lambda _name, _context: (True, 2, ""))
    )
    alias = registry.register_alias("hub.alternate_value", "hub.value")
    assert alias.is_alias and alias.data_type is VariableDataType.INTEGER
    assert registry.resolve("hub.alternate_value").value == 2
    assert registry.render("Alternate: {hub.alternate_value}") == "Alternate: 2"
    assert alias not in registry.definitions()
    assert alias in registry.definitions(include_aliases=True)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_alias("hub.value", "hub.value")

    loop = (
        VariableDefinition(
            "hub.first", "First", "", VariableDataType.TEXT, "Hub", "Hub",
            alias_of="hub.second",
        ),
        VariableDefinition(
            "hub.second", "Second", "", VariableDataType.TEXT, "Hub", "Hub",
            alias_of="hub.first",
        ),
    )
    with pytest.raises(ValueError, match="loop"):
        registry.register(
            CallbackVariableProvider("Hub", loop, lambda _name, _context: (True, "", ""))
        )


def test_temporary_definition_reports_lifetime_and_preview_separately() -> None:
    definition = VariableDefinition(
        "automation.task_output",
        "Task Output",
        "Only available during this routine.",
        VariableDataType.INTEGER,
        "Automation",
        "Task outputs",
        availability=VariableAvailability.TEMPORARY,
        preview_value=12,
    )
    assert definition.availability is VariableAvailability.TEMPORARY
    assert definition.preview_value == 12
    assert definition.default is None


def test_generated_task_outputs_have_typed_temporary_metadata() -> None:
    random_output = generated_output_definitions(
        "core.logic_random_number", {"name": "roll", "mode": "integer"}
    )[0]
    assert random_output.name == "automation.roll"
    assert random_output.data_type is VariableDataType.INTEGER
    assert random_output.availability is VariableAvailability.TEMPORARY
    assert random_output.preview_value == 1

    counter_outputs = generated_output_definitions(
        "counter.update", {"counter_id": "deaths"}, source="Counter — Update"
    )
    amount = next(item for item in counter_outputs if item.name == "automation.deaths_amount_changed")
    status = next(item for item in counter_outputs if item.name == "automation.deaths_status")
    assert amount.data_type is VariableDataType.INTEGER
    assert status.data_type is VariableDataType.TEXT
    assert amount.source == "Counter — Update"

    twitch_outputs = generated_output_definitions("twitch.get_stream_information", {})
    by_name = {item.name: item for item in twitch_outputs}
    assert by_name["automation.is_live"].data_type is VariableDataType.BOOLEAN
    assert by_name["automation.stream_viewers"].data_type is VariableDataType.INTEGER
    assert by_name["automation.stream_started_at"].data_type is VariableDataType.DATETIME


def test_pre_alpha_custom_variable_schema_is_rejected_for_reset() -> None:
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / "variables.json"
        path.write_text(
            json.dumps({"version": 1, "global": {"game_mode": "classic"}}),
            encoding="utf-8",
        )
        store = CustomVariableStore(path)
        with pytest.raises(ValueError, match="discarded pre-alpha schema"):
            store.load()


def test_condition_operator_choices_use_registry_types() -> None:
    numeric = {value for _label, value in comparison_choices_for_type(VariableDataType.INTEGER)}
    boolean = {value for _label, value in comparison_choices_for_type(VariableDataType.BOOLEAN)}
    text = {value for _label, value in comparison_choices_for_type(VariableDataType.TEXT)}
    assert {"greater_than", "less_or_equal"} <= numeric
    assert "contains" not in numeric
    assert {"is_true", "is_false"} <= boolean
    assert "greater_than" not in boolean
    assert {"contains", "starts_with", "ends_with"} <= text


def test_automation_context_receives_canonical_provider_values() -> None:
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = CustomVariableStore(root / "variables.json")
        store.load()
        store.set("global", "game_mode", "Hardcore")
        registry = VariableRegistry()
        registry.register(context_provider())
        registry.register(CustomVariableProvider(store))
        routines = RoutineStore(root / "routines.json")
        routine = routines.add("Capture")
        captured: list[dict[str, str]] = []

        class Capture:
            task_type = "test.capture_variables"

            def execute(self, task, trigger):
                captured.append(dict(trigger.context))
                return TaskExecutionResult(task.task_id, task.task_type, True, "ok")

        tasks = TaskRegistry()
        tasks.register(Capture())
        routines.add_task(routine.routine_id, task_type=Capture.task_type, name="Capture")
        service = AutomationService(routines, tasks, store, variable_registry=registry)

        assert service.run_routine(
            routine.routine_id, {"user": "Viewer", "message": "Hello"}
        ).succeeded
        assert captured[-1]["custom.game_mode"] == "Hardcore"
        assert captured[-1]["user.name"] == "Viewer"
        assert captured[-1]["chat.message"] == "Hello"


def test_variables_page_and_picker_search_canonical_names() -> None:
    application = QApplication.instance() or QApplication([])
    with TemporaryDirectory() as temporary:
        store = CustomVariableStore(Path(temporary) / "variables.json")
        store.load()
        store.set("global", "game_mode", "Hardcore", description="Mode")
        registry = VariableRegistry()
        registry.register(context_provider())
        registry.register(CustomVariableProvider(store))
        page = VariablesPage(registry, store)
        page.search_edit.setText("game_mode")
        assert page.table.rowCount() == 1
        assert page.table.item(0, 0).text() == "custom.game_mode"
        picker = VariablePickerDialog(registry)
        picker.search_edit.setText("game_mode")
        assert picker.selected_placeholder() == "{custom.game_mode}"
        picker.search_edit.setText("user.id")
        assert picker.selected_placeholder() == "{user.id}"
        assert picker.table.item(0, 4).text() == "Unavailable — Contextual"
        editor = TaskEditorDialog(
            "twitch.send_chat_message",
            variable_registry=registry,
        )
        assert editor.browse_variables_button.isEnabled()
        assert any(
            editor.variable_table.item(row, 0).text() == "{custom.game_mode}"
            for row in range(editor.variable_table.rowCount())
        )
        integer_definition = VariableDefinition(
            "hub.score", "Score", "Current score.", VariableDataType.INTEGER, "Hub", "Hub"
        )
        integer_registry = VariableRegistry()
        integer_registry.register(
            CallbackVariableProvider(
                "Hub", (integer_definition,), lambda _name, _context: (True, 4, "")
            )
        )
        condition_editor = TaskEditorDialog(
            "core.logic_if_else", variable_registry=integer_registry
        )
        condition_editor.field_widgets["core.logic_if_else"]["left"].setText("{hub.score}")
        application.processEvents()
        operator = condition_editor.field_widgets["core.logic_if_else"]["operator"]
        operators = {operator.itemData(index) for index in range(operator.count())}
        assert "greater_than" in operators
        assert "contains" not in operators
        condition_editor.close()
        editor.close()
        picker.close()
        page.close()
    application.processEvents()
