from __future__ import annotations

import random
import tempfile
import unittest
from pathlib import Path

from automation.file_tasks import ReadRandomLineTask, register_file_tasks
from automation.models import TaskDefinition, TriggerEvent
from automation.tasks import TaskRegistry


class FileTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.registry = TaskRegistry()
        register_file_tasks(self.registry)
        self.context: dict[str, str] = {}
        self.trigger = TriggerEvent("manual", "test", "manual", self.context)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def execute(self, task_type: str, config: dict):
        return self.registry.execute(
            TaskDefinition("task", task_type, task_type, config),
            self.trigger,
        )

    def test_read_text_handles_utf8_bom_and_sets_routine_variable(self) -> None:
        path = self.root / "lines.txt"
        path.write_text("Hello Sally!", encoding="utf-8-sig")

        result = self.execute(
            "core.file_read",
            {"path": str(path), "variable": "file_text"},
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(self.context["file_text"], "Hello Sally!")

    def test_random_line_ignores_blanks(self) -> None:
        path = self.root / "responses.txt"
        path.write_text("\nOnly line\n\n", encoding="utf-8")
        task = TaskDefinition(
            "random",
            "core.file_random_line",
            "Random",
            {
                "path": str(path),
                "variable": "response",
                "ignore_blank_lines": True,
            },
        )

        result = ReadRandomLineTask(random.Random(1)).execute(task, self.trigger)

        self.assertTrue(result.succeeded)
        self.assertEqual(self.context["response"], "Only line")

    def test_specific_line_renders_line_number_variable(self) -> None:
        path = self.root / "lines.txt"
        path.write_text("first\nsecond\nthird", encoding="utf-8")
        self.context["wanted_line"] = "2"

        result = self.execute(
            "core.file_specific_line",
            {
                "path": str(path),
                "line_number": "{wanted_line}",
                "variable": "selected",
            },
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(self.context["selected"], "second")

    def test_write_can_overwrite_and_append_rendered_text(self) -> None:
        path = self.root / "activity.txt"
        self.context["user"] = "Viewer"

        first = self.execute(
            "core.file_write",
            {
                "path": str(path),
                "mode": "overwrite",
                "text": "Hello {user}",
                "add_newline": True,
            },
        )
        second = self.execute(
            "core.file_write",
            {
                "path": str(path),
                "mode": "append",
                "text": "Again",
                "add_newline": False,
            },
        )

        self.assertTrue(first.succeeded)
        self.assertTrue(second.succeeded)
        self.assertEqual(path.read_text(encoding="utf-8"), "Hello Viewer\nAgain")

    def test_path_exists_distinguishes_files_and_folders(self) -> None:
        path = self.root / "exists.txt"
        path.write_text("yes", encoding="utf-8")

        result = self.execute(
            "core.path_exists",
            {
                "path": str(path),
                "path_type": "folder",
                "variable": "exists",
            },
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(self.context["exists"], "false")

    def test_count_lines_can_ignore_blank_lines(self) -> None:
        path = self.root / "lines.txt"
        path.write_text("one\n\ntwo\n", encoding="utf-8")

        result = self.execute(
            "core.file_count_lines",
            {
                "path": str(path),
                "variable": "line_count",
                "ignore_blank_lines": True,
            },
        )

        self.assertTrue(result.succeeded)
        self.assertEqual(self.context["line_count"], "2")

    def test_read_failure_can_continue_the_routine(self) -> None:
        result = self.execute(
            "core.file_read",
            {
                "path": str(self.root / "missing.txt"),
                "variable": "text",
                "stop_on_failure": False,
            },
        )

        self.assertTrue(result.succeeded)
        self.assertIn("routine will continue", result.detail)


if __name__ == "__main__":
    unittest.main()
