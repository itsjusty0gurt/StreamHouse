from __future__ import annotations

import ast
import unittest
from pathlib import Path


class PackageBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]

    @staticmethod
    def _imports_under(root: Path) -> list[tuple[Path, str]]:
        imports: list[tuple[Path, str]] = []
        for path in root.rglob("*.py"):
            if "tests" in path.parts or "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.extend((path, alias.name) for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append((path, node.module))
        return imports

    def _assert_no_prefix(self, root: Path, forbidden: str) -> None:
        violations = [
            f"{path.relative_to(self.root)} imports {module}"
            for path, module in self._imports_under(root)
            if module == forbidden or module.startswith(forbidden + ".")
        ]
        self.assertEqual(violations, [])

    def test_hub_does_not_import_ai_product(self) -> None:
        self._assert_no_prefix(self.root / "products" / "hub", "products.ai")

    def test_ai_does_not_import_hub_product(self) -> None:
        self._assert_no_prefix(self.root / "products" / "ai", "products.hub")

    def test_hub_and_shared_do_not_import_model_providers(self) -> None:
        for root in (self.root / "products" / "hub", self.root / "shared"):
            for provider in ("openai", "ollama", "transformers", "llama_cpp"):
                self._assert_no_prefix(root, provider)

    def test_hub_settings_do_not_own_model_provider_credentials(self) -> None:
        from dataclasses import fields

        from products.hub.core.settings import AppSettings

        names = {field.name.casefold() for field in fields(AppSettings)}
        for forbidden in (
            "openai_api_key",
            "anthropic_api_key",
            "ollama_api_key",
            "provider_api_key",
            "provider_access_token",
        ):
            self.assertNotIn(forbidden, names)

    def test_shared_reply_policy_cannot_generate_fallback_text(self) -> None:
        from shared.streamhouse_shared.response_policy import ResponsePolicy

        self.assertFalse(hasattr(ResponsePolicy, "fallback_reply"))

    def test_shared_does_not_import_either_product(self) -> None:
        self._assert_no_prefix(self.root / "shared", "products")

    def test_core_bootstrap_does_not_import_obs_implementation(self) -> None:
        for relative in (
            "products/hub/streamhouse_hub/app.py",
            "products/hub/ui/main_window.py",
        ):
            root = self.root / relative
            imports = (
                self._imports_under(root)
                if root.is_dir()
                else self._imports_under(root.parent)
            )
            violations = [
                f"{path.relative_to(self.root)} imports {module}"
                for path, module in imports
                if path == root or root.is_dir()
                if module == "products.hub.obs_service"
                or module.startswith("products.hub.obs_service.")
            ]
            self.assertEqual(violations, [])

    def test_bundled_modules_do_not_import_other_bundled_modules(self) -> None:
        root = self.root / "products" / "hub" / "modules" / "bundled"
        violations: list[str] = []
        for path, module in self._imports_under(root):
            relative = path.relative_to(root)
            if len(relative.parts) < 2:
                continue
            owner = relative.parts[0]
            prefix = "products.hub.modules.bundled."
            if module.startswith(prefix):
                target = module.removeprefix(prefix).split(".", 1)[0]
                if target != owner:
                    violations.append(
                        f"{path.relative_to(self.root)} imports bundled module {target}"
                    )
        self.assertEqual(violations, [])

    def test_product_specific_tools_and_extension_are_separated(self) -> None:
        for relative in (
            "tools/build/build_hub.ps1",
            "tools/build/build_ai.ps1",
            "tools/release/package_hub.ps1",
            "tools/release/package_ai.ps1",
            "extensions/twitch/app/relay_server.py",
            "extensions/twitch/tools/build_extension.ps1",
        ):
            self.assertTrue((self.root / relative).is_file(), relative)

    def test_hub_build_excludes_unused_qt_quick_qml_python_modules(self) -> None:
        self._assert_no_prefix(self.root / "products" / "hub", "PySide6.QtQuick")
        self._assert_no_prefix(self.root / "products" / "hub", "PySide6.QtQml")
        build = (self.root / "tools" / "build" / "build_hub.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('--exclude-module "PySide6.QtQuick"', build)
        self.assertIn('--exclude-module "PySide6.QtQml"', build)

    def test_hub_build_removes_extension_development_files_from_artifacts(self) -> None:
        build = (self.root / "tools" / "build" / "build_hub.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn('"extensions\\twitch\\app"', build)
        self.assertIn('-Filter "*.py"', build)
        for excluded in ("__pycache__", "listing", "README.md"):
            self.assertIn(f'"{excluded}"', build)


if __name__ == "__main__":
    unittest.main()
