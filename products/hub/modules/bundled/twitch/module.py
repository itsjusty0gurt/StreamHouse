from __future__ import annotations

from products.hub.automation.task_catalog import TWITCH_TASK_METADATA
from products.hub.automation.variable_providers import twitch_context_provider
from products.hub.modules.contracts import Availability, ModuleContext, ModuleDescriptor
from products.hub.modules.contributions import TriggerProviderContribution
from products.hub.twitch.automation_triggers import TWITCH_TRIGGER_DISPLAY_NAMES
from products.hub.twitch.tasks import TWITCH_TASK_LABELS


TWITCH_MODULE_ID = "streamhouse.twitch"
TWITCH_TRIGGER_PROVIDER_ID = "twitch.automation"


class TwitchModule:
    """Phase-one Twitch module: static catalogs, without runtime ownership."""

    descriptor = ModuleDescriptor(
        module_id=TWITCH_MODULE_ID,
        display_name="Twitch",
        version="0.3.0-alpha",
        category="Streaming Platform",
        enabled_by_default=True,
        required_core_capabilities=frozenset(
            {"automation", "tasks", "triggers", "variables"}
        ),
        capabilities=frozenset(
            {
                "twitch.automation",
                "twitch.commands",
                "twitch.variables",
            }
        ),
        ui_label="Twitch",
    )

    def __init__(self) -> None:
        self._enabled = False
        self._catalog_registered = False

    def availability(self) -> Availability:
        return Availability(
            self._enabled,
            "The Twitch module is disabled or unavailable." if not self._enabled else "",
        )

    def task_availability(self) -> tuple[bool, str]:
        availability = self.availability()
        return availability.available, availability.detail

    def register_catalog(self, context: ModuleContext) -> None:
        if self._catalog_registered:
            raise ValueError("The Twitch static catalog is already registered.")
        for metadata in TWITCH_TASK_METADATA:
            context.tasks.register_metadata(metadata)
        context.tasks.register_provider_availability(
            TWITCH_TASK_LABELS,
            self.task_availability,
        )
        context.variables.register(
            twitch_context_provider(available=lambda: self._enabled)
        )

        trigger_store = context.trigger_stores.get(TWITCH_TRIGGER_PROVIDER_ID)
        if trigger_store is None:
            raise ValueError(
                "Twitch static catalog requires the current Twitch trigger store."
            )
        if context.trigger_providers is None:
            raise ValueError("Twitch static catalog requires trigger-provider support.")
        context.trigger_providers.register(
            TriggerProviderContribution(
                provider_id=TWITCH_TRIGGER_PROVIDER_ID,
                owner_module_id=TWITCH_MODULE_ID,
                display_name="Twitch",
                store=trigger_store,
                event_types=TWITCH_TRIGGER_DISPLAY_NAMES,
                availability=self.availability,
            )
        )
        self._catalog_registered = True

    def register(self, _context: ModuleContext) -> None:
        # Runtime construction and event dispatch deliberately remain on the
        # existing application path until the next conversion phase.
        self._enabled = True

    def configure(self, _context: ModuleContext) -> None:
        return None

    def start(self) -> None:
        return None

    def stop(self) -> None:
        # Phase 1 does not own the live Twitch runtime. MainWindow still runs
        # application.closing routines before disconnecting that runtime, so
        # catalog availability must not be withdrawn during module shutdown.
        return None
