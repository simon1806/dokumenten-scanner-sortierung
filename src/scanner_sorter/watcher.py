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
        self._thread: threading.Thread | None = None
        self._observed: dict[Path, tuple[int, float, float]] = {}
        self._last_cleanup = 0.0

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self.settings.ensure_directories()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="dokumentensortierer", daemon=True)
        self._thread.start()
        LOGGER.info(
            "Überwachung gestartet; Eingang=%s; Ziel=%s; Archiv=%s; Prüfordner=%s",
            self.settings.input_folder,
            self.settings.output_folder,
            self.settings.archive_folder,
            self.settings.review_folder_path,
        )
        self.on_status("Überwachung gestartet.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self.settings.poll_interval_seconds + 2)
        LOGGER.info("Überwachung beendet.")
        self.on_status("Überwachung beendet.")

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check_input_folder()
                self._cleanup_if_due()
            except Exception:
                LOGGER.exception("Fehler bei der Ordnerüberwachung")
                self.on_status("Ordnerüberwachung: Fehler, Details im Protokoll.")
            self._stop_event.wait(self.settings.poll_interval_seconds)

    def _check_input_folder(self) -> None:
        input_folder = Path(self.settings.input_folder)
        current_files = {path for path in input_folder.glob("*.pdf") if path.is_file()}
        now = time.monotonic()

        for path in list(self._observed):
            if path not in current_files:
                self._observed.pop(path, None)

        for path in sorted(current_files):
            stat = path.stat()
            state = self._observed.get(path)
            signature = (stat.st_size, stat.st_mtime)
            if state is None or signature != state[:2]:
                self._observed[path] = (*signature, now)
                continue
            if now - state[2] < self.settings.settle_seconds:
                continue
            if not self._pdf_is_complete(path):
                LOGGER.debug("PDF wird noch geschrieben oder ist noch nicht vollständig: %s", path)
                continue

            self._observed.pop(path, None)
            self.on_status(f"Verarbeite: {path.name}")
            result = self.processor.process(path)
            self.on_result(result)
            self.on_status(result.message)

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

    def _cleanup_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < 3600:
            return
        removed = self.processor.cleanup_archive()
        self._last_cleanup = now
        if removed:
            self.on_status(f"Archivbereinigung: {removed} Datei(en) gelöscht.")
