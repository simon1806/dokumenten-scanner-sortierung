from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from pypdf import PdfReader, PdfWriter

from .config import Settings
from .models import DetectedDocument, DocumentGroup, ProcessResult
from .recognition import PageRecognizer

LOGGER = logging.getLogger(__name__)


class ProcessingError(RuntimeError):
    pass


def group_page_detections(detections: Iterable[DetectedDocument | None]) -> list[DocumentGroup]:
    """Group continuation pages with the most recently recognised document."""
    groups: list[DocumentGroup] = []
    current: DocumentGroup | None = None

    for page_index, detection in enumerate(detections):
        if detection is None:
            if current is None:
                raise ProcessingError(f"Seite {page_index + 1} konnte keinem Dokument zugeordnet werden.")
            current.page_indexes.append(page_index)
            continue

        if current and current.detected.key == detection.key:
            current.page_indexes.append(page_index)
            continue

        current = DocumentGroup(detected=detection, page_indexes=[page_index])
        groups.append(current)

    if not groups:
        raise ProcessingError("Es wurde kein unterstützter Dokumenttyp erkannt.")
    return groups


class DocumentProcessor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.recognizer = PageRecognizer(settings)

    def process(self, source: Path) -> ProcessResult:
        started = time.perf_counter()
        source_name = source.name
        LOGGER.info("Verarbeitung gestartet: %s", source)

        try:
            archive_path = self._archive_original(source)
        except Exception as error:
            LOGGER.exception("Original konnte nicht archiviert werden: %s", source)
            return ProcessResult(
                source_name,
                False,
                f"Verarbeitung nicht gestartet: Original konnte nicht archiviert werden ({error}).",
            )

        try:
            source.unlink()
        except Exception as error:
            try:
                archive_path.unlink()
            except Exception:
                LOGGER.exception("Temporäre Archivkopie konnte nicht zurückgerollt werden: %s", archive_path)
            LOGGER.exception(
                "Eingangsdatei konnte nicht entfernt werden; es wurden keine Ausgabedokumente erzeugt: %s",
                source,
            )
            return ProcessResult(
                source_name,
                False,
                f"Verarbeitung nicht gestartet: Eingangsdatei konnte nicht entfernt werden ({error}).",
            )

        try:
            created = self._split_and_store(archive_path)
        except ProcessingError as error:
            LOGGER.warning("Dokument nicht erkannt: %s (%s)", source_name, error)
            return self._handle_failed_processing(archive_path, source_name, error, started)
        except Exception as error:
            LOGGER.exception("Verarbeitung fehlgeschlagen: %s", source_name)
            return self._handle_failed_processing(archive_path, source_name, error, started)

        message = f"{len(created)} Dokument(e) erstellt; Original archiviert: {archive_path.name}"
        LOGGER.info(
            "Verarbeitung erfolgreich: %s; Dauer %.2f s; Ausgaben: %s",
            source_name,
            time.perf_counter() - started,
            ", ".join(path.name for path in created),
        )
        return ProcessResult(source_name, True, message, tuple(str(path) for path in created))

    def _handle_failed_processing(
        self,
        archived_source: Path,
        source_name: str,
        error: Exception,
        started: float,
    ) -> ProcessResult:
        try:
            forwarded, review_copy = self._forward_archived_original(archived_source, source_name)
        except Exception as forward_error:
            LOGGER.exception("Fehlerdatei konnte nicht vollständig weitergeleitet werden: %s", source_name)
            return ProcessResult(
                source_name,
                False,
                f"Verarbeitung fehlgeschlagen ({error}); Original ist im Archiv, "
                f"Weiterleitung fehlgeschlagen ({forward_error}).",
            )
        message = (
            f"Nicht erkannt: Original unverändert weitergeleitet; "
            f"Prüfkopie: {review_copy.name} ({error})."
        )
        LOGGER.warning(
            "Nicht erkannt: %s; Dauer %.2f s; Ziel: %s; Prüfkopie: %s; Grund: %s",
            source_name,
            time.perf_counter() - started,
            forwarded,
            review_copy,
            error,
        )
        return ProcessResult(source_name, False, message, (str(forwarded), str(review_copy)))

    def _split_and_store(self, source: Path) -> list[Path]:
        try:
            import fitz
        except ImportError as error:  # pragma: no cover - dependency check at runtime
            raise ProcessingError("PyMuPDF ist nicht installiert.") from error

        recognise_document = getattr(self.recognizer, "recognise_document", None)
        if callable(recognise_document):
            detections = recognise_document(source)
        else:
            with fitz.open(source) as scan:
                detections = [self.recognizer.recognise(page) for page in scan]
        groups = group_page_detections(detections)

        reader = PdfReader(source)
        created: list[Path] = []
        for group in groups:
            destination = self._unique_path(Path(self.settings.output_folder), group.detected.filename)
            writer = PdfWriter()
            for page_index in group.page_indexes:
                writer.add_page(reader.pages[page_index])
            temporary = destination.with_suffix(".tmp")
            with temporary.open("wb") as stream:
                writer.write(stream)
            temporary.replace(destination)
            created.append(destination)
        return created

    def _forward_archived_original(self, source: Path, source_name: str) -> tuple[Path, Path]:
        output_path = self._unique_path(Path(self.settings.output_folder), source_name)
        shutil.copy2(source, output_path)
        review_path = self._unique_path(self.settings.review_folder_path, source_name)
        shutil.copy2(source, review_path)
        return output_path, review_path

    def _archive_original(self, source: Path) -> Path:
        dated_archive = Path(self.settings.archive_folder) / datetime.now().strftime("%Y-%m-%d")
        destination = self._unique_path(dated_archive, source.name)
        # Nur den Dateiinhalt übernehmen. Damit beginnt die Aufbewahrungsfrist
        # mit der Archivierung und nicht mit dem Zeitstempel des Scanneroriginals.
        shutil.copyfile(source, destination)
        return destination

    @staticmethod
    def _unique_path(folder: Path, filename: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        candidate = folder / filename
        counter = 2
        while candidate.exists():
            candidate = folder / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
            counter += 1
        return candidate

    def cleanup_archive(self) -> int:
        cutoff = datetime.now() - timedelta(days=self.settings.archive_retention_days)
        removed = 0
        archive = Path(self.settings.archive_folder)
        if not archive.exists():
            return removed
        for pdf in archive.rglob("*.pdf"):
            if datetime.fromtimestamp(pdf.stat().st_mtime) < cutoff:
                pdf.unlink()
                removed += 1
        return removed
