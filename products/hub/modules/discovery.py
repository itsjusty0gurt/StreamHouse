from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from products.hub.modules.contracts import HubModule, validate_stable_id


BUNDLED_MODULES_ROOT = Path(__file__).resolve().parent / "bundled"


@dataclass(frozen=True, slots=True)
class BundledModuleSpec:
    module_id: str
    directory_name: str
    factory: Callable[[], HubModule]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "module_id",
            validate_stable_id(self.module_id, label="Module ID"),
        )
        if (
            not self.directory_name
            or Path(self.directory_name).name != self.directory_name
            or self.directory_name.startswith(".")
        ):
            raise ValueError("Bundled module directory names must be one visible path segment.")


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    modules: tuple[HubModule, ...]
    missing_trusted: tuple[str, ...]
    untrusted_entries: tuple[str, ...]


class BundledModuleDiscovery:
    """Discovers only Core-allowlisted factories in Hub's bundled module root.

    Directory presence is discovery input; directory contents are never imported
    dynamically. This deliberately does not form a third-party plugin loader.
    """

    def __init__(
        self,
        root: Path,
        specs: Iterable[BundledModuleSpec],
        *,
        embedded: bool = False,
    ) -> None:
        self.root = Path(root)
        self._specs = tuple(sorted(specs, key=lambda item: item.module_id))
        self._embedded = embedded
        module_ids = [item.module_id for item in self._specs]
        directories = [item.directory_name for item in self._specs]
        if len(module_ids) != len(set(module_ids)) or len(directories) != len(set(directories)):
            raise ValueError("Bundled module specifications must have unique IDs and directories.")

    def discover(self) -> DiscoveryResult:
        trusted_directories = {item.directory_name for item in self._specs}
        present = {
            item.name
            for item in self.root.iterdir()
            if item.is_dir() and not item.name.startswith("_") and not item.name.startswith(".")
        } if self.root.is_dir() else set()
        # PyInstaller embeds Python packages in its archive rather than
        # materializing their source directories. The explicit first-party
        # allowlist remains authoritative in that environment; arbitrary
        # filesystem entries are still never imported.
        if self._embedded:
            present.update(trusted_directories)

        modules: list[HubModule] = []
        missing: list[str] = []
        for spec in self._specs:
            if spec.directory_name not in present:
                missing.append(spec.module_id)
                continue
            module = spec.factory()
            if module.descriptor.module_id != spec.module_id:
                raise ValueError(
                    f'Bundled module factory for "{spec.module_id}" returned '
                    f'"{module.descriptor.module_id}".'
                )
            modules.append(module)
        return DiscoveryResult(
            modules=tuple(modules),
            missing_trusted=tuple(missing),
            untrusted_entries=tuple(sorted(present - trusted_directories)),
        )
