from __future__ import annotations

import unittest
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from installer import product


class UninstallerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("powershell"), "Windows PowerShell benötigt")
    def test_runtime_cleanup_preserves_modified_and_unknown_files(self) -> None:
        script = (Path(__file__).parents[1] / "installer" / "uninstall.ps1").read_text(encoding="utf-8")
        # Execute only runtime cleanup in an isolated fixture, never the actual
        # uninstaller, registry changes, task removal or self-deletion.
        cleanup = script[script.index('    $runtimeManifestPath ='):script.index('    foreach ($filename in @(')]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            runtime = root / "_internal" / "tesseract"
            runtime.mkdir(parents=True)
            original = b"owned-runtime"
            (runtime / "owned.dll").write_bytes(original)
            (runtime / "modified.dll").write_bytes(b"modified-runtime")
            (runtime / "unknown.txt").write_bytes(b"user-file")
            entry = {"size": len(original), "sha256": hashlib.sha256(original).hexdigest().upper()}
            (root / "runtime-files.json").write_text(json.dumps({
                "_internal/tesseract/owned.dll": entry, "_internal/tesseract/modified.dll": entry,
            }), encoding="utf-8")
            command = (
                "$ErrorActionPreference = 'Stop'\n"
                f"$InstallFolder = '{str(root).replace(chr(39), chr(39) * 2)}'\n"
                "$resolvedActual = [System.IO.Path]::GetFullPath($InstallFolder).TrimEnd('\\')\n"
                "$RuntimeManifestFilename = 'runtime-files.json'\n" + cleanup
            )
            result = subprocess.run(
                [shutil.which("powershell"), "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, errors="replace", timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertFalse((runtime / "owned.dll").exists())
            self.assertEqual(b"modified-runtime", (runtime / "modified.dll").read_bytes())
            self.assertEqual(b"user-file", (runtime / "unknown.txt").read_bytes())

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
        self.assertIn('$ServerAutostartTaskName = "GlasHagen Dokumenten-Scanner-Sortierung"', script)
        self.assertIn("Get-ScheduledTask -TaskName $ServerAutostartTaskName", script)
        self.assertIn("Unregister-ScheduledTask -TaskName $ServerAutostartTaskName", script)

    def test_windows_uninstall_command_uses_hidden_powershell_script(self) -> None:
        self.assertTrue(product.UNINSTALLER_FILENAME.endswith(".ps1"))


if __name__ == "__main__":
    unittest.main()
