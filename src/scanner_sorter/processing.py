from __future__ import annotations

import logging
import shutil
import time
import uuid
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
        operation_id = uuid.uuid4().hex[:10]
        try:
            source_size = source.stat().st_size
        except OSError:
            source_size = -1
        LOGGER.info(
            "Vorgang gestartet; id=%s; datei=%s; groesse_bytes=%s; quelle=%s",
            operation_id,
            source_name,
            source_size,
            source,
        )

        archive_started = time.perf_counter()
        try:
            archive_path = self._archive_original(source)
        except Exception as error:
            duration = time.perf_counter() - started
            LOGGER.exception(
                "Vorgang fehlgeschlagen; id=%s; status=fehler; phase=archivieren; "
                "datei=%s; groesse_bytes=%s; gesamt_s=%.3f",
                operation_id,
                source_name,
                source_size,
                duration,
            )
            return ProcessResult(
                source_name,
                False,
                f"Verarbeitung nicht gestartet: Original konnte nicht archiviert werden ({error}); "
                f"Dauer: {duration:.2f} s.",
            )
        archive_seconds = time.perf_counter() - archive_started

        try:
            source.unlink()
        except Exception as error:
            try:
                archive_path.unlink()
            except Exception:
                LOGGER.exception("Temporäre Archivkopie konnte nicht zurückgerollt werden: %s", archive_path)
            LOGGER.exception(
                "Vorgang fehlgeschlagen; id=%s; status=fehler; phase=eingang_entfernen; "
                "datei=%s; groesse_bytes=%s; archiv_s=%.3f; gesamt_s=%.3f",
                operation_id,
                source_name,
                source_size,
                archive_seconds,
                time.perf_counter() - started,
            )
            duration = time.perf_counter() - started
            return ProcessResult(
                source_name,
                False,
                f"Verarbeitung nicht gestartet: Eingangsdatei konnte nicht entfernt werden ({error}); "
                f"Dauer: {duration:.2f} s.",
            )

        try:
            created, page_count, document_types, recognition_seconds, output_seconds = (
                self._split_and_store(archive_path)
            )
        except ProcessingError as error:
            return self._handle_failed_processing(
                archive_path,
                source_name,
                error,
                started,
                operation_id,
                source_size,
                archive_seconds,
            )
        except Exception as error:
            LOGGER.exception(
                "Vorgangsausnahme; id=%s; phase=erkennung_oder_ausgabe; datei=%s",
                operation_id,
                source_name,
            )
            return self._handle_failed_processing(
                archive_path,
                source_name,
                error,
                started,
                operation_id,
                source_size,
                archive_seconds,
            )

        total_seconds = time.perf_counter() - started
        message = (
            f"{len(created)} Dokument(e) erstellt; Original archiviert: {archive_path.name}; "
            f"Dauer: {total_seconds:.2f} s"
        )
        LOGGER.info(
            "Vorgang abgeschlossen; id=%s; status=erfolgreich; datei=%s; groesse_bytes=%s; "
            "seiten=%s; dokumente=%s; typen=%s; archiv_s=%.3f; erkennung_s=%.3f; "
            "ausgabe_s=%.3f; gesamt_s=%.3f; ausgaben=%s",
            operation_id,
            source_name,
            source_size,
            page_count,
            len(created),
            ",".join(document_types),
            archive_seconds,
            recognition_seconds,
            output_seconds,
            total_seconds,
            ", ".join(path.name for path in created),
        )
        return ProcessResult(source_name, True, message, tuple(str(path) for path in created))

    def _handle_failed_processing(
        self,
        archived_source: Path,
        source_name: str,
        error: Exception,
        started: float,
        operation_id: str,
        source_size: int,
        archive_seconds: float,
    ) -> ProcessResult:
        try:
            forwarded, review_copy = self._forward_archived_original(archived_source, source_name)
        except Exception as forward_error:
            duration = time.perf_counter() - started
            LOGGER.exception(
                "Vorgang fehlgeschlagen; id=%s; status=fehler; phase=fehlerdatei_weiterleiten; "
                "datei=%s; groesse_bytes=%s; archiv_s=%.3f; gesamt_s=%.3f",
                operation_id,
                source_name,
                source_size,
                archive_seconds,
                duration,
            )
            return ProcessResult(
                source_name,
                False,
                f"Verarbeitung fehlgeschlagen ({error}); Original ist im Archiv, "
                f"Weiterleitung fehlgeschlagen ({forward_error}); Dauer: {duration:.2f} s.",
            )
        duration = time.perf_counter() - started
        message = (
            f"Nicht erkannt: Original unverändert weitergeleitet; "
            f"Prüfkopie: {review_copy.name} ({error}); Dauer: {duration:.2f} s."
        )
        LOGGER.warning(
            "Vorgang abgeschlossen; id=%s; status=nicht_erkannt; datei=%s; groesse_bytes=%s; "
            "archiv_s=%.3f; gesamt_s=%.3f; ziel=%s; pruefkopie=%s; grund=%s",
            operation_id,
            source_name,
            source_size,
            archive_seconds,
            duration,
            forwarded,
            review_copy,
            error,
        )
        return ProcessResult(source_name, False, message, (str(forwarded), str(review_copy)))

    def _split_and_store(self, source: Path) -> tuple[list[Path], int, tuple[str, ...], float, float]:
        try:
            import fitz
        except ImportError as error:  # pragma: no cover - dependency check at runtime
            raise ProcessingError("PyMuPDF ist nicht installiert.") from error

        recognition_started = time.perf_counter()
        recognise_document = getattr(self.recognizer, "recognise_document", None)
        if callable(recognise_document):
            detections = recognise_document(source)
        else:
            with fitz.open(source) as scan:
                detections = [self.recognizer.recognise(page) for page in scan]
        groups = group_page_detections(detections)
        recognition_seconds = time.perf_counter() - recognition_started

        output_started = time.perf_counter()
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
        output_seconds = time.perf_counter() - output_started
        document_types = tuple(
            f"{group.detected.document_type}:{len(group.page_indexes)}S" for group in groups
        )
        return created, len(detections), document_types, recognition_seconds, output_seconds

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
