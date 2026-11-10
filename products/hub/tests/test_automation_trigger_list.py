import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.automation.tasks import TaskRegistry
from products.hub.integrations.music_triggers import MusicTriggerStore
from products.hub.modules import (
    TriggerProviderContribution,
    TriggerProviderRegistry,
)
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES, ObsTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.ui.automation_page import AutomationPage


class AutomationTriggerListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.routine_store = RoutineStore(root / "routines.json")
        self.command_store = TwitchCommandTriggerStore(
            root / "commands.json",
            self.routine_store,
        )
        self.event_store = TwitchEventTriggerStore(
            root / "event-triggers.json",
            self.routine_store,
        )
        self.core_store = CoreTriggerStore(
            root / "core-triggers.json",
            self.routine_store,
        )
        self.music_store = MusicTriggerStore(
            root / "music-triggers.json",
            self.routine_store,
        )
        self.obs_store = ObsTriggerStore(
            root / "obs-triggers.json",
            self.routine_store,
        )
        self.task_registry = TaskRegistry()
        self.automation_service = AutomationService(
            self.routine_store,
            self.task_registry,
        )
        providers = TriggerProviderRegistry()
        providers.register(
            TriggerProviderContribution(
                provider_id="obs.automation",
                owner_module_id="streamhouse.obs",
                display_name="OBS",
                store=self.obs_store,
                event_types=OBS_TRIGGER_TYPES,
            )
        )
        self.page = AutomationPage(
            self.routine_store,
            self.command_store,
            self.event_store,
            self.core_store,
            self.music_store,
            self.task_registry,
            self.automation_service,
            trigger_provider_registry=providers,
        )

    def tearDown(self) -> None:
        self.page.queue_timer.stop()
        self.page.close()
        self.page.deleteLater()
        self.application.processEvents()
        self.directory.cleanup()

    def test_double_click_uses_shared_editor_for_core_twitch_and_obs(self) -> None:
        routine = self.routine_store.add("Double-click trigger editors")
        twitch_trigger = self.event_store.add(routine.routine_id, "channel.raid")
        core_trigger = self.core_store.add(
            routine.routine_id,
            "application.started",
            enabled=False,
        )
        obs_trigger = self.obs_store.add(
            routine.routine_id,
            "CurrentProgramSceneChanged",
        )
        self.page.select_routine(routine.routine_id)
        self.page._edit_event_trigger = Mock()
        self.page._edit_core_trigger = Mock()
        self.page._edit_obs_trigger = Mock()
        self.page.automation_service.run_routine = Mock()

        for kind, trigger_id, editor in (
            ("event", twitch_trigger.trigger_id, self.page._edit_event_trigger),
            ("core", core_trigger.trigger_id, self.page._edit_core_trigger),
            ("obs", obs_trigger.trigger_id, self.page._edit_obs_trigger),
        ):
            self.page._select_trigger(kind, trigger_id)
            item = self.page.trigger_list.currentItem()
            self.assertIsNotNone(item)
            self.page.trigger_list.itemDoubleClicked.emit(item)
            self.assertEqual(editor.call_count, 1)

        self.assertFalse(core_trigger.enabled)
        self.page.automation_service.run_routine.assert_not_called()

    def test_empty_space_is_inert_and_edit_button_still_uses_same_path(self) -> None:
        routine = self.routine_store.add("Trigger edit controls")
        trigger = self.core_store.add(
            routine.routine_id,
            "application.started",
        )
        self.page.select_routine(routine.routine_id)
        self.page._edit_core_trigger = Mock()

        self.page._edit_trigger_item(None)
        self.page._edit_core_trigger.assert_not_called()

        self.page._select_trigger("core", trigger.trigger_id)
        self.page._edit_core_trigger.assert_not_called()
        self.page.edit_trigger_button.click()
        self.page._edit_core_trigger.assert_called_once()


if __name__ == "__main__":
    unittest.main()
