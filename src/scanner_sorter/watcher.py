from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from .config import Settings
from .models import ProcessResult
from .processing import DocumentProcessor

LOGGER = logging.getLogger(__name__)

StatusCallback = Callable[[str], None]
ResultCallback = Callable[[ProcessResult], None]


class FolderWatcher:
    """Poll an input folder and finish an active document before stopping.

    The worker is deliberately not a daemon thread.  A stop request prevents a
    new document from being started, while a document that is already inside
    ``DocumentProcessor.process`` is allowed to complete safely.
    """

    MAX_RETRY_BACKOFF_SECONDS = 60.0
    RECOVERY_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        settings: Settings,
        on_status: StatusCallback | None = None,
        on_result: ResultCallback | None = None,
    ):
        self.settings = settings
        self.processor = DocumentProcessor(settings)
        self.on_status = on_status or (lambda _message: None)
        self.on_result = on_result or (lambda _result: None)
        self._stop_event = threading.Event()
        self._processing_event = threading.Event()
        self._operation_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._observed: dict[Path, tuple[int, float, float, bool]] = {}
        self._last_cleanup = 0.0
        self._last_recovery = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def processing(self) -> bool:
        return self._processing_event.is_set()

    @property
    def stopping(self) -> bool:
        return self.running and self._stop_event.is_set()

    def _notify_status(self, message: str) -> None:
        try:
            self.on_status(message)
        except Exception:
            LOGGER.exception("Statusmeldung konnte nicht zugestellt werden: %s", message)

    def _notify_result(self, result: ProcessResult) -> None:
        try:
            self.on_result(result)
        except Exception:
            LOGGER.exception("Verarbeitungsergebnis konnte nicht zugestellt werden: %s", result.source_name)

    def _begin_operation(self) -> bool:
        """Atomically reject new work once a stop has been requested."""
        with self._operation_lock:
            if self._stop_event.is_set():
                return False
            self._processing_event.set()
            return True

    def _end_operation(self) -> None:
        with self._operation_lock:
            self._processing_event.clear()

    def start(self) -> None:
        with self._thread_lock:
            if self.running:
                return
            self._stop_event.clear()
            self._processing_event.clear()
            # A daemon thread could be terminated in the middle of publishing a
            # document when the GUI exits.  Keep the process alive until the
            # worker reaches its controlled shutdown point instead.
            self._thread = threading.Thread(target=self._run, name="dokumentensortierer", daemon=False)
            self._thread.start()
        LOGGER.info(
            "Überwachung gestartet; eingang=%s; ziel=%s; archiv=%s; pruefordner=%s; "
            "archiv_tage=%s; dateistabilitaet_s=%s; defekt_timeout_s=%s; abfrage_s=%s; "
            "ocr_sprachen=%s; tesseract=%s",
            self.settings.input_folder,
            self.settings.output_folder,
            self.settings.archive_folder,
            self.settings.review_folder_path,
            self.settings.archive_retention_days,
            self.settings.settle_seconds,
            self.settings.invalid_pdf_timeout_seconds,
            self.settings.poll_interval_seconds,
            self.settings.ocr_languages,
            self.settings.tesseract_path or "automatisch/mitgeliefert",
        )
        self._notify_status("Überwachung gestartet.")

    def request_stop(self) -> bool:
        """Request a controlled stop without waiting for the worker thread."""
        with self._operation_lock:
            if not self.running or self._stop_event.is_set():
                return False
            self._stop_event.set()
            processing = self._processing_event.is_set()
        if processing:
            message = "Beenden angefordert: Der laufende Vorgang wird noch sicher abgeschlossen."
        else:
            message = "Beenden angefordert: Die Überwachung wird kontrolliert beendet."
        LOGGER.info(message)
        self._notify_status(message)
        return True

    def stop(self, timeout: float | None = None) -> bool:
        """Stop and wait until the worker has really ended.

        With the default ``timeout=None`` this method guarantees that an active
        document has finished before it returns.  The final "beendet" status is
        emitted by the worker itself and therefore can never be premature.
        """
        thread = self._thread
        if thread is None or not thread.is_alive():
            return True
        self.request_stop()
        if threading.current_thread() is thread:
            return False
        thread.join(timeout=timeout)
        return not thread.is_alive()

    @classmethod
    def retry_backoff_seconds(cls, failure_count: int, poll_interval_seconds: float) -> float:
        """Return a bounded exponential retry delay for folder/network errors."""
        if failure_count < 1:
            return max(1.0, float(poll_interval_seconds))
        base = max(1.0, float(poll_interval_seconds))
        # Once the maximum has been reached there is no reason to keep growing
        # an unbounded integer during a long network outage.
        multiplier = 2 ** min(failure_count - 1, 16)
        return min(cls.MAX_RETRY_BACKOFF_SECONDS, base * multiplier)

    def _run(self) -> None:
        failure_count = 0
        try:
            while not self._stop_event.is_set():
                try:
                    # Do this inside the retry loop so a temporarily unavailable
                    # network share does not prevent the watcher from starting.
                    self.settings.ensure_directories()
                    if self._stop_event.is_set():
                        break
                    self._recover_if_due()
                    if self._stop_event.is_set():
                        break
                    self._check_input_folder()
                    if self._stop_event.is_set():
                        break
                    self._cleanup_if_due()
                except Exception as error:
                    if self._stop_event.is_set():
                        break
                    failure_count += 1
                    delay = self.retry_backoff_seconds(failure_count, self.settings.poll_interval_seconds)
                    LOGGER.exception(
                        "Fehler bei der Ordnerüberwachung; versuch=%s; erneut_in_s=%.1f; fehler=%s",
                        failure_count,
                        delay,
                        error,
                    )
                    self._notify_status(
                        "Ordner oder Netzwerk nicht erreichbar. "
                        f"Neuer Versuch in {delay:g} Sekunden; Details im Protokoll."
                    )
                    self._stop_event.wait(delay)
                    continue

                if failure_count:
                    LOGGER.info("Ordnerüberwachung nach %s Fehler(n) wiederhergestellt.", failure_count)
                    self._notify_status("Ordner und Netzwerk wieder erreichbar; Überwachung fortgesetzt.")
                    failure_count = 0
                self._stop_event.wait(self.settings.poll_interval_seconds)
        finally:
            self._end_operation()
            LOGGER.info("Überwachung beendet.")
            self._notify_status("Überwachung beendet.")

    def _check_input_folder(self) -> None:
        input_folder = Path(self.settings.input_folder)
        current_files = {path for path in input_folder.glob("*.pdf") if path.is_file()}
        now = time.monotonic()

        for path in list(self._observed):
            if path not in current_files:
                self._observed.pop(path, None)

        for path in sorted(current_files):
            # A stop request made while another file was being processed must
            # prevent the next file in this polling batch from being started.
            if self._stop_event.is_set():
                break
            stat = path.stat()
            state = self._observed.get(path)
            signature = (stat.st_size, stat.st_mtime)
            if state is None or signature != state[:2]:
                self._observed[path] = (*signature, now, False)
                continue
            if now - state[2] < self.settings.settle_seconds:
                continue
            if not self._pdf_is_complete(path):
                if not state[3]:
                    message = (
                        f"PDF noch unvollständig: {path.name}; Weiterleitung nach "
                        f"{self.settings.invalid_pdf_timeout_seconds} Sekunden ohne Dateiänderung."
                    )
                    LOGGER.warning(message)
                    self._notify_status(message)
                    self._observed[path] = (*state[:3], True)
                if now - state[2] < self.settings.invalid_pdf_timeout_seconds:
                    continue
                timeout_message = (
                    f"PDF nach {self.settings.invalid_pdf_timeout_seconds} Sekunden weiterhin unvollständig: "
                    f"{path.name}; Original wird zur Prüfung weitergeleitet."
                )
                LOGGER.error(timeout_message)
                self._notify_status(timeout_message)

            if not self._begin_operation():
                break
            self._observed.pop(path, None)
            self._notify_status(f"Verarbeite: {path.name}")
            try:
                result = self.processor.process(path)
            finally:
                self._end_operation()
            self._notify_result(result)
            self._notify_status(result.message)

    @staticmethod
    def _pdf_is_complete(path: Path) -> bool:
        """Only release a scan for processing once its PDF structure is readable."""
        try:
            import fitz

            with fitz.open(path) as document:
                if document.page_count < 1:
                    return False
                document.load_page(document.page_count - 1)
            return True
        except Exception:
            return False

    def _recover_if_due(self) -> None:
        now = time.monotonic()
        if self._last_recovery and now - self._last_recovery < self.RECOVERY_INTERVAL_SECONDS:
            return
        if not self._begin_operation():
            return
        try:
            results = self.processor.recover_incomplete_jobs(should_stop=self._stop_event.is_set)
        finally:
            self._end_operation()
        self._last_recovery = now
        for result in results:
            self._notify_result(result)
            self._notify_status(result.message)

    def _cleanup_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 3600:
            return
        removed = self.processor.cleanup_archive()
        self._last_cleanup = now
        if removed:
            self._notify_status(f"Archivbereinigung: {removed} Datei(en) gelöscht.")
