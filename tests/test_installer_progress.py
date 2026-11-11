from __future__ import annotations

import json
import os
import subprocess
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from installer import installer, windows_dialog


class InstallerProgressTests(unittest.TestCase):
    def test_large_file_copy_and_verification_report_intermediate_bytes(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.exe"
            source.write_bytes(b"MZ" + b"x" * (3 * 1024 * 1024))
            target = root / "install" / "application.exe"
            target.parent.mkdir()
            target.write_bytes(b"previous version")
            payload = installer.PayloadFile("application.exe", source, target, source.stat().st_size, installer.sha256_file(source))
            events = []
            transaction = installer.install_files_transactionally((payload,), lambda *event: events.append(event))
            for phase in ("stage", "replace"):
                values = [(done, total) for name, done, total, _ in events if name == phase]
                self.assertTrue(any(0 < done < total for done, total in values))
                self.assertEqual(values[-1][0], values[-1][1])
                self.assertEqual(sorted(done for done, _ in values), [done for done, _ in values])
            self.assertEqual(source.read_bytes(), target.read_bytes())
            transaction.rollback()
            self.assertEqual(b"previous version", target.read_bytes())

    def test_dialog_publishes_unicode_and_cleans_status_after_process_exit(self) -> None:
        with TemporaryDirectory() as directory:
            status = Path(directory) / "progress.json"
            dialog = windows_dialog.ProgressDialog(Mock(poll=Mock(return_value=0)), status)
            dialog.update(37, "Dateien prüfen", "Größe und Prüfsumme")
            state = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual({"percent": 37, "step": "Dateien prüfen", "detail": "Größe und Prüfsumme"}, state)
            dialog.close()
            self.assertFalse(status.exists())
            dialog.update(100, "Fertig")
            self.assertFalse(status.exists())

    def test_display_write_failure_does_not_abort_installation(self) -> None:
        dialog = windows_dialog.ProgressDialog(Mock(), Path("unused.response"))
        with patch.object(Path, "write_text", side_effect=PermissionError("locked")):
            dialog.update(20, "Dateien prüfen")

    def test_success_and_partial_failure_have_correct_final_progress(self) -> None:
        for server_failure in (False, True):
            with self.subTest(server_failure=server_failure), TemporaryDirectory() as directory, ExitStack() as stack:
                root = Path(directory)
                dialog = Mock()
                transaction = SimpleNamespace(backup_directory=root, commit=Mock(return_value=None), rollback=Mock())
                mocks = {
                    "installation_path": root / installer.APPLICATION_FILENAME,
                    "recover_orphaned_transactions": (), "application_version": "0.4.2",
                    "confirm_installation_selection": installer.InstallationSelection(True, True, dialog),
                    "payload_files": (), "validate_payload_bundle": (),
                    "installed_application_registration": None, "install_files_transactionally": transaction,
                    "capture_integration_snapshot": {}, "write_integration_snapshot": None,
                    "create_desktop_shortcut": root / "desktop.lnk", "register_installed_application": None,
                    "prepare_server_settings": root / "settings.json", "remove_startup_shortcut": None,
                    "notify_shell_icon_change": None, "show_completion": False,
                }
                stack.enter_context(patch.object(installer.sys, "argv", ["setup.exe", "--no-launch"]))
                for name, result in mocks.items():
                    stack.enter_context(patch(f"installer.installer.{name}", return_value=result))
                stack.enter_context(patch("installer.installer.create_or_update_server_autostart", side_effect=(
                    RuntimeError("task failed") if server_failure else None
                )))
                installer.run_installation()
                values = [call.args[0] for call in dialog.update.call_args_list]
                self.assertEqual(sorted(values), values)
                self.assertEqual(99 if server_failure else 100, values[-1])
                self.assertIn("7/8", str(dialog.update.call_args_list))

    @unittest.skipUnless(os.name == "nt", "Windows Forms requires Windows")
    def test_windows_timer_reads_actual_status_without_showing_a_window(self) -> None:
        with TemporaryDirectory() as directory:
            status = Path(directory) / "progress.json"
            status.write_text(json.dumps({"percent": 37, "step": "Dateien prüfen", "detail": "Testdatei"}), encoding="utf-8")
            script = windows_dialog._progress_script("Setup", "Start", "Bitte warten", Path("missing.ico"), status_path=status)
            script = script.replace("[System.Windows.Forms.Application]::Run($form)", (
                "$probe = New-Object System.Windows.Forms.Timer; $probe.Interval = 500; "
                "$probe.Add_Tick({ $probe.Stop(); "
                "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
                "[Console]::WriteLine((@{percent=$progress.Value;step=$titleLabel.Text;detail=$contentLabel.Text} | ConvertTo-Json -Compress)); "
                "[System.Windows.Forms.Application]::ExitThread() }); $probe.Start(); "
                "[System.Windows.Forms.Application]::Run(); $form.Dispose()"
            ))
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-STA", "-Command", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertEqual(0, result.returncode, result.stderr)
            state = json.loads(result.stdout)
            self.assertEqual(37, state["percent"])
            self.assertEqual("Dateien prüfen", state["step"])
            self.assertEqual("Testdatei", state["detail"])


if __name__ == "__main__":
    unittest.main()
