from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from installer import installer


class InstallerTests(unittest.TestCase):
    def test_first_install_prompt_uses_installation_action(self) -> None:
        title, instruction, _content, action = installer.prompt_text(False, "0.1.16")

        self.assertEqual("Installation bestätigen", title)
        self.assertIn("0.1.16", instruction)
        self.assertEqual("Installation ausführen", action)

    def test_update_prompt_uses_update_action(self) -> None:
        title, instruction, content, action = installer.prompt_text(True, "0.1.16")

        self.assertEqual("Update bestätigen", title)
        self.assertIn("0.1.16", instruction)
        self.assertIn("Einstellungen", content)
        self.assertEqual("Update ausführen", action)

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
