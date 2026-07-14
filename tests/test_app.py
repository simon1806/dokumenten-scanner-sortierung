from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scanner_sorter.app import SettingsWindow, acquire_single_instance, release_single_instance


class AppTests(unittest.TestCase):
    def test_tray_image_has_windows_notification_size(self) -> None:
        image = SettingsWindow._tray_image()

        self.assertEqual((64, 64), image.size)
        self.assertEqual("RGBA", image.mode)

    @unittest.skipUnless(os.name == "nt", "Windows-Mutex wird nur unter Windows verwendet")
    def test_only_one_instance_per_settings_file_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"
            first_acquired, first_handle = acquire_single_instance(settings_path)
            try:
                second_acquired, second_handle = acquire_single_instance(settings_path)
                self.assertTrue(first_acquired)
                self.assertFalse(second_acquired)
                self.assertIsNone(second_handle)
            finally:
                release_single_instance(first_handle)

            third_acquired, third_handle = acquire_single_instance(settings_path)
            try:
                self.assertTrue(third_acquired)
            finally:
                release_single_instance(third_handle)


if __name__ == "__main__":
    unittest.main()
