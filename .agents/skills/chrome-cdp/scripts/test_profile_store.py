from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from profile_store import (
    active_profile,
    copied_profiles,
    load_manifest,
    profile_display_name,
    select_profile,
)


class ProfileStoreTest(unittest.TestCase):
    def test_reads_legacy_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Profile 2").mkdir()
            (root / ".chrome-cdp.json").write_text(
                json.dumps(
                    {
                        "created_at": "now",
                        "source_user_data": "/source",
                        "profile_directory": "Profile 2",
                    }
                ),
                encoding="utf-8",
            )

            manifest = load_manifest(root)

        self.assertEqual(manifest["active_profile"], "Profile 2")
        self.assertIn("Profile 2", manifest["profiles"])

    def test_selects_an_existing_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Default").mkdir()
            (root / "Profile 2").mkdir()
            (root / ".chrome-cdp.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "profiles": {"Default": {}, "Profile 2": {}},
                        "active_profile": "Profile 2",
                    }
                ),
                encoding="utf-8",
            )

            select_profile(root, "Default")

            self.assertEqual(active_profile(root), "Default")

    def test_sorts_numbered_profiles_numerically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for profile in ("Profile 10", "Default", "Profile 2"):
                (root / profile).mkdir()
            (root / ".chrome-cdp.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "profiles": {
                            "Profile 10": {},
                            "Default": {},
                            "Profile 2": {},
                        },
                        "active_profile": "Default",
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                copied_profiles(root),
                ["Default", "Profile 2", "Profile 10"],
            )

    def test_reads_display_name_from_local_state_for_existing_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Default").mkdir()
            (root / ".chrome-cdp.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "profiles": {"Default": {"copied_at": "now"}},
                        "active_profile": "Default",
                    }
                ),
                encoding="utf-8",
            )
            (root / "Local State").write_text(
                json.dumps(
                    {
                        "profile": {
                            "info_cache": {"Default": {"name": "Personal"}}
                        }
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(profile_display_name(root, "Default"), "Personal")


if __name__ == "__main__":
    unittest.main()
