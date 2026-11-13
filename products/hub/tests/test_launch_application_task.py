from __future__ import annotations

import os
from pathlib import Path
import unittest
from unittest.mock import call, patch

from products.hub.automation.core_tasks import LaunchApplicationTask
from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.core import external_process
from products.hub.core.external_process import (
    external_process_environment,
    start_external_process,
)


class ExternalProcessEnvironmentTests(unittest.TestCase):
    def test_source_execution_preserves_environment(self) -> None:
        environment = {
            "PATH": "C:\\Tools",
            "QT_PLUGIN_PATH": "C:\\UserQt\\plugins",
            "CUSTOM_VALUE": "preserved",
        }

        result = external_process_environment(environment, frozen=False)

        self.assertEqual(result, environment)
        self.assertIsNot(result, environment)

    def test_frozen_execution_removes_only_bundle_runtime_paths(self) -> None:
        bundle = Path("C:/Program Files/Streamhouse Hub/_internal")
        environment = {
            "PATH": os.pathsep.join((str(bundle), "C:\\Windows", "C:\\UserTools")),
            "QT_PLUGIN_PATH": str(bundle / "PySide6" / "plugins"),
            "QML2_IMPORT_PATH": str(bundle / "PySide6" / "qml"),
            "QT_QPA_PLATFORM_PLUGIN_PATH": "C:\\UserQt\\platforms",
            "CUSTOM_VALUE": "preserved",
            "_PYI_ARCHIVE_FILE": "StreamhouseHub.exe",
            "_MEIPASS2": str(bundle),
        }

        result = external_process_environment(
            environment,
            frozen=True,
            bundle_root=bundle,
        )

        self.assertEqual(result["PATH"], os.pathsep.join(("C:\\Windows", "C:\\UserTools")))
        self.assertNotIn("QT_PLUGIN_PATH", result)
        self.assertNotIn("QML2_IMPORT_PATH", result)
        self.assertEqual(result["QT_QPA_PLATFORM_PLUGIN_PATH"], "C:\\UserQt\\platforms")
        self.assertEqual(result["CUSTOM_VALUE"], "preserved")
        self.assertNotIn("_PYI_ARCHIVE_FILE", result)
        self.assertNotIn("_MEIPASS2", result)

    def test_sanitization_does_not_mutate_parent_environment(self) -> None:
        bundle = Path("C:/Hub/_internal")
        environment = {
            "PATH": os.pathsep.join((str(bundle), "C:\\Windows")),
            "QT_PLUGIN_PATH": str(bundle / "plugins"),
        }
        original = dict(environment)

        external_process_environment(environment, frozen=True, bundle_root=bundle)

        self.assertEqual(environment, original)

    @patch("products.hub.core.external_process.subprocess.Popen")
    @patch("products.hub.core.external_process.external_process_environment")
    def test_start_uses_sanitized_environment(self, environment, popen) -> None:
        environment.return_value = {"CUSTOM_VALUE": "preserved"}

        start_external_process(
            ["C:/Apps/example.exe", "--flag"],
            cwd="C:/Apps",
            startupinfo=None,
        )

        popen.assert_called_once_with(
            ["C:/Apps/example.exe", "--flag"],
            cwd="C:/Apps",
            startupinfo=None,
            env={"CUSTOM_VALUE": "preserved"},
        )

    @patch("ctypes.WinDLL")
    def test_frozen_windows_launch_restores_dll_directory(self, win_dll) -> None:
        kernel32 = win_dll.return_value

        def read_directory(_size, buffer) -> int:
            buffer.value = "C:\\Hub\\_internal"
            return len(buffer.value)

        kernel32.GetDllDirectoryW.side_effect = read_directory
        kernel32.SetDllDirectoryW.return_value = True

        with (
            patch.object(external_process.sys, "platform", "win32"),
            patch.object(external_process.sys, "frozen", True, create=True),
            external_process._standard_windows_dll_search_path(),
        ):
            kernel32.SetDllDirectoryW.assert_called_once_with(None)

        self.assertEqual(
            kernel32.SetDllDirectoryW.call_args_list,
            [call(None), call("C:\\Hub\\_internal")],
        )


class LaunchApplicationTaskTests(unittest.TestCase):
    @staticmethod
    def _trigger() -> TriggerEvent:
        return TriggerEvent("manual", "core", "manual", {})

    @patch("products.hub.automation.core_tasks.start_external_process")
    def test_launch_preserves_arguments_and_working_directory(self, start) -> None:
        task = TaskDefinition(
            "launch",
            "core.launch_application",
            "Launch OBS",
            {
                "executable": "C:/Program Files/obs-studio/bin/64bit/obs64.exe",
                "arguments": '--collection "Main Show" --minimize-to-tray',
                "working_directory": "C:/Program Files/obs-studio/bin/64bit",
            },
        )

        result = LaunchApplicationTask().execute(task, self._trigger())

        self.assertTrue(result.succeeded)
        start.assert_called_once_with(
            [
                "C:/Program Files/obs-studio/bin/64bit/obs64.exe",
                "--collection",
                '"Main Show"',
                "--minimize-to-tray",
            ],
            cwd="C:/Program Files/obs-studio/bin/64bit",
            startupinfo=None,
        )

    @patch("products.hub.automation.core_tasks.start_external_process")
    def test_blank_working_directory_uses_executable_folder(self, start) -> None:
        executable = "C:/Program Files/obs-studio/bin/64bit/obs64.exe"
        task = TaskDefinition(
            "launch",
            "core.launch_application",
            "Launch OBS",
            {
                "executable": executable,
                "arguments": "--minimize-to-tray",
                "working_directory": "",
            },
        )

        result = LaunchApplicationTask().execute(task, self._trigger())

        self.assertTrue(result.succeeded)
        start.assert_called_once_with(
            [executable, "--minimize-to-tray"],
            cwd=str(Path(executable).parent),
            startupinfo=None,
        )


if __name__ == "__main__":
    unittest.main()
