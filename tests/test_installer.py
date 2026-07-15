from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from installer import installer


class InstallerTests(unittest.TestCase):
    def test_first_install_prompt_uses_installation_action(self) -> None:
        title, instruction, _content, action = installer.prompt_text(False, "0.1.19")

        self.assertEqual("Installation bestätigen", title)
        self.assertIn("0.1.19", instruction)
        self.assertEqual("Installation ausführen", action)

    def test_update_prompt_uses_update_action(self) -> None:
        title, instruction, content, action = installer.prompt_text(True, "0.1.19")

        self.assertEqual("Update bestätigen", title)
        self.assertIn("0.1.19", instruction)
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

    def test_installed_apps_entry_contains_publisher_support_and_uninstaller(self) -> None:
        target = Path(r"C:\Users\Test\AppData\Local\Programs\DokumentenScannerSortierung\app.exe")
        uninstaller = target.parent / installer.UNINSTALLER_FILENAME

        values = installer.installed_app_values(target, uninstaller, "0.1.19", 123_456)

        self.assertEqual("Simon Hagen – Glas Hagen", values["Publisher"])
        self.assertEqual("simon.hagen@glashagen.de", values["Contact"])
        self.assertEqual("0.1.19", values["DisplayVersion"])
        self.assertIn(str(uninstaller), str(values["UninstallString"]))
        self.assertEqual(1, values["NoModify"])
        self.assertEqual(1, values["NoRepair"])

    def test_uninstaller_payload_uses_embedded_source_filename(self) -> None:
        self.assertEqual("uninstall.ps1", installer.uninstaller_payload_path().name)


if __name__ == "__main__":
    unittest.main()
