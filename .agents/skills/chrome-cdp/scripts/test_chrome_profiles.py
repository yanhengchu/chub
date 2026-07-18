from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chrome_profiles import list_profiles, read_profile_names


class ChromeProfilesTest(unittest.TestCase):
    def test_lists_user_profiles_and_excludes_internal_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for directory in ("Default", "Profile 2", "Guest Profile", "System Profile"):
                (root / directory).mkdir()
            (root / "Local State").write_text(
                json.dumps(
                    {
                        "profile": {
                            "info_cache": {
                                "Default": {"name": "Primary"},
                                "Profile 2": {"name": "Work"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            profiles = list_profiles(root)

        self.assertEqual(
            [(profile.directory, profile.name) for profile in profiles],
            [("Default", "Primary"), ("Profile 2", "Work")],
        )

    def test_invalid_profile_metadata_falls_back_safely(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "Local State").write_text(
                '{"profile": []}',
                encoding="utf-8",
            )

            self.assertEqual(read_profile_names(root), {})


if __name__ == "__main__":
    unittest.main()
