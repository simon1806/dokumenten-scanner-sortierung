from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scanner_sorter.app import (
    SettingsWindow,
    acquire_single_instance,
    app_asset_path,
    initial_window_geometry,
    release_single_instance,
    ui_icon_path,
)


class AppTests(unittest.TestCase):
    def test_default_window_is_large_and_centered_on_full_hd_screen(self) -> None:
        width, height, x, y = initial_window_geometry(1920, 1080)

        self.assertEqual((1460, 1000, 230, 40), (width, height, x, y))

    def test_default_window_stays_inside_smaller_screen(self) -> None:
        width, height, x, y = initial_window_geometry(1366, 768)

        self.assertEqual((1286, 688, 40, 40), (width, height, x, y))

    def test_required_button_icons_are_available(self) -> None:
        for name in ("folder-open", "device-floppy", "player-play", "player-stop", "window-minimize", "power"):
            with self.subTest(name=name):
                self.assertTrue(ui_icon_path(name).is_file())

    def test_program_icons_are_available(self) -> None:
        self.assertTrue(app_asset_path("dokumenten-scanner-sortierung.ico").is_file())
        self.assertTrue(app_asset_path("dokumenten-scanner-sortierung.png").is_file())

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
