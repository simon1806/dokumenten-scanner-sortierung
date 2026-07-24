from __future__ import annotations

import os
import queue
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scanner_sorter.app import (
    SettingsWindow,
    acquire_single_instance,
    app_asset_path,
    canonicalize_windows_network_path,
    cleanup_old_logs,
    initial_window_geometry,
    main,
    release_single_instance,
    single_instance_identity,
    single_instance_mutex_name,
    ui_icon_path,
)
from scanner_sorter.window_launcher import main as open_launcher_main


class AppTests(unittest.TestCase):
    def test_default_window_is_large_and_centered_on_full_hd_screen(self) -> None:
        width, height, x, y = initial_window_geometry(1920, 1080)

        self.assertEqual((1580, 1040, 170, 20), (width, height, x, y))

    def test_default_window_stays_inside_smaller_screen(self) -> None:
        width, height, x, y = initial_window_geometry(1366, 768)

        self.assertEqual((1326, 728, 20, 20), (width, height, x, y))

    def test_required_button_icons_are_available(self) -> None:
        for name in ("folder-open", "device-floppy", "player-play", "player-stop", "window-minimize", "power"):
            with self.subTest(name=name):
                self.assertTrue(ui_icon_path(name).is_file())

    def test_program_icons_are_available(self) -> None:
        self.assertTrue(app_asset_path("dokumenten-scanner-sortierung.ico").is_file())
        self.assertTrue(app_asset_path("dokumenten-scanner-sortierung.png").is_file())

    def test_fast_open_launcher_self_test_does_not_start_application(self) -> None:
        self.assertEqual(0, open_launcher_main(["--self-test"]))

    def test_fast_open_launcher_activates_existing_window_without_starting_main_application(self) -> None:
        with (
            patch("scanner_sorter.window_launcher.activate_existing_window", return_value=True) as activate,
            patch("scanner_sorter.window_launcher.start_main_application") as start,
        ):
            result = open_launcher_main([])

        self.assertEqual(0, result)
        activate.assert_called_once_with()
        start.assert_not_called()

    def test_fast_open_launcher_starts_main_application_when_no_window_exists(self) -> None:
        command = ("C:/Programme/DokumentenScannerSortierung.exe",)
        with (
            patch("scanner_sorter.window_launcher.activate_existing_window", return_value=False),
            patch("scanner_sorter.window_launcher.main_application_command", return_value=command),
            patch("scanner_sorter.window_launcher.start_main_application", return_value=True) as start,
        ):
            result = open_launcher_main([])

        self.assertEqual(0, result)
        start.assert_called_once_with(command)

    def test_tray_image_has_windows_notification_size(self) -> None:
        image = SettingsWindow._tray_image()

        self.assertEqual((64, 64), image.size)
        self.assertEqual("RGBA", image.mode)

    def test_input_folder_identity_is_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            incoming.mkdir()
            alternative = incoming / ".." / "eingang" / "."

            first = single_instance_identity(root / "one.json", str(incoming))
            second = single_instance_identity(root / "two.json", str(alternative))

            self.assertEqual(first, second)
            self.assertTrue(first.startswith("input:"))

    def test_settings_path_is_used_as_identity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "settings.json"

            identity = single_instance_identity(settings_path)

            self.assertTrue(identity.startswith("settings:"))
            self.assertIn("settings.json", identity)

    def test_mutex_uses_server_wide_global_namespace(self) -> None:
        name = single_instance_mutex_name(Path("settings.json"), r"C:\Scans\Eingang")

        self.assertTrue(name.startswith("Global\\DokumentenScannerSortierung-"))

    def test_mapped_network_path_is_canonicalized_to_unc(self) -> None:
        canonical = canonicalize_windows_network_path(
            r"S:\Eingang\Scans",
            drive_type_resolver=lambda _root: 4,
            universal_name_resolver=lambda _path: r"\\server\scanfreigabe\Eingang\Scans",
            drive_connection_resolver=lambda _drive: None,
        )

        self.assertEqual(r"\\server\scanfreigabe\Eingang\Scans", canonical)

    def test_mapped_drive_connection_is_used_when_full_path_resolution_fails(self) -> None:
        canonical = canonicalize_windows_network_path(
            r"S:\Eingang\Scans",
            drive_type_resolver=lambda _root: 4,
            universal_name_resolver=lambda _path: None,
            drive_connection_resolver=lambda _drive: r"\\server\scanfreigabe",
        )

        self.assertEqual(r"\\server\scanfreigabe\Eingang\Scans", canonical)

    def test_local_drive_is_left_unchanged_without_network_lookup(self) -> None:
        universal_resolver = Mock()
        connection_resolver = Mock()

        canonical = canonicalize_windows_network_path(
            r"C:\Scans\Eingang",
            drive_type_resolver=lambda _root: 3,
            universal_name_resolver=universal_resolver,
            drive_connection_resolver=connection_resolver,
        )

        self.assertEqual(r"C:\Scans\Eingang", canonical)
        universal_resolver.assert_not_called()
        connection_resolver.assert_not_called()

    def test_unresolved_remote_drive_fails_closed_with_unc_guidance(self) -> None:
        with self.assertRaisesRegex(OSError, "UNC-Pfad"):
            canonicalize_windows_network_path(
                r"S:\Eingang",
                drive_type_resolver=lambda _root: 4,
                universal_name_resolver=lambda _path: None,
                drive_connection_resolver=lambda _drive: None,
            )

    @unittest.skipUnless(os.name == "nt", "Pfadidentität verwendet Windows-Pfadregeln")
    def test_mapped_and_unc_input_produce_same_identity(self) -> None:
        mapped = r"S:\Eingang\Scans"
        unc = r"\\server\scanfreigabe\Eingang\Scans"
        with patch("scanner_sorter.app.canonicalize_windows_network_path") as canonicalize:
            canonicalize.side_effect = lambda path: unc if path.casefold() == mapped.casefold() else path
            mapped_identity = single_instance_identity(Path("one.json"), mapped)
            unc_identity = single_instance_identity(Path("two.json"), unc)

        self.assertEqual(mapped_identity, unc_identity)

    def test_log_cleanup_only_removes_matching_expired_daily_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            today = date(2026, 7, 15)
            expired = root / f"dokumentensortierer-{(today - timedelta(days=91)).isoformat()}.log"
            boundary = root / f"dokumentensortierer-{(today - timedelta(days=90)).isoformat()}.log"
            current = root / f"dokumentensortierer-{today.isoformat()}.log"
            unrelated = root / "dokumentensortierer.log"
            malformed = root / "dokumentensortierer-2026-99-99.log"
            for path in (expired, boundary, current, unrelated, malformed):
                path.write_text("test", encoding="utf-8")

            removed = cleanup_old_logs(root, retention_days=90, today=today)

            self.assertEqual(1, removed)
            self.assertFalse(expired.exists())
            for path in (boundary, current, unrelated, malformed):
                self.assertTrue(path.exists(), path.name)

    def test_save_is_rejected_while_watcher_is_running(self) -> None:
        window = object.__new__(SettingsWindow)
        window.watcher = SimpleNamespace(running=True)
        window._messagebox = Mock()

        result = window.save()

        self.assertIsNone(result)
        window._messagebox.showwarning.assert_called_once()

    def test_manual_archive_reset_is_rejected_while_watcher_is_running(self) -> None:
        window = object.__new__(SettingsWindow)
        window.watcher = SimpleNamespace(running=True)
        window._messagebox = Mock()

        window.clear_archive_manually()

        window._messagebox.showwarning.assert_called_once()

    def test_manual_archive_reset_requires_exact_confirmation_and_reports_result(self) -> None:
        window = object.__new__(SettingsWindow)
        window.watcher = None
        window.settings = SimpleNamespace(archive_folder="archiv")
        window.root = object()
        window._messagebox = Mock()
        window._messagebox.askyesno.return_value = True
        window._simpledialog = Mock()
        window._simpledialog.askstring.return_value = "ARCHIV LEEREN"
        window.status = Mock()
        window._append_activity = Mock()
        clear = Mock(return_value=SimpleNamespace(removed_files=4, removed_folders=2, skipped_entries=("manuell.txt",)))

        with patch("scanner_sorter.app.DocumentProcessor") as processor:
            processor.return_value.clear_archive_manually = clear
            window.clear_archive_manually()

        clear.assert_called_once_with()
        self.assertIn("4 Datei(en)", window.status.set.call_args.args[0])
        window._messagebox.showinfo.assert_called_once()

    def test_current_settings_reads_editable_protection_limits(self) -> None:
        class Value:
            def __init__(self, value: str):
                self.value = value

            def get(self) -> str:
                return self.value

        window = object.__new__(SettingsWindow)
        window.settings = SimpleNamespace(ocr_languages="deu+eng", tesseract_path="")
        window.fields = {
            "input_folder": Value("eingang"),
            "output_folder": Value("ziel"),
            "archive_folder": Value("archiv"),
            "review_folder": Value("pruefung"),
            "archive_retention_days": Value("30"),
            "settle_seconds": Value("2"),
            "invalid_pdf_timeout_seconds": Value("60"),
            "backlog_threshold": Value("4"),
            "backlog_pause_seconds": Value("12"),
            "processing_timeout_seconds": Value("95"),
        }

        settings = window._current_settings()

        self.assertEqual(4, settings.backlog_threshold)
        self.assertEqual(12, settings.backlog_pause_seconds)
        self.assertEqual(95, settings.processing_timeout_seconds)

    def test_worker_callback_only_enqueues_and_never_calls_tk(self) -> None:
        window = object.__new__(SettingsWindow)
        window._worker_messages = queue.Queue()

        window._from_worker("Überwachung beendet.")

        self.assertEqual("Überwachung beendet.", window._worker_messages.get_nowait())

    def test_worker_queue_is_drained_by_main_thread_poll(self) -> None:
        window = object.__new__(SettingsWindow)
        window._worker_messages = queue.Queue()
        window._worker_messages.put("Ordner wieder erreichbar.")
        window._worker_poll_after_id = None
        window._quitting = False
        window.status = Mock()
        window._append_activity = Mock()
        window.root = Mock()
        window.root.after.return_value = "poll-id"

        window._poll_worker_messages()

        window.status.set.assert_called_once_with("Ordner wieder erreichbar.")
        window._append_activity.assert_called_once_with("Ordner wieder erreichbar.")
        window.root.after.assert_called_once_with(100, window._poll_worker_messages)
        self.assertEqual("poll-id", window._worker_poll_after_id)

    def test_autostart_starts_monitoring_and_hides_only_when_tray_is_available(self) -> None:
        window = object.__new__(SettingsWindow)
        window.start = Mock()
        window.watcher = SimpleNamespace(running=True)
        window.tray_icon = object()
        window.hide_to_tray = Mock()

        window._start_from_autostart()

        window.start.assert_called_once_with()
        window.hide_to_tray.assert_called_once_with()

    def test_autostart_keeps_window_open_when_tray_is_unavailable(self) -> None:
        window = object.__new__(SettingsWindow)
        window.start = Mock()
        window.watcher = SimpleNamespace(running=True)
        window.tray_icon = None
        window.hide_to_tray = Mock()

        window._start_from_autostart()

        window.hide_to_tray.assert_not_called()

    def test_headless_mode_reports_corrupt_settings_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{invalid", encoding="utf-8")
            stderr = StringIO()

            with redirect_stderr(stderr):
                result = main(["--run", "--settings", str(path)])

            self.assertEqual(2, result)
            self.assertIn("beschädigt oder nicht lesbar", stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())

    def test_autostart_argument_opens_window_with_automatic_monitoring(self) -> None:
        settings_path = Path("autostart-settings.json")
        instance = object()
        with (
            patch("scanner_sorter.app.acquire_single_instance", return_value=(True, instance)),
            patch("scanner_sorter.app.configure_logging"),
            patch("scanner_sorter.app.release_single_instance") as release,
            patch("scanner_sorter.app.SettingsWindow") as window_type,
        ):
            result = main(["--autostart", "--settings", str(settings_path)])

        self.assertEqual(0, result)
        window_type.assert_called_once_with(settings_path, start_monitoring=True)
        window_type.return_value.run.assert_called_once_with()
        release.assert_called_once_with(instance)

    def test_self_test_runs_before_mutex_and_settings_access(self) -> None:
        with (
            patch("scanner_sorter.app.run_self_test", return_value=0) as self_test,
            patch("scanner_sorter.app.acquire_single_instance") as acquire,
        ):
            result = main(["--self-test"])

        self.assertEqual(0, result)
        self_test.assert_called_once_with()
        acquire.assert_not_called()

    def test_mutex_creation_error_is_reported_without_traceback(self) -> None:
        stderr = StringIO()
        with (
            patch(
                "scanner_sorter.app.acquire_single_instance",
                side_effect=OSError("Test: Global-Mutex nicht verfügbar"),
            ),
            redirect_stderr(stderr),
        ):
            result = main([])

        self.assertEqual(3, result)
        self.assertIn("Anwendungssperre konnte nicht erstellt werden", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

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

    @unittest.skipUnless(os.name == "nt", "Windows-Mutex wird nur unter Windows verwendet")
    def test_same_input_folder_is_locked_across_different_settings_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            incoming.mkdir()
            first_acquired, first_handle = acquire_single_instance(root / "one.json", str(incoming))
            try:
                second_acquired, second_handle = acquire_single_instance(
                    root / "two.json",
                    str(incoming / "."),
                )
                self.assertTrue(first_acquired)
                self.assertFalse(second_acquired)
                self.assertIsNone(second_handle)
            finally:
                release_single_instance(first_handle)


if __name__ == "__main__":
    unittest.main()
