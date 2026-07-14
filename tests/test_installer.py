from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from installer import installer


class InstallerTests(unittest.TestCase):
    @patch("installer.installer.subprocess.run")
    def test_desktop_shortcut_points_to_installed_application(self, run: object) -> None:
        target = Path(r"C:\Users\Test\AppData\Local\Programs\DokumentenScannerSortierung\app.exe")
        icon = target.parent / installer.ICON_FILENAME

        installer.create_desktop_shortcut(target, icon)

        arguments = run.call_args.args[0]
        powershell_script = arguments[-1]
        self.assertIn(installer.SHORTCUT_FILENAME, powershell_script)
        self.assertIn(str(target), powershell_script)
        self.assertIn(str(icon), powershell_script)
        self.assertIn("$shortcut.IconLocation", powershell_script)
        self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
