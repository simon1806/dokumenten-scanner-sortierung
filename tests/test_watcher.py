from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from pypdf import PdfWriter

from scanner_sorter.config import Settings
from scanner_sorter.models import ProcessResult
from scanner_sorter.watcher import FolderWatcher


class WatcherTests(unittest.TestCase):
    @staticmethod
    def _settings(root: Path) -> Settings:
        return Settings(
            input_folder=str(root / "eingang"),
            output_folder=str(root / "ziel"),
            archive_folder=str(root / "archiv"),
            review_folder=str(root / "pruefung"),
            settle_seconds=1,
            invalid_pdf_timeout_seconds=1,
            poll_interval_seconds=1,
        )

    def test_pdf_completeness_check_rejects_partial_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            path.write_bytes(b"%PDF-1.7\npartial")

            self.assertFalse(FolderWatcher._pdf_is_complete(path))

    def test_pdf_completeness_check_accepts_finished_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=595, height=842)
            with path.open("wb") as stream:
                writer.write(stream)

            self.assertTrue(FolderWatcher._pdf_is_complete(path))

    def test_permanently_invalid_pdf_is_forwarded_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            review = root / "pruefung"
            incoming.mkdir()
            path = incoming / "beschaedigt.pdf"
            path.write_bytes(b"%PDF-1.7\npartial")
            settings = Settings(
                input_folder=str(incoming),
                output_folder=str(output),
                archive_folder=str(archive),
                review_folder=str(review),
                settle_seconds=1,
                invalid_pdf_timeout_seconds=1,
            )
            statuses: list[str] = []
            watcher = FolderWatcher(settings, on_status=statuses.append)
            stat = path.stat()
            watcher._observed[path] = (stat.st_size, stat.st_mtime, time.monotonic() - 2, False)

            watcher._check_input_folder()

            self.assertFalse(path.exists())
            self.assertTrue((output / "beschaedigt.pdf").exists())
            self.assertTrue((review / "beschaedigt.pdf").exists())
            self.assertEqual(1, len(list(archive.rglob("beschaedigt.pdf"))))
            self.assertTrue(any("weiterhin unvollständig" in status.lower() for status in statuses))

    def test_invalid_pdf_waits_until_timeout_and_reports_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            incoming.mkdir()
            path = incoming / "noch_nicht_fertig.pdf"
            path.write_bytes(b"%PDF-1.7\npartial")
            settings = Settings(
                input_folder=str(incoming),
                output_folder=str(root / "ziel"),
                archive_folder=str(root / "archiv"),
                settle_seconds=1,
                invalid_pdf_timeout_seconds=60,
            )
            statuses: list[str] = []
            watcher = FolderWatcher(settings, on_status=statuses.append)
            stat = path.stat()
            watcher._observed[path] = (stat.st_size, stat.st_mtime, time.monotonic() - 2, False)

            watcher._check_input_folder()

            self.assertTrue(path.exists())
            self.assertTrue(watcher._observed[path][3])
            self.assertTrue(any("PDF noch unvollständig" in status for status in statuses))

    def test_stop_waits_for_active_document_and_reports_finished_only_afterwards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            settings.ensure_directories()
            first_path = Path(settings.input_folder) / "01-scan.pdf"
            second_path = Path(settings.input_folder) / "02-scan.pdf"
            first_path.write_bytes(b"complete-enough-for-mocked-check")
            second_path.write_bytes(b"also-complete-enough")
            statuses: list[str] = []
            watcher = FolderWatcher(settings, on_status=statuses.append)
            for path in (first_path, second_path):
                stat = path.stat()
                watcher._observed[path] = (stat.st_size, stat.st_mtime, time.monotonic() - 2, False)
            entered = threading.Event()
            release = threading.Event()
            processed: list[str] = []

            def slow_process(source: Path) -> ProcessResult:
                processed.append(source.name)
                entered.set()
                self.assertTrue(release.wait(5), "Testverarbeitung wurde nicht freigegeben")
                return ProcessResult(source.name, True, "Verarbeitung abgeschlossen.")

            watcher.processor.process = slow_process
            watcher.processor.recover_incomplete_jobs = Mock(return_value=[])
            watcher.processor.cleanup_archive = Mock(return_value=0)
            watcher._pdf_is_complete = Mock(return_value=True)
            watcher.start()
            self.assertTrue(entered.wait(2), "Verarbeitung wurde nicht gestartet")
            self.assertIsNotNone(watcher._thread)
            self.assertFalse(watcher._thread.daemon)

            stop_thread = threading.Thread(target=watcher.stop)
            stop_thread.start()
            deadline = time.monotonic() + 2
            while (
                not any("laufende Vorgang" in status for status in statuses)
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)

            self.assertTrue(watcher.stopping)
            self.assertTrue(stop_thread.is_alive())
            self.assertTrue(any("laufende Vorgang" in status for status in statuses))
            self.assertFalse(any(status == "Überwachung beendet." for status in statuses))

            release.set()
            stop_thread.join(2)
            self.assertFalse(stop_thread.is_alive())
            self.assertFalse(watcher.running)
            self.assertEqual([first_path.name], processed)
            self.assertEqual("Überwachung beendet.", statuses[-1])

    def test_folder_errors_use_bounded_backoff_and_report_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self._settings(root)
            statuses: list[str] = []
            watcher = FolderWatcher(settings, on_status=statuses.append)
            watcher.processor.recover_incomplete_jobs = Mock(return_value=[])
            watcher.processor.cleanup_archive = Mock(return_value=0)
            attempts = 0

            def flaky_check() -> None:
                nonlocal attempts
                attempts += 1
                if attempts <= 2:
                    raise OSError("Netzwerk nicht erreichbar")

            waits: list[float] = []

            def record_wait(delay: float) -> bool:
                waits.append(delay)
                if len(waits) == 3:
                    watcher._stop_event.set()
                return False

            watcher._check_input_folder = flaky_check
            with patch.object(watcher._stop_event, "wait", side_effect=record_wait):
                watcher._run()

            self.assertEqual([1.0, 2.0], waits[:2])
            self.assertTrue(any("Neuer Versuch in 1" in status for status in statuses))
            self.assertTrue(any("Neuer Versuch in 2" in status for status in statuses))
            self.assertTrue(any("wieder erreichbar" in status for status in statuses))

    def test_retry_backoff_is_capped_for_long_outage(self) -> None:
        self.assertEqual(1.0, FolderWatcher.retry_backoff_seconds(1, 1))
        self.assertEqual(2.0, FolderWatcher.retry_backoff_seconds(2, 1))
        self.assertEqual(4.0, FolderWatcher.retry_backoff_seconds(3, 1))
        self.assertEqual(60.0, FolderWatcher.retry_backoff_seconds(100_000, 1))

    def test_recovery_results_are_forwarded_to_callbacks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = ProcessResult("scan.pdf", True, "Offenen Vorgang wiederhergestellt.")
            statuses: list[str] = []
            results: list[ProcessResult] = []
            watcher = FolderWatcher(self._settings(Path(directory)), statuses.append, results.append)
            watcher.processor.recover_incomplete_jobs = Mock(return_value=[result])

            watcher._recover_if_due()
            watcher._recover_if_due()

            self.assertEqual([result], results)
            self.assertEqual([result.message], statuses)
            watcher.processor.recover_incomplete_jobs.assert_called_once()
            should_stop = watcher.processor.recover_incomplete_jobs.call_args.kwargs["should_stop"]
            self.assertFalse(should_stop())

    def test_stop_during_first_recovery_job_prevents_second_pending_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            watcher = FolderWatcher(self._settings(Path(directory)))
            first_started = threading.Event()
            release_first = threading.Event()
            jobs_started: list[str] = []

            def recover_two_jobs(should_stop: object = None) -> list[ProcessResult]:
                results: list[ProcessResult] = []
                for job_name in ("pending-1", "pending-2"):
                    if callable(should_stop) and should_stop():
                        break
                    jobs_started.append(job_name)
                    if job_name == "pending-1":
                        first_started.set()
                        self.assertTrue(release_first.wait(5), "Erster Recovery-Job wurde nicht freigegeben")
                    results.append(ProcessResult(job_name, True, f"{job_name} wiederhergestellt."))
                return results

            watcher.processor.recover_incomplete_jobs = recover_two_jobs
            watcher.processor.cleanup_archive = Mock(return_value=0)
            watcher._check_input_folder = Mock()
            watcher.start()
            stop_thread: threading.Thread | None = None
            try:
                self.assertTrue(first_started.wait(2), "Erster Recovery-Job wurde nicht gestartet")
                stop_thread = threading.Thread(target=watcher.stop)
                stop_thread.start()
                deadline = time.monotonic() + 2
                while not watcher.stopping and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(watcher.stopping)
                self.assertTrue(stop_thread.is_alive())
            finally:
                release_first.set()
                if stop_thread is not None:
                    stop_thread.join(2)
                watcher.stop(timeout=2)

            self.assertEqual(["pending-1"], jobs_started)
            self.assertFalse(watcher.running)
            watcher._check_input_folder.assert_not_called()

    def test_stop_after_directory_check_prevents_recovery_and_new_document_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            watcher = FolderWatcher(self._settings(Path(directory)))
            watcher._recover_if_due = Mock()
            watcher._check_input_folder = Mock()
            watcher._cleanup_if_due = Mock()

            def stop_during_directory_check(_settings: Settings) -> None:
                watcher._stop_event.set()

            with patch.object(
                Settings,
                "ensure_directories",
                autospec=True,
                side_effect=stop_during_directory_check,
            ):
                watcher._run()

            watcher._recover_if_due.assert_not_called()
            watcher._check_input_folder.assert_not_called()
            watcher._cleanup_if_due.assert_not_called()


if __name__ == "__main__":
    unittest.main()
