from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import chrome_process


def result(returncode: int, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class ChromeProcessTest(unittest.TestCase):
    @patch("chrome_process.run_process_command", return_value=result(0))
    def test_detects_running_chrome_on_macos(self, _check: object) -> None:
        self.assertTrue(chrome_process.is_chrome_running_macos())

    @patch("chrome_process.run_process_command", return_value=result(1))
    def test_detects_stopped_chrome_on_macos(self, _check: object) -> None:
        self.assertFalse(chrome_process.is_chrome_running_macos())

    def test_recognizes_official_macos_main_and_helper_executables(self) -> None:
        main = Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        )
        helper = Path(
            "/Applications/Google Chrome.app/Contents/Frameworks/"
            "Google Chrome Framework.framework/Versions/150.0/Helpers/"
            "Google Chrome Helper (Renderer).app/Contents/MacOS/"
            "Google Chrome Helper (Renderer)"
        )
        impostor = Path(
            "/tmp/Google Chrome Helper (Renderer).app/Contents/MacOS/"
            "Google Chrome Helper (Renderer)"
        )

        self.assertTrue(
            chrome_process.is_stable_chrome_executable(main, platform="darwin")
        )
        self.assertTrue(
            chrome_process.is_stable_chrome_executable(helper, platform="darwin")
        )
        self.assertFalse(
            chrome_process.is_stable_chrome_executable(impostor, platform="darwin")
        )

    def test_reads_google_chrome_processes_on_ubuntu(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            proc_root = Path(temporary_directory)
            main = proc_root / "100"
            helper = proc_root / "101"
            other = proc_root / "102"
            main.mkdir()
            helper.mkdir()
            other.mkdir()
            (main / "exe").symlink_to("/opt/google/chrome/chrome")
            (helper / "exe").symlink_to("/opt/google/chrome/chrome")
            (other / "exe").symlink_to("/usr/bin/firefox")
            (main / "cmdline").write_bytes(b"/opt/google/chrome/chrome\0")
            (helper / "cmdline").write_bytes(
                b"/opt/google/chrome/chrome\0--type=renderer\0"
            )
            (other / "cmdline").write_bytes(b"/usr/bin/firefox\0")

            processes = chrome_process.linux_chrome_processes(proc_root)

        self.assertEqual([process.pid for process in processes], [100, 101])

    def test_ubuntu_requires_real_google_chrome_executable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            proc_root = Path(temporary_directory)
            impostor = proc_root / "200"
            impostor.mkdir()
            (impostor / "exe").symlink_to("/usr/bin/python3")
            (impostor / "cmdline").write_bytes(
                b"/opt/google/chrome/chrome\0--user-data-dir=/tmp/debug\0"
            )

            self.assertEqual(chrome_process.linux_chrome_processes(proc_root), [])

    def test_ubuntu_expands_chrome_single_field_process_title(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            proc_root = Path(temporary_directory)
            main = proc_root / "150"
            main.mkdir()
            executable = Path("/opt/google/chrome/chrome")
            (main / "exe").symlink_to(executable)
            (main / "cmdline").write_bytes(
                (
                    f"{executable} --remote-debugging-port=9222 "
                    "--user-data-dir='/tmp/chrome debug'\0"
                ).encode()
            )

            processes = chrome_process.linux_chrome_processes(proc_root)

        self.assertEqual(len(processes), 1)
        self.assertEqual(
            processes[0].arguments,
            [
                str(executable),
                "--remote-debugging-port=9222",
                "--user-data-dir=/tmp/chrome debug",
            ],
        )

    @patch(
        "chrome_process.run_process_command",
        return_value=result(0, '"chrome.exe","123","Console","1","100,000 K"'),
    )
    def test_detects_running_chrome_on_windows(self, _check: object) -> None:
        self.assertTrue(chrome_process.is_chrome_running_windows())

    @patch(
        "chrome_process.run_process_command",
        return_value=result(0, "INFO: No tasks are running"),
    )
    def test_detects_stopped_chrome_on_windows(self, _check: object) -> None:
        self.assertFalse(chrome_process.is_chrome_running_windows())

    @patch("chrome_process.wait_for_chrome_exit", return_value=True)
    @patch("chrome_process.request_chrome_close")
    @patch("chrome_process.is_chrome_running", return_value=True)
    def test_closes_running_chrome_normally(
        self, _running: object, request_close: object, _wait: object
    ) -> None:
        chrome_process.close_running_chrome()

        request_close.assert_called_once()

    @patch("chrome_process.os.kill")
    @patch(
        "chrome_process.linux_chrome_processes",
        return_value=[
            chrome_process.ChromeProcess(
                100, Path("/opt/google/chrome/chrome"), ["/opt/google/chrome/chrome"]
            ),
            chrome_process.ChromeProcess(
                101,
                Path("/opt/google/chrome/chrome"),
                ["/opt/google/chrome/chrome", "--type=renderer"],
            ),
        ],
    )
    def test_ubuntu_closes_only_main_process(
        self, _processes: object, kill: object
    ) -> None:
        chrome_process.request_chrome_close_ubuntu()

        kill.assert_called_once_with(100, chrome_process.signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
