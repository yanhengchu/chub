from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import chrome_debug
from chrome_process import ChromeProcess


class ChromeDebugTest(unittest.TestCase):
    def create_profile(self, root: Path) -> None:
        (root / "Profile 2").mkdir(parents=True)
        (root / chrome_debug.MANIFEST_FILE).write_text(
            json.dumps(
                {
                    "version": 2,
                    "source_user_data": "/source",
                    "profiles": {"Profile 2": {"copied_at": "now"}},
                    "active_profile": "Profile 2",
                }
            ),
            encoding="utf-8",
        )

    def test_loads_profile_from_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_profile(root)

            self.assertEqual(chrome_debug.load_profile_directory(root), "Profile 2")

    def test_launch_command_uses_isolated_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            user_data_dir = Path(temporary_directory) / "chrome-debug-data"
            command = chrome_debug.build_launch_command(
                Path(temporary_directory) / "chrome",
                user_data_dir,
                "Profile 2",
            )

            self.assertIn("--remote-debugging-address=127.0.0.1", command)
            self.assertIn("--remote-debugging-port=9222", command)
            self.assertIn(f"--user-data-dir={user_data_dir}", command)
            self.assertIn("--profile-directory=Profile 2", command)
            self.assertNotIn("--headless", command)

            headless_command = chrome_debug.build_launch_command(
                Path(temporary_directory) / "chrome",
                user_data_dir,
                "Profile 2",
                headless=True,
            )
            self.assertIn("--headless", headless_command)

    @patch("chrome_debug.listener_process_ids", return_value=[123])
    @patch("chrome_debug.probe_cdp", return_value="ws://127.0.0.1/devtools/browser/id")
    @patch(
        "chrome_debug.debug_processes",
        return_value=[
            ChromeProcess(
                123,
                Path("/opt/google/chrome/chrome"),
                [
                    "/opt/google/chrome/chrome",
                    "--remote-debugging-port=9222",
                    "--user-data-dir=/tmp/debug",
                ],
            )
        ],
    )
    def test_status_reports_running(
        self,
        _processes: object,
        _probe: object,
        _listeners: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_profile(root)

            result = chrome_debug.status(root)

        self.assertEqual(result.state, "running")
        self.assertEqual(result.mode, "headed")
        self.assertEqual(result.process_ids, [123])
        _processes.assert_called_once_with(root)

    @patch("chrome_debug.request_debug_close")
    @patch("chrome_debug.status")
    @patch("chrome_debug.wait_for_debug_exit", return_value=True)
    @patch("chrome_debug.debug_main_process_ids", return_value=[123])
    @patch("chrome_debug.debug_process_ids", return_value=[123, 124])
    def test_stop_targets_only_debug_process(
        self,
        _processes: object,
        _main_processes: object,
        _wait: object,
        status: object,
        request_close: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory).absolute()
            expected = chrome_debug.DebugStatus(
                state="stopped",
                mode=None,
                endpoint=chrome_debug.CDP_ENDPOINT,
                user_data_dir=str(root),
                profile_directory="Profile 2",
                process_ids=[],
            )
            status.return_value = expected

            result = chrome_debug.stop(root)

            request_close.assert_called_once_with(root, [123])
            self.assertEqual(result, expected)

    @patch("chrome_debug.linux_chrome_processes")
    def test_process_listing_includes_helpers_but_main_listing_does_not(
        self, linux_processes: object
    ) -> None:
        linux_processes.return_value = [
            ChromeProcess(
                101,
                Path("/opt/google/chrome/chrome"),
                [
                    "/opt/google/chrome/chrome",
                    "--user-data-dir=/tmp/debug",
                    "--remote-debugging-port=9222",
                ],
            ),
            ChromeProcess(
                102,
                Path("/opt/google/chrome/chrome"),
                [
                    "/opt/google/chrome/chrome",
                    "--type=renderer",
                    "--user-data-dir=/tmp/debug",
                ],
            ),
        ]

        self.assertEqual(
            [process.pid for process in chrome_debug.debug_processes_linux(Path("/tmp/debug"))],
            [101, 102],
        )
        with patch(
            "chrome_debug.debug_processes",
            return_value=linux_processes.return_value,
        ):
            self.assertEqual(
                chrome_debug.debug_main_process_ids(Path("/tmp/debug")),
                [101],
            )
            self.assertEqual(
                chrome_debug.debug_cdp_main_process_ids(Path("/tmp/debug")),
                [101],
            )

    @patch("chrome_debug.linux_chrome_processes")
    def test_linux_debug_listing_excludes_non_chrome_process(
        self, linux_processes: object
    ) -> None:
        linux_processes.return_value = []
        self.assertEqual(chrome_debug.debug_processes_linux(Path("/tmp/debug")), [])

    def test_process_matching_requires_exact_user_data_path(self) -> None:
        self.assertTrue(
            chrome_debug.process_uses_user_data(
                "/chrome --user-data-dir='/tmp/chrome debug'",
                Path("/tmp/chrome debug"),
                windows=False,
            )
        )
        self.assertFalse(
            chrome_debug.process_uses_user_data(
                "/chrome --user-data-dir=/tmp/chrome-debug-data-backup",
                Path("/tmp/chrome-debug-data"),
                windows=False,
            )
        )

    def test_macos_process_parser_preserves_executable_path_spaces(self) -> None:
        self.assertEqual(
            chrome_debug.macos_process_arguments(
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--user-data-dir='/tmp/chrome debug'"
            ),
            [
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                "--user-data-dir=/tmp/chrome debug",
            ],
        )

    def test_macos_process_parser_accepts_official_helper(self) -> None:
        executable = (
            "/Applications/Google Chrome.app/Contents/Frameworks/"
            "Google Chrome Framework.framework/Versions/150.0/Helpers/"
            "Google Chrome Helper (Renderer).app/Contents/MacOS/"
            "Google Chrome Helper (Renderer)"
        )
        self.assertEqual(
            chrome_debug.macos_process_arguments(
                f"{executable} --type=renderer --user-data-dir=/tmp/debug"
            ),
            [
                executable,
                "--type=renderer",
                "--user-data-dir=/tmp/debug",
            ],
        )

    @patch("chrome_debug.run_process_command")
    def test_macos_debug_listing_includes_official_helper_only(
        self, run_command: object
    ) -> None:
        main = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        helper = (
            "/Applications/Google Chrome.app/Contents/Frameworks/"
            "Google Chrome Framework.framework/Versions/150.0/Helpers/"
            "Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper"
        )
        impostor = "/tmp/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper"
        run_command.return_value = Mock(
            returncode=0,
            stdout=(
                f" 101 {main} --user-data-dir=/tmp/debug\n"
                f" 102 {helper} --type=utility --user-data-dir=/tmp/debug\n"
                f" 103 {impostor} --type=renderer --user-data-dir=/tmp/debug\n"
            ),
        )

        processes = chrome_debug.debug_processes_macos(Path("/tmp/debug"))

        self.assertEqual([process.pid for process in processes], [101, 102])

    @patch("chrome_debug.os.kill")
    @patch("chrome_debug.status")
    @patch("chrome_debug.debug_process_ids", return_value=[])
    def test_stop_does_not_signal_unrecognized_process(
        self, _processes: object, status: object, kill: object
    ) -> None:
        root = Path("/tmp/debug").absolute()
        expected = chrome_debug.DebugStatus(
            state="stopped",
            mode=None,
            endpoint=chrome_debug.CDP_ENDPOINT,
            user_data_dir=str(root),
            profile_directory=None,
            process_ids=[],
        )
        status.return_value = expected

        self.assertEqual(chrome_debug.stop(root), expected)
        kill.assert_not_called()

    @patch("chrome_debug.listener_process_ids", return_value=[999])
    @patch("chrome_debug.probe_cdp", return_value="ws://127.0.0.1/devtools/browser/id")
    @patch(
        "chrome_debug.debug_processes",
        return_value=[
            ChromeProcess(
                123,
                Path("/opt/google/chrome/chrome"),
                [
                    "/opt/google/chrome/chrome",
                    "--remote-debugging-port=9222",
                    "--user-data-dir=/tmp/debug",
                ],
            )
        ],
    )
    def test_status_rejects_cdp_owned_by_another_process(
        self,
        _processes: object,
        _probe: object,
        _listeners: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = chrome_debug.status(Path(temporary_directory))
        self.assertEqual(result.state, "broken")
        self.assertIsNone(result.mode)

    @patch("chrome_debug.status")
    def test_start_rejects_switching_a_running_instance_mode(
        self, status: object
    ) -> None:
        status.return_value = chrome_debug.DebugStatus(
            state="running",
            mode="headed",
            endpoint=chrome_debug.CDP_ENDPOINT,
            user_data_dir="/tmp/debug",
            profile_directory="Default",
            process_ids=[123],
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_profile(root)
            with self.assertRaisesRegex(RuntimeError, "stop it before starting"):
                chrome_debug.start(root, headless=True)

    @patch("chrome_debug.status")
    def test_start_is_idempotent_for_the_same_mode(self, status: object) -> None:
        expected = chrome_debug.DebugStatus(
            state="running",
            mode="headless",
            endpoint=chrome_debug.CDP_ENDPOINT,
            user_data_dir="/tmp/debug",
            profile_directory="Default",
            process_ids=[123],
        )
        status.return_value = expected

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_profile(root)
            self.assertEqual(
                chrome_debug.start(root, headless=True),
                expected,
            )

    @patch("chrome_debug.listener_process_ids", return_value=[101])
    @patch("chrome_debug.probe_cdp", return_value="ws://127.0.0.1/devtools/browser/id")
    @patch(
        "chrome_debug.debug_processes",
        return_value=[
            ChromeProcess(
                101,
                Path("/opt/google/chrome/chrome"),
                [
                    "/opt/google/chrome/chrome",
                    "--remote-debugging-port=9222",
                    "--headless",
                ],
            )
        ],
    )
    def test_status_reports_headless_mode(
        self,
        _processes: object,
        _probe: object,
        _listeners: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = chrome_debug.status(Path(temporary_directory))

        self.assertEqual(result.state, "running")
        self.assertEqual(result.mode, "headless")

    @patch("chrome_debug.listener_process_ids", return_value=[123])
    @patch("chrome_debug.debug_cdp_main_process_ids", return_value=[123])
    @patch("chrome_debug.probe_cdp", return_value="ws://127.0.0.1:9222/devtools/browser/id")
    @patch("chrome_debug.send_browser_close")
    def test_cdp_close_requires_and_uses_owned_endpoint(
        self,
        send_close: object,
        _probe: object,
        _processes: object,
        _listeners: object,
    ) -> None:
        self.assertTrue(chrome_debug.request_cdp_close(Path("/tmp/debug")))
        send_close.assert_called_once_with(
            "ws://127.0.0.1:9222/devtools/browser/id"
        )

    @patch("chrome_debug.force_debug_close_windows")
    @patch("chrome_debug.request_debug_close")
    @patch("chrome_debug.debug_main_process_ids", return_value=[123])
    @patch("chrome_debug.wait_for_debug_exit", side_effect=[False, True])
    @patch("chrome_debug.request_cdp_close", return_value=False)
    @patch("chrome_debug.debug_process_ids", return_value=[123, 124])
    def test_windows_headless_cleanup_uses_validated_force_stop_as_last_resort(
        self,
        _processes: object,
        _cdp_close: object,
        _wait: object,
        _main_processes: object,
        request_close: object,
        force_close: object,
    ) -> None:
        root = Path("/tmp/debug")
        with patch("chrome_debug.sys.platform", "win32"):
            self.assertTrue(chrome_debug.close_owned_debug_processes(root))

        request_close.assert_called_once_with(root, [123])
        force_close.assert_called_once_with(root)

    @patch("chrome_debug.run_process_command")
    @patch("chrome_debug.debug_process_ids", return_value=[123, 124])
    def test_windows_force_stop_uses_only_validated_debug_processes(
        self, _processes: object, run_command: object
    ) -> None:
        run_command.return_value = Mock(returncode=0)

        chrome_debug.force_debug_close_windows(Path("/tmp/debug"))

        command = run_command.call_args.args[0]
        self.assertIn("Get-Process -Id $ids", command[-1])
        self.assertIn("Stop-Process -Force", command[-1])
        self.assertIn("$ids = @(123,124)", command[-1])

    @patch("chrome_debug.wait_for_debug_exit", return_value=True)
    @patch("chrome_debug.request_debug_close")
    @patch("chrome_debug.debug_main_process_ids", return_value=[456])
    @patch("chrome_debug.debug_process_ids", return_value=[456, 457])
    @patch("chrome_debug.status")
    @patch("chrome_debug.time.sleep")
    @patch("chrome_debug.time.monotonic", side_effect=[0, 16])
    @patch("chrome_debug.subprocess.Popen")
    @patch("chrome_debug.chrome_executable", return_value=Path("/chrome"))
    def test_failed_start_closes_launched_debug_chrome(
        self,
        _executable: object,
        popen: object,
        _monotonic: object,
        _sleep: object,
        status: object,
        _processes: object,
        _main_processes: object,
        request_close: object,
        _wait: object,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.create_profile(root)
            stopped = chrome_debug.DebugStatus(
                state="stopped",
                mode=None,
                endpoint=chrome_debug.CDP_ENDPOINT,
                user_data_dir=str(root),
                profile_directory="Profile 2",
                process_ids=[],
            )
            broken = chrome_debug.DebugStatus(
                state="broken",
                mode=None,
                endpoint=chrome_debug.CDP_ENDPOINT,
                user_data_dir=str(root),
                profile_directory="Profile 2",
                process_ids=[456, 457],
            )
            status.side_effect = [stopped, broken]
            popen.return_value.poll.return_value = None

            with self.assertRaisesRegex(RuntimeError, "did not expose CDP"):
                chrome_debug.start(root)

        request_close.assert_called_once_with(root, [456])


if __name__ == "__main__":
    unittest.main()
