from __future__ import annotations

import unittest

from scanner_sorter.app import SettingsWindow


class AppTests(unittest.TestCase):
    def test_tray_image_has_windows_notification_size(self) -> None:
        image = SettingsWindow._tray_image()

        self.assertEqual((64, 64), image.size)
        self.assertEqual("RGBA", image.mode)


if __name__ == "__main__":
    unittest.main()
