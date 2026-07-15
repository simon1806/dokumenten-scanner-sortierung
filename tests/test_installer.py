from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from installer import installer, windows_dialog


class InstallerTests(unittest.TestCase):
    def test_first_install_prompt_uses_installation_action(self) -> None:
        title, instruction, _content, action = installer.prompt_text(False, "0.1.21")

        self.assertEqual("Installation bestätigen", title)
        self.assertIn("0.1.21", instruction)
        self.assertEqual("Installation ausführen", action)

    def test_update_prompt_uses_update_action(self) -> None:
        title, instruction, content, action = installer.prompt_text(True, "0.1.21", "0.1.14")

        self.assertEqual("Update bestätigen", title)
        self.assertIn("0.1.21", instruction)
        self.assertIn("Installierte Version: 0.1.14", content)
        self.assertIn("Neue Version: 0.1.21", content)
        self.assertIn("Update: 0.1.14 → 0.1.21", content)
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

        values = installer.installed_app_values(target, uninstaller, "0.1.21", 123_456)

        self.assertEqual("Simon Hagen – Glas Hagen", values["Publisher"])
        self.assertEqual("simon.hagen@glashagen.de", values["Contact"])
        self.assertEqual("0.1.21", values["DisplayVersion"])
        self.assertIn(str(uninstaller), str(values["UninstallString"]))
        self.assertEqual(1, values["NoModify"])
        self.assertEqual(1, values["NoRepair"])

    def test_uninstaller_payload_uses_embedded_source_filename(self) -> None:
        self.assertEqual("uninstall.ps1", installer.uninstaller_payload_path().name)

    def test_update_completion_contains_version_transition(self) -> None:
        target = Path(r"C:\Users\Test\AppData\Local\Programs\DokumentenScannerSortierung\app.exe")

        title, instruction, content = installer.completion_text(True, "0.1.21", "0.1.14", target)

        self.assertEqual("Update abgeschlossen", title)
        self.assertEqual("Update erfolgreich abgeschlossen", instruction)
        self.assertIn("Update: 0.1.14 → 0.1.21", content)
        self.assertIn(str(target.parent), content)

    def test_legacy_version_is_detected_from_existing_log(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "Programme" / installer.APPLICATION_FILENAME
            log_folder = root / "Roaming" / installer.APPLICATION_FOLDER / "logs"
            log_folder.mkdir(parents=True)
            (log_folder / "dokumentensortierer.log").write_text(
                "2026-07-14 INFO root: Anwendung gestartet; Version 0.1.14\n",
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"APPDATA": str(root / "Roaming")}):
                version = installer.installed_application_version(target)

        self.assertEqual("0.1.14", version)

    def test_installed_version_file_has_priority(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / installer.APPLICATION_FILENAME
            (target.parent / installer.VERSION_FILENAME).write_text("0.1.21\n", encoding="utf-8")

            version = installer.installed_application_version(target)

        self.assertEqual("0.1.21", version)

    @patch("installer.windows_dialog.subprocess.run")
    def test_completion_dialog_offers_checked_launch_and_finish_button(self, run: object) -> None:
        run.return_value = SimpleNamespace(returncode=0)

        should_launch = windows_dialog.show_completion(
            "Update abgeschlossen",
            "Update erfolgreich abgeschlossen",
            "Update: 0.1.14 → 0.1.21",
            Path("app.ico"),
        )

        script = run.call_args.args[0][-1]
        self.assertTrue(should_launch)
        self.assertIn("$launch.Checked = $true", script)
        self.assertIn("Anwendung starten", script)
        self.assertIn("Installation beenden", script)
        self.assertIn("Size(720,340)", script)
        self.assertIn("Size(670,120)", script)

    @patch("installer.windows_dialog.subprocess.run")
    def test_confirmation_dialog_has_enough_space_for_update_information(self, run: object) -> None:
        run.return_value = SimpleNamespace(returncode=0)

        confirmed = windows_dialog.show_confirmation(
            "Update bestätigen",
            "Update auf Version 0.1.21 ausführen?",
            "Installierte Version: 0.1.19\nNeue Version: 0.1.21\nUpdate: 0.1.19 → 0.1.21",
            "Update ausführen",
            Path("app.ico"),
        )

        script = run.call_args.args[0][-1]
        self.assertTrue(confirmed)
        self.assertIn("Size(720,350)", script)
        self.assertIn("Size(670,170)", script)

    @patch("installer.installer.subprocess.run")
    def test_running_installed_application_is_detected(self, run: object) -> None:
        run.return_value = SimpleNamespace(returncode=0)
        target = Path(r"C:\Users\Test\AppData\Local\Programs\DokumentenScannerSortierung\DokumentenScannerSortierung.exe")

        running = installer.is_application_running(target)

        self.assertTrue(running)
        script = run.call_args.args[0][-1]
        self.assertIn(target.stem, script)
        self.assertIn(str(target), script)


if __name__ == "__main__":
    unittest.main()
