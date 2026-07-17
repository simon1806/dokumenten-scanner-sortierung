from __future__ import annotations

import unittest
from pathlib import Path

from installer import product


class UninstallerTests(unittest.TestCase):
    def test_uninstaller_script_preserves_settings_and_document_folders(self) -> None:
        script = (Path(__file__).parents[1] / "installer" / "uninstall.ps1").read_text(encoding="utf-8")

        self.assertIn("Einstellungen, Protokolle und sämtliche Dokumentordner bleiben erhalten", script)
        self.assertNotIn("APPDATA\\DokumentenScannerSortierung", script)
        self.assertIn("$ApplicationFilename", script)
        self.assertIn("$ShortcutFilename", script)
        self.assertIn('[Environment]::GetFolderPath("Startup")', script)
        self.assertIn("$RegistryPath", script)
        self.assertIn("Remove-Item -LiteralPath $installedFile -Force -ErrorAction Stop", script)
        self.assertIn('$VersionFilename = "version.txt"', script)

    def test_windows_uninstall_command_uses_hidden_powershell_script(self) -> None:
        self.assertTrue(product.UNINSTALLER_FILENAME.endswith(".ps1"))


if __name__ == "__main__":
    unittest.main()
