from __future__ import annotations

import hashlib
import json
import shutil
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from installer import installer, windows_dialog


def verified_payload(name: str, source: Path, destination: Path) -> installer.PayloadFile:
    content = source.read_bytes()
    return installer.PayloadFile(
        name,
        source,
        destination,
        len(content),
        hashlib.sha256(content).hexdigest().upper(),
    )


def create_full_recovery_transaction(
    root: Path,
    transaction_id: str,
    mode: str = "replaced",
) -> tuple[installer.InstallationTransaction, Path, Path, bytes, bytes]:
    stage_directory = root / f".stage-{transaction_id}"
    backup_directory = root / f".backup-{transaction_id}"
    stage_directory.mkdir()
    backup_directory.mkdir()
    replacements: list[installer.FileReplacement] = []
    selected_destination = root / installer.APPLICATION_FILENAME
    selected_backup = backup_directory / f"00-{installer.APPLICATION_FILENAME}"
    selected_old = b""
    selected_new = b""
    for index, destination_name in enumerate(installer.PAYLOAD_DESTINATION_NAMES):
        destination = root / destination_name
        backup = backup_directory / f"{index:02d}-{destination_name}"
        stage = stage_directory / f"{index:02d}-{destination_name}"
        old_content = f"old:{destination_name}".encode()
        new_content = f"new:{destination_name}".encode()
        had_original = True
        if index == 0:
            selected_destination = destination
            selected_backup = backup
            selected_old = old_content
            selected_new = new_content
        if mode == "committed":
            destination.write_bytes(new_content)
            backup.write_bytes(old_content)
        elif index == 0:
            if mode == "replaced":
                destination.write_bytes(new_content)
                backup.write_bytes(old_content)
            elif mode == "new-file":
                had_original = False
                destination.write_bytes(new_content)
            elif mode == "corrupt-backup":
                destination.write_bytes(new_content)
                backup.write_bytes(b"corrupted-backup")
            elif mode == "unchanged":
                destination.write_bytes(old_content)
            else:
                raise ValueError(f"Unbekannter Recovery-Testmodus: {mode}")
        else:
            destination.write_bytes(old_content)
        replacements.append(
            installer.FileReplacement(
                destination,
                backup,
                had_original,
                stage=stage,
                original_size=len(old_content) if had_original else None,
                original_sha256=(
                    hashlib.sha256(old_content).hexdigest().upper() if had_original else None
                ),
                payload_size=len(new_content),
                payload_sha256=hashlib.sha256(new_content).hexdigest().upper(),
            )
        )
    transaction = installer.InstallationTransaction(
        stage_directory,
        backup_directory,
        replacements,
    )
    installer.write_transaction_journal(transaction)
    return transaction, selected_destination, selected_backup, selected_old, selected_new


class InstallerTests(unittest.TestCase):
    def test_first_install_prompt_uses_installation_action(self) -> None:
        title, instruction, _content, action = installer.prompt_text(False, "0.1.24")

        self.assertEqual("Installation bestätigen", title)
        self.assertIn("0.1.24", instruction)
        self.assertEqual("Installation ausführen", action)

    def test_update_prompt_uses_update_action(self) -> None:
        title, instruction, content, action = installer.prompt_text(True, "0.1.24", "0.1.14")

        self.assertEqual("Update bestätigen", title)
        self.assertIn("0.1.24", instruction)
        self.assertIn("Installierte Version: 0.1.14", content)
        self.assertIn("Neue Version: 0.1.24", content)
        self.assertIn("Update: 0.1.14 → 0.1.24", content)
        self.assertIn("Einstellungen", content)
        self.assertEqual("Update ausführen", action)

    @patch("installer.installer.subprocess.run")
    def test_desktop_shortcut_points_to_fast_open_launcher(self, run: object) -> None:
        target = Path(
            r"C:\Users\Test\AppData\Local\Programs\DokumentenScannerSortierung"
            + "\\"
            + installer.OPEN_LAUNCHER_FILENAME
        )
        icon = target.parent / installer.ICON_FILENAME

        installer.create_desktop_shortcut(target, icon)

        arguments = run.call_args.args[0]
        powershell_script = arguments[-1]
        self.assertIn(installer.SHORTCUT_FILENAME, powershell_script)
        self.assertIn(str(target), powershell_script)
        self.assertIn(str(icon), powershell_script)
        self.assertIn("$shortcut.IconLocation", powershell_script)
        self.assertTrue(run.call_args.kwargs["check"])

    @patch("installer.installer.subprocess.run")
    def test_desktop_shortcut_script_restores_backup_after_creation_error(self, run: object) -> None:
        target = Path(r"C:\Users\Test\Programs\DokumentenScannerSortierung\app.exe")
        backup = target.parent / ".backup" / installer.SHORTCUT_FILENAME

        installer.create_desktop_shortcut(target, backup_path=backup)

        script = run.call_args.args[0][-1]
        self.assertIn("$ErrorActionPreference = 'Stop'", script)
        self.assertIn("$shortcutExisted = Test-Path -LiteralPath $shortcutPath", script)
        self.assertIn("try {", script)
        self.assertIn("} catch {", script)
        self.assertIn(
            "Copy-Item -LiteralPath $backupPath -Destination $shortcutPath -Force",
            script,
        )
        self.assertIn("Remove-Item -LiteralPath $shortcutPath -Force", script)
        self.assertIn("throw $shortcutError", script)
        self.assertNotIn("Move-Item", script)
        self.assertNotIn("Remove-Item -LiteralPath $backupPath", script)

    def test_installed_apps_entry_contains_publisher_support_and_uninstaller(self) -> None:
        target = Path(r"C:\Users\Test\AppData\Local\Programs\DokumentenScannerSortierung\app.exe")
        uninstaller = target.parent / installer.UNINSTALLER_FILENAME

        values = installer.installed_app_values(target, uninstaller, "0.1.24", 123_456)

        self.assertEqual("Simon Hagen – Glas Hagen", values["Publisher"])
        self.assertEqual("simon.hagen@glashagen.de", values["Contact"])
        self.assertEqual("0.1.24", values["DisplayVersion"])
        self.assertIn(str(uninstaller), str(values["UninstallString"]))
        self.assertEqual(1, values["NoModify"])
        self.assertEqual(1, values["NoRepair"])

    @patch("installer.installer.subprocess.run")
    def test_startup_shortcut_starts_application_in_autostart_mode(self, run: object) -> None:
        target = Path(r"C:\Users\Test\Programs\DokumentenScannerSortierung\app.exe")
        icon = target.parent / installer.ICON_FILENAME

        installer.create_startup_shortcut(target, icon)

        script = run.call_args.args[0][-1]
        self.assertIn("GetFolderPath('Startup')", script)
        self.assertIn(installer.AUTOSTART_ARGUMENT, script)
        self.assertIn("$shortcut.Arguments", script)
        self.assertIn(str(target), script)

    @patch("installer.installer.subprocess.run")
    def test_server_autostart_task_runs_as_system_at_boot(self, run: Mock) -> None:
        run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
        target = Path(r"C:\Program Files\Scanner\DokumentenScannerSortierung.exe")
        settings = Path(r"C:\ProgramData\DokumentenScannerSortierung\settings.json")

        installer.create_or_update_server_autostart(target, settings)

        script = run.call_args.args[0][-1]
        self.assertIn("New-ScheduledTaskTrigger -AtStartup", script)
        self.assertIn("PT30S", script)
        self.assertIn("-MultipleInstances IgnoreNew", script)
        self.assertIn("-RestartCount 3", script)
        self.assertIn("-User 'SYSTEM' -RunLevel Highest", script)
        self.assertIn(installer.SERVER_AUTOSTART_TASK_NAME, script)
        self.assertIn('--run --settings "C:\\ProgramData\\DokumentenScannerSortierung\\settings.json"', script)

    def test_server_settings_are_copied_once_and_then_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Roaming" / installer.APPLICATION_FOLDER / installer.SERVER_SETTINGS_FILENAME
            source.parent.mkdir(parents=True)
            source.write_text('{"input_folder": "C:/Eingang"}', encoding="utf-8")
            environment = {
                "APPDATA": str(root / "Roaming"),
                "PROGRAMDATA": str(root / "ProgramData"),
            }
            with patch.dict("installer.installer.os.environ", environment, clear=False):
                destination = installer.prepare_server_settings()
                self.assertEqual('{"input_folder": "C:/Eingang"}', destination.read_text(encoding="utf-8"))
                destination.write_text('{"input_folder": "C:/Zentral"}', encoding="utf-8")
                source.write_text('{"input_folder": "C:/Neu"}', encoding="utf-8")

                self.assertEqual(destination, installer.prepare_server_settings())

            self.assertEqual('{"input_folder": "C:/Zentral"}', destination.read_text(encoding="utf-8"))

    def test_server_settings_require_saved_user_configuration(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                "APPDATA": str(root / "Roaming"),
                "PROGRAMDATA": str(root / "ProgramData"),
            }
            with patch.dict("installer.installer.os.environ", environment, clear=False):
                with self.assertRaisesRegex(RuntimeError, "Keine gespeicherten Einstellungen"):
                    installer.prepare_server_settings()

    def test_server_autostart_removes_user_startup_shortcut(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            shortcut = (
                root
                / "Roaming"
                / "Microsoft"
                / "Windows"
                / "Start Menu"
                / "Programs"
                / "Startup"
                / installer.STARTUP_SHORTCUT_FILENAME
            )
            shortcut.parent.mkdir(parents=True)
            shortcut.write_bytes(b"shortcut")
            with patch.dict("installer.installer.os.environ", {"APPDATA": str(root / "Roaming")}, clear=False):
                installer.remove_startup_shortcut()

            self.assertFalse(shortcut.exists())

    def test_uninstaller_payload_uses_embedded_source_filename(self) -> None:
        self.assertEqual("uninstall.ps1", installer.uninstaller_payload_path().name)

    def test_update_completion_contains_version_transition(self) -> None:
        target = Path(r"C:\Users\Test\AppData\Local\Programs\DokumentenScannerSortierung\app.exe")

        title, instruction, content = installer.completion_text(True, "0.1.24", "0.1.14", target)

        self.assertEqual("Update abgeschlossen", title)
        self.assertEqual("Update erfolgreich abgeschlossen", instruction)
        self.assertIn("Update: 0.1.14 → 0.1.24", content)
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
            (target.parent / installer.VERSION_FILENAME).write_text("0.1.24\n", encoding="utf-8")

            version = installer.installed_application_version(target)

        self.assertEqual("0.1.24", version)

    @patch("installer.windows_dialog.subprocess.run")
    def test_completion_dialog_offers_checked_launch_and_finish_button(self, run: object) -> None:
        run.return_value = SimpleNamespace(returncode=0)

        should_launch = windows_dialog.show_completion(
            "Update abgeschlossen",
            "Update erfolgreich abgeschlossen",
            "Update: 0.1.14 → 0.1.24",
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
    def test_server_autostart_choice_is_offered_by_confirmation_dialog(self, run: Mock) -> None:
        run.return_value = SimpleNamespace(returncode=10)

        confirmed, server_autostart = windows_dialog.show_confirmation_with_server_autostart(
            "Installation bestätigen",
            "Version installieren?",
            "Inhalt",
            "Installation ausführen",
            Path("app.ico"),
        )

        script = run.call_args.args[0][-1]
        self.assertTrue(confirmed)
        self.assertTrue(server_autostart)
        self.assertIn("Serverautostart beim Systemstart einrichten", script)
        self.assertIn("SYSTEM-Aufgabe", script)

    @patch("installer.windows_dialog.subprocess.run")
    def test_confirmation_dialog_has_enough_space_for_update_information(self, run: object) -> None:
        run.return_value = SimpleNamespace(returncode=0)

        confirmed = windows_dialog.show_confirmation(
            "Update bestätigen",
            "Update auf Version 0.1.24 ausführen?",
            "Installierte Version: 0.1.19\nNeue Version: 0.1.24\nUpdate: 0.1.19 → 0.1.24",
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

    def test_version_comparison_and_actions_prevent_accidental_downgrade(self) -> None:
        self.assertEqual(0, installer.compare_versions("0.1.24", "0.1.24.0"))
        self.assertEqual(1, installer.compare_versions("0.1.24", "0.1.19"))
        self.assertEqual(-1, installer.compare_versions("0.1.19", "0.1.24"))
        self.assertIsNone(installer.compare_versions("unbekannt", "0.1.24"))
        self.assertEqual("install", installer.determine_install_action(False, None, "0.1.24"))
        self.assertEqual("update", installer.determine_install_action(True, "0.1.19", "0.1.24"))
        self.assertEqual("repair", installer.determine_install_action(True, "0.1.24", "0.1.24"))
        self.assertEqual(
            "downgrade_blocked",
            installer.determine_install_action(True, "0.1.24", "0.1.19"),
        )
        self.assertEqual(
            "downgrade",
            installer.determine_install_action(True, "0.1.24", "0.1.19", allow_downgrade=True),
        )
        self.assertEqual(
            "unknown_version_blocked",
            installer.determine_install_action(True, None, "0.1.24"),
        )
        self.assertEqual(
            "update",
            installer.determine_install_action(
                True,
                "beschädigt",
                "0.1.24",
                allow_unknown_version=True,
            ),
        )

    def test_payload_manifest_detects_corruption(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            sources = {
                installer.APPLICATION_FILENAME: b"MZapplication",
                installer.OPEN_LAUNCHER_FILENAME: b"MZlauncher",
                installer.NOTICE_FILENAME: b"notices",
                installer.PAYLOAD_ICON_FILENAME: b"icon",
                installer.VERSION_FILENAME: b"0.1.24\n",
                installer.PAYLOAD_UNINSTALLER_FILENAME: b"uninstaller",
            }
            files: list[installer.PayloadFile] = []
            manifest_files: dict[str, dict[str, str | int]] = {}
            for name, content in sources.items():
                source = root / name
                source.write_bytes(content)
                destination_name = installer.PAYLOAD_LAYOUT[name]
                files.append(installer.PayloadFile(name, source, root / "target" / destination_name))
                manifest_files[name] = {
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest().upper(),
                }
            manifest = root / installer.PAYLOAD_MANIFEST_FILENAME
            manifest.write_text(
                json.dumps({"schema": 1, "version": "0.1.24", "files": manifest_files}),
                encoding="utf-8",
            )
            version_file = root / installer.VERSION_FILENAME
            with (
                patch("installer.installer.payload_manifest_path", return_value=manifest),
                patch("installer.installer.version_payload_path", return_value=version_file),
            ):
                validated = installer.validate_payload_bundle("0.1.24", tuple(files))
                self.assertTrue(all(payload.expected_size is not None for payload in validated))
                self.assertTrue(all(payload.expected_sha256 for payload in validated))
                (root / installer.NOTICE_FILENAME).write_bytes(b"manipuliert")
                with self.assertRaisesRegex(RuntimeError, "Payload-Prüfung fehlgeschlagen"):
                    installer.validate_payload_bundle("0.1.24", tuple(files))

    def test_transaction_rollback_restores_existing_files(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.exe"
            destination = root / "install" / "application.exe"
            destination.parent.mkdir()
            source.write_bytes(b"MZnew")
            destination.write_bytes(b"MZold")
            transaction = installer.install_files_transactionally(
                (verified_payload("application.exe", source, destination),)
            )

            self.assertEqual(b"MZnew", destination.read_bytes())
            transaction.rollback()

            self.assertEqual(b"MZold", destination.read_bytes())
            self.assertFalse(transaction.stage_directory.exists())
            self.assertFalse(transaction.backup_directory.exists())

    def test_transaction_commit_keeps_new_files_and_removes_temporary_data(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.exe"
            destination = root / "install" / "application.exe"
            source.write_bytes(b"MZnew")
            transaction = installer.install_files_transactionally(
                (verified_payload("application.exe", source, destination),)
            )

            transaction.commit()

            self.assertEqual(b"MZnew", destination.read_bytes())
            self.assertFalse(transaction.stage_directory.exists())
            self.assertFalse(transaction.backup_directory.exists())

    def test_transaction_keeps_original_when_first_backup_move_fails(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.exe"
            destination = root / "install" / "application.exe"
            destination.parent.mkdir()
            original_content = b"MZexisting-application-byte-for-byte"
            source.write_bytes(b"MZnew")
            destination.write_bytes(original_content)

            real_replace = installer.os.replace

            def fail_payload_backup(source_path: Path, destination_path: Path) -> None:
                if (
                    Path(destination_path).parent.name.startswith(".backup-")
                    and Path(destination_path).name != installer.TRANSACTION_JOURNAL_FILENAME
                ):
                    raise OSError("backup locked")
                real_replace(source_path, destination_path)

            with patch("installer.installer.os.replace", side_effect=fail_payload_backup):
                with self.assertRaisesRegex(OSError, "backup locked"):
                    installer.install_files_transactionally(
                        (verified_payload("application.exe", source, destination),)
                    )

            self.assertEqual(original_content, destination.read_bytes())

    def test_transaction_restores_original_when_stage_move_fails_after_backup(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.exe"
            destination = root / "install" / "application.exe"
            destination.parent.mkdir()
            original_content = b"MZexisting-application-byte-for-byte"
            source.write_bytes(b"MZnew")
            destination.write_bytes(original_content)
            real_replace = installer.os.replace
            replace_calls = 0

            def fail_second_replace(source_path: Path, destination_path: Path) -> None:
                nonlocal replace_calls
                replace_calls += 1
                if replace_calls == 2:
                    raise OSError("stage move failed")
                real_replace(source_path, destination_path)

            with patch("installer.installer.os.replace", side_effect=fail_second_replace):
                with self.assertRaisesRegex(OSError, "stage move failed"):
                    installer.install_files_transactionally(
                        (verified_payload("application.exe", source, destination),)
                    )

            self.assertEqual(4, replace_calls)
            self.assertEqual(original_content, destination.read_bytes())

    def test_staging_uses_manifest_identity_and_detects_toctou_change(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.exe"
            destination = root / "install" / "application.exe"
            source.write_bytes(b"MZvalidated")
            payload = verified_payload("application.exe", source, destination)
            source.write_bytes(b"MZtampered!")

            with self.assertRaisesRegex(OSError, "nicht mehr manifestkonform"):
                installer.install_files_transactionally((payload,))

            self.assertFalse(destination.exists())
            self.assertFalse(any(destination.parent.glob(".stage-*")))
            self.assertFalse(any(destination.parent.glob(".backup-*")))

    def test_failed_restore_preserves_backup_and_reports_recovery_paths(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage_directory = root / ".stage-test"
            backup_directory = root / ".backup-test"
            stage_directory.mkdir()
            backup_directory.mkdir()
            destination = root / "application.exe"
            backup = backup_directory / "00-application.exe"
            new_content = b"MZnew"
            old_content = b"MZold-last-copy"
            destination.write_bytes(new_content)
            backup.write_bytes(old_content)
            replacement = installer.FileReplacement(
                destination,
                backup,
                True,
                original_size=len(old_content),
                original_sha256=hashlib.sha256(old_content).hexdigest().upper(),
                payload_size=len(new_content),
                payload_sha256=hashlib.sha256(new_content).hexdigest().upper(),
                backup_created=True,
                destination_replaced=True,
            )
            transaction = installer.InstallationTransaction(
                stage_directory,
                backup_directory,
                [replacement],
            )

            with patch("installer.installer.os.replace", side_effect=OSError("restore denied")):
                with self.assertRaisesRegex(RuntimeError, str(backup_directory).replace("\\", r"\\")):
                    transaction.rollback()

            self.assertEqual(old_content, backup.read_bytes())
            self.assertEqual(new_content, destination.read_bytes())
            self.assertTrue(stage_directory.exists())
            self.assertTrue(backup_directory.exists())
            self.assertFalse(transaction.finished)

    def test_rollback_validates_backup_before_touching_new_destination(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage_directory = root / ".stage-test"
            backup_directory = root / ".backup-test"
            stage_directory.mkdir()
            backup_directory.mkdir()
            destination = root / "application.exe"
            backup = backup_directory / "00-application.exe"
            expected_old = b"expected-old"
            new_content = b"expected-new"
            destination.write_bytes(new_content)
            backup.write_bytes(b"corrupt-old")
            replacement = installer.FileReplacement(
                destination,
                backup,
                True,
                original_size=len(expected_old),
                original_sha256=hashlib.sha256(expected_old).hexdigest().upper(),
                payload_size=len(new_content),
                payload_sha256=hashlib.sha256(new_content).hexdigest().upper(),
                backup_created=True,
                destination_replaced=True,
            )
            transaction = installer.InstallationTransaction(
                stage_directory,
                backup_directory,
                [replacement],
            )

            with self.assertRaisesRegex(RuntimeError, "Installationsbackup.*verändert"):
                transaction.rollback()

            self.assertEqual(new_content, destination.read_bytes())
            self.assertEqual(b"corrupt-old", backup.read_bytes())

    def test_rollback_never_overwrites_unexpected_destination(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage_directory = root / ".stage-test"
            backup_directory = root / ".backup-test"
            stage_directory.mkdir()
            backup_directory.mkdir()
            destination = root / "application.exe"
            backup = backup_directory / "00-application.exe"
            old_content = b"expected-old"
            expected_payload = b"expected-new"
            foreign_content = b"foreign-file"
            destination.write_bytes(foreign_content)
            backup.write_bytes(old_content)
            replacement = installer.FileReplacement(
                destination,
                backup,
                True,
                original_size=len(old_content),
                original_sha256=hashlib.sha256(old_content).hexdigest().upper(),
                payload_size=len(expected_payload),
                payload_sha256=hashlib.sha256(expected_payload).hexdigest().upper(),
                backup_created=True,
                destination_replaced=True,
            )

            with self.assertRaisesRegex(RuntimeError, "Payload-Datei.*verändert"):
                installer.InstallationTransaction(
                    stage_directory,
                    backup_directory,
                    [replacement],
                ).rollback()

            self.assertEqual(foreign_content, destination.read_bytes())
            self.assertEqual(old_content, backup.read_bytes())

    def test_rollback_without_original_removes_only_own_payload(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage_directory = root / ".stage-test"
            backup_directory = root / ".backup-test"
            stage_directory.mkdir()
            backup_directory.mkdir()
            destination = root / "new-file.txt"
            expected_payload = b"expected-new"
            foreign_content = b"foreign-file"
            destination.write_bytes(foreign_content)
            replacement = installer.FileReplacement(
                destination,
                backup_directory / "00-new-file.txt",
                False,
                payload_size=len(expected_payload),
                payload_sha256=hashlib.sha256(expected_payload).hexdigest().upper(),
                destination_replaced=True,
            )

            with self.assertRaisesRegex(RuntimeError, "Payload-Datei.*verändert"):
                installer.InstallationTransaction(
                    stage_directory,
                    backup_directory,
                    [replacement],
                ).rollback()

            self.assertEqual(foreign_content, destination.read_bytes())

    def test_orphaned_transaction_is_rolled_back_from_journal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, _backup, old_content, _new_content = (
                create_full_recovery_transaction(root, "crash", "replaced")
            )

            recovered = installer.recover_orphaned_transactions(root)

            self.assertEqual(("crash",), recovered)
            self.assertEqual(old_content, destination.read_bytes())
            self.assertFalse(transaction.stage_directory.exists())
            self.assertFalse(transaction.backup_directory.exists())

    def test_orphaned_new_file_is_removed_only_when_payload_identity_matches(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, _backup, _old_content, _new_content = (
                create_full_recovery_transaction(root, "new-file", "new-file")
            )

            installer.recover_orphaned_transactions(root)

            self.assertFalse(destination.exists())
            self.assertFalse(transaction.stage_directory.exists())
            self.assertFalse(transaction.backup_directory.exists())

    def test_orphaned_corrupt_backup_is_blocked_and_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, _destination, backup, _old_content, _new_content = (
                create_full_recovery_transaction(root, "corrupt-backup", "corrupt-backup")
            )

            with self.assertRaisesRegex(RuntimeError, "Installationsbackup.*beschädigt"):
                installer.recover_orphaned_transactions(root)

            self.assertEqual(b"corrupted-backup", backup.read_bytes())
            self.assertTrue(transaction.stage_directory.exists())
            self.assertTrue(transaction.backup_directory.exists())

    def test_committed_backup_only_residue_is_cleaned_without_rollback(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, _backup, _old_content, new_content = (
                create_full_recovery_transaction(root, "committed", "committed")
            )
            installer.mark_transaction_committed(transaction)
            transaction.committed = True
            shutil.rmtree(transaction.stage_directory)

            recovered = installer.recover_orphaned_transactions(root)

            self.assertEqual(("committed",), recovered)
            self.assertEqual(new_content, destination.read_bytes())
            self.assertFalse(transaction.backup_directory.exists())

    def test_commit_cleanup_failure_is_reported_and_retried_on_next_start(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, _backup, _old_content, new_content = (
                create_full_recovery_transaction(root, "cleanup", "committed")
            )
            with patch("installer.installer.shutil.rmtree", side_effect=OSError("directory busy")):
                warning = transaction.commit()

            self.assertIn("nicht bereinigt", warning or "")
            self.assertTrue(transaction.committed)
            self.assertTrue(transaction.stage_directory.exists())
            self.assertTrue(transaction.backup_directory.exists())

            installer.recover_orphaned_transactions(root)

            self.assertEqual(new_content, destination.read_bytes())
            self.assertFalse(transaction.stage_directory.exists())
            self.assertFalse(transaction.backup_directory.exists())

    def test_rollback_cleanup_fault_after_stage_deletion_is_retryable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, _backup, old_content, _new_content = (
                create_full_recovery_transaction(root, "rollback-cleanup", "replaced")
            )
            real_rmtree = shutil.rmtree

            def delete_stage_then_fail_backup(path: Path, *args: object, **kwargs: object) -> None:
                if Path(path) == transaction.backup_directory:
                    raise OSError("backup directory busy")
                real_rmtree(path, *args, **kwargs)

            with patch("installer.installer.shutil.rmtree", side_effect=delete_stage_then_fail_backup):
                with self.assertRaisesRegex(RuntimeError, "Backup-Ordner.*nicht entfernt"):
                    installer.recover_orphaned_transactions(root)

            self.assertEqual(old_content, destination.read_bytes())
            self.assertFalse(transaction.stage_directory.exists())
            self.assertTrue(transaction.backup_directory.exists())
            journal = json.loads(
                (transaction.backup_directory / installer.TRANSACTION_JOURNAL_FILENAME).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(installer.TRANSACTION_STATE_ROLLED_BACK, journal["state"])

            recovered = installer.recover_orphaned_transactions(root)

            self.assertEqual(("rollback-cleanup",), recovered)
            self.assertEqual(old_content, destination.read_bytes())
            self.assertFalse(transaction.backup_directory.exists())

    def test_prepared_backup_only_residue_is_cleaned_after_exact_original_check(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, _backup, old_content, _new_content = (
                create_full_recovery_transaction(root, "legacy-rollback", "unchanged")
            )
            shutil.rmtree(transaction.stage_directory)

            recovered = installer.recover_orphaned_transactions(root)

            self.assertEqual(("legacy-rollback",), recovered)
            self.assertEqual(old_content, destination.read_bytes())
            self.assertFalse(transaction.backup_directory.exists())

    def test_prepared_backup_only_residue_accepts_exact_nonexistent_original_state(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, _backup, _old_content, _new_content = (
                create_full_recovery_transaction(root, "legacy-new-file", "new-file")
            )
            destination.unlink()
            shutil.rmtree(transaction.stage_directory)

            recovered = installer.recover_orphaned_transactions(root)

            self.assertEqual(("legacy-new-file",), recovered)
            self.assertFalse(destination.exists())
            self.assertFalse(transaction.backup_directory.exists())

    def test_prepared_backup_only_residue_fails_closed_when_target_is_not_original(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, backup, _old_content, new_content = (
                create_full_recovery_transaction(root, "ambiguous-rollback", "replaced")
            )
            shutil.rmtree(transaction.stage_directory)

            with self.assertRaisesRegex(RuntimeError, "nicht eindeutig vollständig zurückgerollt"):
                installer.recover_orphaned_transactions(root)

            self.assertEqual(new_content, destination.read_bytes())
            self.assertTrue(backup.exists())
            self.assertTrue(transaction.backup_directory.exists())

    def test_committed_cleanup_fails_closed_when_payload_was_changed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, destination, backup, _old_content, _new_content = (
                create_full_recovery_transaction(root, "committed-tampered", "committed")
            )
            installer.mark_transaction_committed(transaction)
            destination.write_bytes(b"foreign-after-commit")
            shutil.rmtree(transaction.stage_directory)

            with self.assertRaisesRegex(RuntimeError, "festgeschriebene Payload-Datei"):
                installer.recover_orphaned_transactions(root)

            self.assertEqual(b"foreign-after-commit", destination.read_bytes())
            self.assertTrue(backup.exists())
            self.assertTrue(transaction.backup_directory.exists())

    def test_recovery_journal_rejects_case_insensitive_duplicate_targets(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, _destination, _backup, _old_content, _new_content = (
                create_full_recovery_transaction(root, "duplicate", "unchanged")
            )
            journal_path = transaction.backup_directory / installer.TRANSACTION_JOURNAL_FILENAME
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            journal["records"][1]["destination_name"] = installer.APPLICATION_FILENAME.lower()
            journal_path.write_text(json.dumps(journal), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "Doppelter oder mehrdeutiger"):
                installer.recover_orphaned_transactions(root)

            self.assertTrue(transaction.stage_directory.exists())
            self.assertTrue(transaction.backup_directory.exists())

    def test_recovery_rejects_reparse_transaction_directory_without_deletion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            transaction, _destination, _backup, _old_content, _new_content = (
                create_full_recovery_transaction(root, "reparse", "unchanged")
            )
            real_is_reparse = installer.is_reparse_point

            def mark_backup_as_reparse(path: Path) -> bool:
                return path == transaction.backup_directory or real_is_reparse(path)

            with patch("installer.installer.is_reparse_point", side_effect=mark_backup_as_reparse):
                with self.assertRaisesRegex(OSError, "Reparse-Point"):
                    installer.recover_orphaned_transactions(root)

            self.assertTrue(transaction.stage_directory.exists())
            self.assertTrue(transaction.backup_directory.exists())

    def test_incomplete_orphan_is_blocked_without_deletion(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage_directory = root / ".stage-incomplete"
            stage_directory.mkdir()
            (stage_directory / "payload.exe").write_bytes(b"MZpayload")

            with self.assertRaisesRegex(RuntimeError, "manuelle Prüfung"):
                installer.recover_orphaned_transactions(root)

            self.assertTrue(stage_directory.exists())
            self.assertEqual(b"MZpayload", (stage_directory / "payload.exe").read_bytes())

    def test_orphan_with_broken_journal_is_blocked_and_preserved(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            stage_directory = root / ".stage-broken"
            backup_directory = root / ".backup-broken"
            stage_directory.mkdir()
            backup_directory.mkdir()
            journal = backup_directory / installer.TRANSACTION_JOURNAL_FILENAME
            journal.write_text("{unvollständig", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "nicht automatisch"):
                installer.recover_orphaned_transactions(root)

            self.assertTrue(stage_directory.exists())
            self.assertTrue(backup_directory.exists())
            self.assertEqual("{unvollständig", journal.read_text(encoding="utf-8"))

    def test_setup_mutex_is_global_stable_and_blocks_second_installer(self) -> None:
        self.assertEqual(
            r"Global\GlasHagen.DokumentenScannerSortierung.Setup",
            installer.SETUP_MUTEX_NAME,
        )
        with (
            patch.object(installer.sys, "argv", ["setup.exe"]),
            patch("installer.installer.acquire_setup_mutex", return_value=None),
            patch("installer.installer.show_message") as show_message,
            patch("installer.installer.run_installation") as run_installation,
        ):
            result = installer.main()

        self.assertEqual(3, result)
        run_installation.assert_not_called()
        self.assertIn("bereits", show_message.call_args.args[1])

    def test_setup_mutex_is_held_until_installer_finishes(self) -> None:
        mutex = Mock()
        with (
            patch.object(installer.sys, "argv", ["setup.exe"]),
            patch("installer.installer.acquire_setup_mutex", return_value=mutex),
            patch("installer.installer.run_installation", return_value=7),
        ):
            result = installer.main()

        self.assertEqual(7, result)
        mutex.release.assert_called_once_with()

    def test_setup_self_test_validates_payload_without_installation_or_mutex(self) -> None:
        payloads = tuple(
            installer.PayloadFile(f"file-{index}", Path(f"source-{index}"), Path(f"target-{index}"))
            for index in range(5)
        )
        with (
            patch.object(installer.sys, "argv", ["setup.exe", "--self-test"]),
            patch("installer.installer.application_version", return_value="0.1.24"),
            patch("installer.installer.payload_files", return_value=payloads),
            patch("installer.installer.validate_payload_bundle", return_value=payloads) as validate,
            patch("installer.installer.acquire_setup_mutex") as acquire_mutex,
            patch("installer.installer.run_installation") as run_installation,
        ):
            result = installer.main()

        self.assertEqual(0, result)
        validate.assert_called_once()
        acquire_mutex.assert_not_called()
        run_installation.assert_not_called()

    def test_existing_target_with_unknown_version_is_blocked_before_confirmation(self) -> None:
        with TemporaryDirectory() as directory:
            target = Path(directory) / installer.APPLICATION_FILENAME
            target.write_bytes(b"MZexisting")
            with (
                patch("installer.installer.installation_path", return_value=target),
                patch("installer.installer.recover_orphaned_transactions", return_value=()),
                patch("installer.installer.application_version", return_value="0.1.24"),
                patch("installer.installer.installed_application_version", return_value=None),
                patch("installer.installer.show_message") as show_message,
                patch("installer.installer.confirm_installation") as confirm,
            ):
                result = installer.run_installation()

        self.assertEqual(2, result)
        confirm.assert_not_called()
        self.assertEqual("Installation blockiert", show_message.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
