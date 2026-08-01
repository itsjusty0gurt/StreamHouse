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

    def test_shared_does_not_import_either_product(self) -> None:
        self._assert_no_prefix(self.root / "shared", "products")

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


if __name__ == "__main__":
    unittest.main()
