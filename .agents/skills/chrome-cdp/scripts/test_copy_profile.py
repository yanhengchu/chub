from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from copy_profile import copy_profile
from profile_store import load_manifest


class CopyProfileTest(unittest.TestCase):
    def test_copies_profile_and_excludes_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            profile = source / "Profile 2"
            (profile / "Network").mkdir(parents=True)
            (profile / "Network" / "Cookies").write_text("fixture", encoding="utf-8")
            (profile / "Cache").mkdir()
            (profile / "Cache" / "large.bin").write_text("cache", encoding="utf-8")
            (source / "Local State").write_text("{}", encoding="utf-8")

            target = root / "chrome-debug-data"
            with patch("copy_profile.is_chrome_running", return_value=False):
                result = copy_profile(
                    "Profile 2",
                    source_user_data=source,
                    target=target,
                )

            self.assertEqual(result, target)
            self.assertTrue((target / "Profile 2" / "Network" / "Cookies").is_file())
            self.assertFalse((target / "Profile 2" / "Cache").exists())
            manifest = load_manifest(target)
            self.assertEqual(manifest["active_profile"], "Profile 2")
            self.assertIn("Profile 2", manifest["profiles"])

    def test_refuses_unmanaged_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            target = root / "chrome-debug-data"
            target.mkdir()
            (target / "existing.txt").write_text("keep", encoding="utf-8")

            with patch("copy_profile.is_chrome_running", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "not managed"):
                    copy_profile(
                        "Default",
                        source_user_data=source,
                        target=target,
                    )

            self.assertEqual(
                (target / "existing.txt").read_text(encoding="utf-8"),
                "keep",
            )

    def test_adds_second_profile_and_makes_it_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            (source / "Profile 2").mkdir()
            (source / "Local State").write_text("{}", encoding="utf-8")
            target = root / "chrome-debug-data"

            with patch("copy_profile.is_chrome_running", return_value=False):
                copy_profile(
                    "Profile 2",
                    source_user_data=source,
                    target=target,
                )
                copy_profile(
                    "Default",
                    source_user_data=source,
                    target=target,
                )

            manifest = load_manifest(target)
            self.assertTrue((target / "Profile 2").is_dir())
            self.assertTrue((target / "Default").is_dir())
            self.assertEqual(manifest["active_profile"], "Default")
            self.assertEqual(
                set(manifest["profiles"]),
                {"Default", "Profile 2"},
            )

    def test_adds_profile_name_and_merges_only_its_local_state_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            (source / "Profile 2").mkdir()
            source_state = {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "Personal"},
                        "Profile 2": {"name": "Work", "avatar_icon": "avatar"},
                    }
                }
            }
            (source / "Local State").write_text(
                json.dumps(source_state), encoding="utf-8"
            )
            target = root / "chrome-debug-data"

            with patch("copy_profile.is_chrome_running", return_value=False):
                copy_profile("Default", source_user_data=source, target=target)
                target_state = json.loads(
                    (target / "Local State").read_text(encoding="utf-8")
                )
                target_state["debug_only"] = {"keep": True}
                (target / "Local State").write_text(
                    json.dumps(target_state), encoding="utf-8"
                )
                copy_profile("Profile 2", source_user_data=source, target=target)

            merged_state = json.loads(
                (target / "Local State").read_text(encoding="utf-8")
            )
            manifest = load_manifest(target)
            self.assertEqual(merged_state["debug_only"], {"keep": True})
            self.assertEqual(
                merged_state["profile"]["info_cache"]["Profile 2"]["name"],
                "Work",
            )
            self.assertEqual(manifest["profiles"]["Profile 2"]["name"], "Work")

    def test_manifest_failure_rolls_back_added_profile_and_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            (source / "Profile 2").mkdir()
            (source / "Local State").write_text(
                json.dumps(
                    {
                        "profile": {
                            "info_cache": {
                                "Default": {"name": "Personal"},
                                "Profile 2": {"name": "Work"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            target = root / "chrome-debug-data"
            with patch("copy_profile.is_chrome_running", return_value=False):
                copy_profile("Default", source_user_data=source, target=target)
            original_local_state = (target / "Local State").read_bytes()

            with (
                patch("copy_profile.is_chrome_running", return_value=False),
                patch("copy_profile.save_manifest", side_effect=OSError("disk full")),
            ):
                with self.assertRaisesRegex(OSError, "disk full"):
                    copy_profile(
                        "Profile 2",
                        source_user_data=source,
                        target=target,
                    )

            self.assertFalse((target / "Profile 2").exists())
            self.assertEqual(
                (target / "Local State").read_bytes(),
                original_local_state,
            )
            self.assertNotIn("Profile 2", load_manifest(target)["profiles"])

    @patch("copy_profile.close_running_chrome")
    @patch("copy_profile.is_chrome_running", return_value=True)
    def test_running_chrome_is_closed_before_copy(
        self, _running: object, close_chrome: object
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)

            copy_profile(
                "Default",
                source_user_data=source,
                target=root / "target",
            )

        close_chrome.assert_called_once_with()

    @patch("copy_profile.close_running_chrome")
    @patch("copy_profile.is_chrome_running", return_value=True)
    def test_require_stopped_refuses_without_closing_chrome(
        self, _running: object, close_chrome: object
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)

            with self.assertRaisesRegex(RuntimeError, "Close regular Chrome"):
                copy_profile(
                    "Default",
                    source_user_data=source,
                    target=root / "target",
                    close_running=False,
                )

        close_chrome.assert_not_called()

    @patch("copy_profile.close_running_chrome")
    @patch("copy_profile.is_chrome_running", side_effect=[False, True])
    def test_require_stopped_checks_running_chrome_only_once(
        self, running: object, close_chrome: object
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)

            copy_profile(
                "Default",
                source_user_data=source,
                target=root / "target",
                close_running=False,
            )

        self.assertEqual(running.call_count, 1)
        close_chrome.assert_not_called()

    def test_next_copy_cleans_interrupted_staging_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            target = root / "chrome-debug-data"
            stale = root / ".chrome-debug-data.tmp-interrupted"
            stale.mkdir()
            (stale / "partial").write_text("partial", encoding="utf-8")

            with patch("copy_profile.is_chrome_running", return_value=False):
                copy_profile(
                    "Default",
                    source_user_data=source,
                    target=target,
                    close_running=False,
                )

        self.assertFalse(stale.exists())

    @patch("copy_profile.close_running_chrome")
    @patch("copy_profile.is_chrome_running", return_value=True)
    def test_invalid_existing_target_does_not_close_chrome(
        self, _running: object, close_chrome: object
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            target = root / "target"
            target.mkdir()
            (target / "unmanaged").write_text("keep", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "not managed"):
                copy_profile("Default", source_user_data=source, target=target)

        close_chrome.assert_not_called()

    def test_rejects_non_object_source_local_state_when_merging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            (source / "Default").mkdir(parents=True)
            (source / "Profile 2").mkdir()
            (source / "Local State").write_text("{}", encoding="utf-8")
            target = root / "target"
            with patch("copy_profile.is_chrome_running", return_value=False):
                copy_profile("Default", source_user_data=source, target=target)
            (source / "Local State").write_text("[]", encoding="utf-8")

            with patch("copy_profile.is_chrome_running", return_value=False):
                with self.assertRaisesRegex(RuntimeError, "Invalid source"):
                    copy_profile("Profile 2", source_user_data=source, target=target)


if __name__ == "__main__":
    unittest.main()
