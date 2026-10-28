"""Core-owned contracts for trusted, bundled Streamhouse Hub modules."""

from products.hub.modules.actions import (
    ActionDescriptor,
    ActionExecutionContext,
    ActionParameter,
    ActionRegistry,
    ActionResult,
)
from products.hub.modules.contracts import (
    Availability,
    HubModule,
    ModuleContext,
    ModuleDescriptor,
    ModuleLifecycle,
    ModuleStatus,
    RestartRequired,
)
from products.hub.modules.contributions import (
    AutomationSubpageContribution,
    AutomationSubpageRegistry,
    BackupContributor,
    BackupContributorRegistry,
    ModuleTabContribution,
    ModuleTabRegistry,
    TriggerProviderContribution,
    TriggerProviderRegistry,
)
from products.hub.modules.discovery import (
    BUNDLED_MODULES_ROOT,
    BundledModuleDiscovery,
    BundledModuleSpec,
    DiscoveryResult,
)
from products.hub.modules.registry import ModuleRegistry
from products.hub.modules.settings import ModuleEnablementStore

__all__ = [
    "ActionDescriptor",
    "ActionExecutionContext",
    "ActionParameter",
    "ActionRegistry",
    "ActionResult",
    "Availability",
    "AutomationSubpageContribution",
    "AutomationSubpageRegistry",
    "BackupContributor",
    "BackupContributorRegistry",
    "BUNDLED_MODULES_ROOT",
    "BundledModuleDiscovery",
    "BundledModuleSpec",
    "DiscoveryResult",
    "HubModule",
    "ModuleContext",
    "ModuleDescriptor",
    "ModuleEnablementStore",
    "ModuleLifecycle",
    "ModuleRegistry",
    "ModuleStatus",
    "ModuleTabContribution",
    "ModuleTabRegistry",
    "RestartRequired",
    "TriggerProviderContribution",
    "TriggerProviderRegistry",
]
