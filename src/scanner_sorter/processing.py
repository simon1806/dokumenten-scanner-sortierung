from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

from pypdf import PdfReader, PdfWriter

from .config import Settings
from .models import DetectedDocument, DocumentGroup, ProcessResult
from .recognition import PageRecognizer

LOGGER = logging.getLogger(__name__)

_CONTROL_FOLDER = ".dokumentensortierer"
_PENDING_FOLDER = "pending"
_ARCHIVE_MARKER = ".dokumentensortierer-archiv-v1"
_ARCHIVE_MARKER_CONTENT = "DokumentenScannerSortierung archive v1\n"
_ARCHIVE_METADATA_SUFFIX = ".dokumentensortierer-archiv.json"
_DATE_FOLDER_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_JOB_SCHEMA_VERSION = 1


class ProcessingError(RuntimeError):
    pass


class PendingJobError(ProcessingError):
    """A recoverable error while staging or publishing a persisted job."""


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

        # Eine Montageinfo ist fachlich immer ein eigenstaendiger, einseitiger
        # Bericht. Auch bei derselben Auftragsnummer darf die naechste erkannte
        # MI daher nicht an die vorherige angehaengt werden.
        if current and current.detected.key == detection.key and detection.document_type != "MI":
            current.page_indexes.append(page_index)
            continue

        current = DocumentGroup(detected=detection, page_indexes=[page_index])
        groups.append(current)

    if not groups:
        raise ProcessingError("Es wurde kein unterstützter Dokumenttyp erkannt.")
    return groups


class DocumentProcessor:
    """Archive, recognise and transactionally publish incoming scan jobs.

    Every accepted input is represented by a small persisted job below the controlled
    archive folder. The original remains in the dated archive while job staging and
    publication can be retried after an application crash or a temporary share outage.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.recognizer = PageRecognizer(settings)

    @property
    def _archive_root(self) -> Path:
        return Path(self.settings.archive_folder).expanduser().resolve(strict=False)

    @property
    def _pending_root(self) -> Path:
        return self._archive_root / _CONTROL_FOLDER / _PENDING_FOLDER

    def process(self, source: Path) -> ProcessResult:
        started = time.perf_counter()
        source = Path(source)
        source_name = source.name
        operation_id = uuid.uuid4().hex[:10]
        try:
            initial_stat = source.stat()
            source_size = initial_stat.st_size
            initial_signature: tuple[int, int] | None = (
                initial_stat.st_size,
                initial_stat.st_mtime_ns,
            )
        except OSError:
            source_size = -1
            initial_signature = None
        LOGGER.info(
            "Vorgang gestartet; id=%s; datei=%s; groesse_bytes=%s; quelle=%s",
            operation_id,
            source_name,
            source_size,
            source,
        )

        # A crash can occur after the job was persisted but before the input was
        # removed. Reuse that job instead of creating a duplicate archive/output.
        existing_job = self._find_pending_job_for_source(source)
        if existing_job is not None:
            try:
                existing = self._load_job(existing_job)
                self._claim_source_for_job(existing_job, existing)
            except (OSError, PendingJobError) as error:
                return self._deferred_result(
                    source_name,
                    operation_id,
                    started,
                    f"Eingangsdatei eines offenen Vorgangs konnte nicht entfernt werden ({error})",
                )
            if existing_job is not None:
                return self._run_pending_job(existing_job, started, operation_id, source_size, 0.0)

        archive_started = time.perf_counter()
        archive_path: Path | None = None
        archived_hash: str | None = None
        archive_owner_token: str | None = None
        job_file: Path | None = None
        try:
            archive_path, archived_hash, archive_owner_token = self._archive_original(source)
            source_identity = self._capture_source_identity(source)
            if initial_signature is not None and (
                source_identity["size"], source_identity["mtime_ns"]
            ) != initial_signature:
                raise ProcessingError("Eingangsdatei wurde während der Archivierung verändert.")
            if source_identity["sha256"] != archived_hash:
                raise ProcessingError("Archivkopie stimmt nicht mit der Eingangsdatei überein.")
            job_file = self._create_pending_job(
                operation_id=operation_id,
                source=source,
                source_name=source_name,
                source_size=source_size,
                source_identity=source_identity,
                archive_path=archive_path,
            )
        except Exception as error:
            if job_file is not None:
                self._remove_job_folder(job_file.parent)
            if archive_path is not None:
                removed = self._remove_archived_original(
                    archive_path,
                    expected_sha256=archived_hash,
                    expected_owner_token=archive_owner_token,
                )
                if not removed:
                    LOGGER.warning(
                        "Archivkopie wurde nach einem Fehler nicht entfernt, weil ihr "
                        "Eigentumsnachweis nicht mehr eindeutig war: %s",
                        archive_path,
                    )
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
                f"Verarbeitung nicht gestartet: Original konnte nicht sicher archiviert werden "
                f"({error}); Dauer: {duration:.2f} s.",
            )
        archive_seconds = time.perf_counter() - archive_started

        try:
            job = self._load_job(job_file)
            self._claim_source_for_job(job_file, job)
        except Exception as error:
            LOGGER.exception(
                "Vorgang zurückgestellt; id=%s; status=offen; phase=eingang_claim; "
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
                f"Verarbeitung zurückgestellt; Original ist sicher archiviert und die Eingangsdatei "
                f"wird erneut atomar übernommen ({error}); Dauer: {duration:.2f} s.",
            )

        return self._run_pending_job(
            job_file,
            started,
            operation_id,
            source_size,
            archive_seconds,
        )

    def recover_incomplete_jobs(
        self,
        should_stop: Callable[[], bool] | None = None,
    ) -> list[ProcessResult]:
        """Retry all valid persisted jobs and return one result for every attempt.

        Jobs remain in place when a destination is temporarily unavailable. A corrupt
        or tampered manifest is reported but never used to move or delete files.
        ``should_stop`` is checked before each job; an already running job always
        finishes, while no subsequent job is started after a stop request.
        """
        try:
            self._assert_safe_archive_root()
        except ProcessingError as error:
            LOGGER.error("Offene Vorgänge können nicht geprüft werden: %s", error)
            return []
        if not self._pending_root.exists():
            return []

        should_stop = should_stop or (lambda: False)
        results: list[ProcessResult] = []
        for job_folder in sorted(path for path in self._pending_root.iterdir() if path.is_dir()):
            # A recovery that is already running may finish safely.  Re-check
            # immediately before every subsequent job so shutdown never starts
            # another pending operation from the same batch.
            if should_stop():
                LOGGER.info("Wiederherstellung offener Vorgänge wegen Stop-Anforderung unterbrochen.")
                break
            job_file = job_folder / "job.json"
            if not job_file.is_file():
                message = f"Offener Vorgang ist beschädigt: {job_folder.name} (job.json fehlt)."
                LOGGER.error(message)
                results.append(ProcessResult(job_folder.name, False, message))
                continue
            started = time.perf_counter()
            try:
                job = self._load_job(job_file)
                source_size = int(job.get("source_size", -1))
                operation_id = str(job["job_id"])
                self._claim_source_for_job(job_file, job)
                result = self._run_pending_job(
                    job_file,
                    started,
                    operation_id,
                    source_size,
                    0.0,
                )
            except Exception as error:
                LOGGER.exception("Offener Vorgang konnte nicht geladen werden: %s", job_file)
                result = ProcessResult(
                    job_folder.name,
                    False,
                    f"Offener Vorgang ist beschädigt oder unsicher ({error}); Dateien bleiben erhalten.",
                )
            results.append(result)
        return results

    def _run_pending_job(
        self,
        job_file: Path,
        started: float,
        operation_id: str,
        source_size: int,
        archive_seconds: float,
    ) -> ProcessResult:
        try:
            job = self._load_job(job_file)
            if job["status"] == "archived":
                job = self._prepare_job(job_file, job)
            created = self._publish_job(job_file, job)
        except PendingJobError as error:
            source_name = self._safe_job_source_name(job_file)
            return self._deferred_result(source_name, operation_id, started, str(error))
        except Exception as error:
            source_name = self._safe_job_source_name(job_file)
            LOGGER.exception(
                "Vorgangsausnahme; id=%s; phase=pending; datei=%s",
                operation_id,
                source_name,
            )
            return self._deferred_result(source_name, operation_id, started, str(error))

        total_seconds = time.perf_counter() - started
        source_name = str(job["source_name"])
        archive_path = Path(str(job["archive_path"]))
        recognition_seconds = float(job.get("recognition_seconds", 0.0))
        output_seconds = float(job.get("output_seconds", 0.0))
        outcome = str(job["outcome"])
        page_count = int(job.get("page_count", 0))
        document_types = tuple(str(value) for value in job.get("document_types", []))
        reason = str(job.get("reason", ""))

        self._remove_job_folder(job_file.parent)
        if outcome == "not_recognized":
            message = (
                "Nicht erkannt: Original unverändert weitergeleitet; "
                f"Prüfkopie: {created[-1].name} ({reason}); Dauer: {total_seconds:.2f} s."
            )
            LOGGER.warning(
                "Vorgang abgeschlossen; id=%s; status=nicht_erkannt; datei=%s; groesse_bytes=%s; "
                "archiv_s=%.3f; erkennung_s=%.3f; ausgabe_s=%.3f; gesamt_s=%.3f; "
                "ziel=%s; pruefkopie=%s; grund=%s",
                operation_id,
                source_name,
                source_size,
                archive_seconds,
                recognition_seconds,
                output_seconds,
                total_seconds,
                created[0],
                created[-1],
                reason,
            )
            return ProcessResult(source_name, False, message, tuple(str(path) for path in created))

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

    def _prepare_job(self, job_file: Path, job: dict[str, Any]) -> dict[str, Any]:
        staging = job_file.parent / "staging"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        archive_path = Path(str(job["archive_path"]))

        try:
            groups, page_count, recognition_seconds = self._recognise_groups(archive_path)
        except Exception as recognition_error:
            return self._prepare_original_forwarding(
                job_file,
                job,
                staging,
                archive_path,
                reason=str(recognition_error),
            )

        output_started = time.perf_counter()
        try:
            reader = PdfReader(archive_path)
            plan: list[dict[str, str]] = []
            reserved: set[Path] = set()
            for index, group in enumerate(groups):
                stage = staging / f"document-{index + 1}.pdf"
                writer = PdfWriter()
                for page_index in group.page_indexes:
                    writer.add_page(reader.pages[page_index])
                with stage.open("wb") as stream:
                    writer.write(stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                destination = self._available_destination(
                    Path(self.settings.output_folder),
                    group.detected.filename,
                    reserved,
                )
                reserved.add(destination)
                plan.append(self._plan_item(job_file.parent, stage, destination))
        except Exception as error:
            return self._prepare_original_forwarding(
                job_file,
                job,
                staging,
                archive_path,
                reason=f"Erkanntes Dokument konnte nicht sicher getrennt werden ({error})",
                page_count=page_count,
                recognition_seconds=recognition_seconds,
                output_seconds=time.perf_counter() - output_started,
            )

        job.update(
            {
                "status": "ready",
                "outcome": "recognized",
                "reason": "",
                "page_count": page_count,
                "document_types": [
                    f"{group.detected.document_type}:{len(group.page_indexes)}S" for group in groups
                ],
                "recognition_seconds": recognition_seconds,
                "output_seconds": time.perf_counter() - output_started,
                "plan": plan,
            }
        )
        try:
            self._write_job(job_file, job)
        except Exception as error:
            raise PendingJobError(f"Vorgang konnte nicht persistent gespeichert werden ({error})") from error
        return job

    def _prepare_original_forwarding(
        self,
        job_file: Path,
        job: dict[str, Any],
        staging: Path,
        archive_path: Path,
        *,
        reason: str,
        page_count: int = 0,
        recognition_seconds: float = 0.0,
        output_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Stage the unchanged original for target and review after a permanent PDF error."""
        try:
            if staging.exists():
                shutil.rmtree(staging)
            staging.mkdir(parents=True)
            stage = staging / "original.pdf"
            self._copy_file_durable(archive_path, stage)
            output = self._available_destination(
                Path(self.settings.output_folder),
                str(job["source_name"]),
            )
            review = self._available_destination(
                self.settings.review_folder_path,
                str(job["source_name"]),
            )
            job.update(
                {
                    "status": "ready",
                    "outcome": "not_recognized",
                    "reason": reason,
                    "page_count": page_count,
                    "document_types": [],
                    "recognition_seconds": recognition_seconds,
                    "output_seconds": output_seconds,
                    "plan": [
                        self._plan_item(job_file.parent, stage, output),
                        self._plan_item(job_file.parent, stage, review),
                    ],
                }
            )
            self._write_job(job_file, job)
            return job
        except Exception as error:
            raise PendingJobError(
                f"Fehlerdatei konnte nicht für die spätere Weiterleitung vorbereitet werden ({error})"
            ) from error

    def _recognise_groups(
        self,
        source: Path,
    ) -> tuple[list[DocumentGroup], int, float]:
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
        return groups, len(detections), time.perf_counter() - recognition_started

    def _publish_job(self, job_file: Path, job: dict[str, Any]) -> list[Path]:
        self._validate_job(job_file, job, require_plan=True)
        plan = job["plan"]
        job_id = str(job["job_id"])
        staged_and_destinations: list[tuple[Path, Path, str]] = []

        # A destination may have appeared while the job was waiting. Only an exact
        # hash match can be a publication from this job; all other files are preserved
        # and the job receives a new unique destination.
        changed = False
        reserved = {Path(str(item["destination"])) for item in plan}
        for item in plan:
            stage = self._stage_path(job_file.parent, str(item["stage"]))
            destination = Path(str(item["destination"]))
            expected_hash = str(item["sha256"])
            if destination.exists() and not self._file_matches(destination, expected_hash):
                reserved.discard(destination)
                destination = self._available_destination(destination.parent, destination.name, reserved)
                item["destination"] = str(destination)
                reserved.add(destination)
                changed = True
            staged_and_destinations.append((stage, destination, expected_hash))
        if changed:
            self._write_job(job_file, job)

        temporary_files: list[Path] = []
        published_this_attempt: list[tuple[Path, str]] = []
        publication_started = time.perf_counter()
        try:
            # Prepare every target-side temporary copy first. Until this phase has
            # completed, no final document is visible in any destination folder.
            for index, (stage, destination, expected_hash) in enumerate(staged_and_destinations):
                destination.parent.mkdir(parents=True, exist_ok=True)
                temporary = destination.parent / f".{destination.name}.{job_id}.{index}.tmp"
                temporary.unlink(missing_ok=True)
                if destination.exists() and self._file_matches(destination, expected_hash):
                    continue
                temporary_files.append(temporary)
                shutil.copy2(stage, temporary)
                self._fsync_file(temporary)
                if not self._file_matches(temporary, expected_hash):
                    raise OSError(f"Prüfsumme der temporären Ausgabe stimmt nicht: {temporary}")

            for index, (_stage, destination, expected_hash) in enumerate(staged_and_destinations):
                if destination.exists() and self._file_matches(destination, expected_hash):
                    continue
                temporary = destination.parent / f".{destination.name}.{job_id}.{index}.tmp"
                if destination.exists():
                    raise FileExistsError(f"Zieldatei ist inzwischen vorhanden: {destination}")
                self._publish_no_clobber(temporary, destination)
                published_this_attempt.append((destination, expected_hash))

            if not all(
                destination.exists() and self._file_matches(destination, expected_hash)
                for _stage, destination, expected_hash in staged_and_destinations
            ):
                raise OSError("Nicht alle Ausgabedateien konnten nach der Veröffentlichung geprüft werden.")
        except Exception as error:
            rollback_errors: list[str] = []
            for temporary in temporary_files:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError as cleanup_error:
                    rollback_errors.append(str(cleanup_error))
            # Roll back only files atomically published by this invocation. An exact
            # file that existed before the attempt may be a recovered publication or
            # an unrelated user file and must never be removed here.
            for destination, expected_hash in published_this_attempt:
                try:
                    if destination.exists() and self._file_matches(destination, expected_hash):
                        destination.unlink()
                except OSError as cleanup_error:
                    rollback_errors.append(str(cleanup_error))
            detail = f"; Rollbackfehler: {' | '.join(rollback_errors)}" if rollback_errors else ""
            raise PendingJobError(
                f"Ziel- oder Prüfordner vorübergehend nicht beschreibbar ({error}){detail}"
            ) from error

        job["output_seconds"] = float(job.get("output_seconds", 0.0)) + (
            time.perf_counter() - publication_started
        )
        return [destination for _stage, destination, _hash in staged_and_destinations]

    def _create_pending_job(
        self,
        *,
        operation_id: str,
        source: Path,
        source_name: str,
        source_size: int,
        source_identity: dict[str, int | str],
        archive_path: Path,
    ) -> Path:
        self._pending_root.mkdir(parents=True, exist_ok=True)
        job_id = f"{datetime.now():%Y%m%d%H%M%S}-{operation_id}-{uuid.uuid4().hex[:8]}"
        job_folder = self._pending_root / job_id
        job_folder.mkdir()
        job_file = job_folder / "job.json"
        job: dict[str, Any] = {
            "schema_version": _JOB_SCHEMA_VERSION,
            "job_id": job_id,
            "source_name": source_name,
            "source_path": str(source.resolve(strict=False)),
            "source_claim": str(
                (Path(self.settings.input_folder).expanduser().resolve(strict=False) / f".{job_id}.claim")
            ),
            "source_size": source_size,
            "source_identity": source_identity,
            "archive_path": str(archive_path.resolve(strict=False)),
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "status": "archived",
            "outcome": "",
            "reason": "",
            "page_count": 0,
            "document_types": [],
            "recognition_seconds": 0.0,
            "output_seconds": 0.0,
            "plan": [],
        }
        try:
            self._write_job(job_file, job)
        except Exception:
            self._remove_job_folder(job_folder)
            raise
        return job_file

    def _load_job(self, job_file: Path) -> dict[str, Any]:
        try:
            raw = json.loads(job_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise PendingJobError(f"Jobdatei ist nicht lesbar: {job_file}") from error
        if not isinstance(raw, dict):
            raise PendingJobError(f"Jobdatei hat ein ungültiges Format: {job_file}")
        self._validate_job(job_file, raw, require_plan=raw.get("status") == "ready")
        return raw

    def _validate_job(self, job_file: Path, job: dict[str, Any], *, require_plan: bool) -> None:
        required = {
            "schema_version",
            "job_id",
            "source_name",
            "source_path",
            "source_claim",
            "source_identity",
            "archive_path",
            "status",
            "plan",
        }
        if not required.issubset(job):
            raise PendingJobError("Jobdatei ist unvollständig.")
        if job["schema_version"] != _JOB_SCHEMA_VERSION:
            raise PendingJobError("Jobdatei verwendet eine nicht unterstützte Version.")
        if str(job["job_id"]) != job_file.parent.name:
            raise PendingJobError("Jobkennung und Jobordner stimmen nicht überein.")
        if Path(str(job["source_name"])).name != str(job["source_name"]):
            raise PendingJobError("Unsicherer Quelldateiname in der Jobdatei.")
        if job["status"] not in {"archived", "ready"}:
            raise PendingJobError("Unbekannter Jobstatus.")

        pending_root = self._pending_root.resolve(strict=False)
        if job_file.parent.resolve(strict=False).parent != pending_root:
            raise PendingJobError("Job liegt außerhalb des kontrollierten Pending-Ordners.")

        archive_path = Path(str(job["archive_path"])).resolve(strict=False)
        archive_parent = archive_path.parent
        if (
            archive_parent.parent != self._archive_root
            or not _DATE_FOLDER_PATTERN.fullmatch(archive_parent.name)
            or not self._is_owned_archive_folder(archive_parent)
        ):
            raise PendingJobError("Archivdatei liegt außerhalb eines eigenen datierten Archivordners.")
        if not archive_path.is_file():
            raise PendingJobError(f"Archiviertes Original fehlt: {archive_path}")
        owned_archive = self._owned_archive_identity(archive_path)
        if owned_archive is None:
            raise PendingJobError(f"Eigentumsnachweis der Archivdatei fehlt oder ist ungültig: {archive_path}")

        source_path = Path(str(job["source_path"])).expanduser().resolve(strict=False)
        input_root = Path(self.settings.input_folder).expanduser().resolve(strict=False)
        if source_path.parent != input_root or source_path.name != str(job["source_name"]):
            raise PendingJobError("Unsicherer Quellpfad in der Jobdatei.")
        source_claim = Path(str(job["source_claim"])).expanduser().resolve(strict=False)
        if (
            source_claim.parent != input_root
            or source_claim.name != f".{job['job_id']}.claim"
        ):
            raise PendingJobError("Unsicherer Claim-Pfad in der Jobdatei.")
        identity = job["source_identity"]
        if (
            not isinstance(identity, dict)
            or not isinstance(identity.get("size"), int)
            or isinstance(identity.get("size"), bool)
            or identity["size"] < 0
            or not isinstance(identity.get("mtime_ns"), int)
            or isinstance(identity.get("mtime_ns"), bool)
            or not isinstance(identity.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", identity["sha256"]) is None
        ):
            raise PendingJobError("Ungültige Identität der Eingangsdatei in der Jobdatei.")
        _archive_metadata, archive_identity = owned_archive
        if (
            identity["size"] != archive_identity["size"]
            or identity["sha256"] != archive_identity["sha256"]
        ):
            raise PendingJobError(
                "Identität der Eingangsdatei stimmt nicht mit dem sicher archivierten Original überein."
            )

        if not isinstance(job["plan"], list):
            raise PendingJobError("Ungültiger Ausgabeplan.")
        if require_plan and not job["plan"]:
            raise PendingJobError("Ausgabeplan fehlt.")
        allowed_parents = {
            Path(self.settings.output_folder).expanduser().resolve(strict=False),
            self.settings.review_folder_path.expanduser().resolve(strict=False),
        }
        for item in job["plan"]:
            if not isinstance(item, dict) or not {"stage", "destination", "sha256"}.issubset(item):
                raise PendingJobError("Ungültiger Eintrag im Ausgabeplan.")
            stage = self._stage_path(job_file.parent, str(item["stage"]))
            if not stage.is_file():
                raise PendingJobError(f"Staging-Datei fehlt: {stage}")
            if self._sha256(stage) != str(item["sha256"]):
                raise PendingJobError(f"Staging-Datei wurde verändert: {stage}")
            destination = Path(str(item["destination"])).expanduser().resolve(strict=False)
            if destination.parent not in allowed_parents or destination.suffix.lower() != ".pdf":
                raise PendingJobError("Unsicheres Ziel im Ausgabeplan.")

    def _find_pending_job_for_source(self, source: Path) -> Path | None:
        if not self._pending_root.exists():
            return None
        expected = source.resolve(strict=False)
        for job_file in self._pending_root.glob("*/job.json"):
            try:
                job = self._load_job(job_file)
                if (
                    Path(str(job.get("source_path", ""))).resolve(strict=False) == expected
                    and self._source_matches_job(source, job)
                ):
                    return job_file
            except Exception:
                continue
        return None

    def _write_job(self, job_file: Path, job: dict[str, Any]) -> None:
        job_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = job_file.with_name(f".{job_file.name}.{uuid.uuid4().hex}.tmp")
        payload = json.dumps(job, ensure_ascii=False, indent=2) + "\n"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, job_file)
        finally:
            temporary.unlink(missing_ok=True)

    def _archive_original(self, source: Path) -> tuple[Path, str, str]:
        archive_root = self._assert_safe_archive_root()
        dated_archive = archive_root / datetime.now().strftime("%Y-%m-%d")
        dated_archive.mkdir(parents=True, exist_ok=True)
        if dated_archive.resolve(strict=False).parent != archive_root:
            raise ProcessingError("Der datierte Archivordner verlässt den konfigurierten Archivbereich.")
        marker = dated_archive / _ARCHIVE_MARKER
        if marker.exists():
            if marker.read_text(encoding="utf-8") != _ARCHIVE_MARKER_CONTENT:
                raise ProcessingError(f"Archivmarker ist ungültig: {dated_archive}")
        else:
            temporary_marker = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.tmp")
            temporary_marker_created = False
            try:
                with temporary_marker.open("x", encoding="utf-8", newline="\n") as stream:
                    temporary_marker_created = True
                    stream.write(_ARCHIVE_MARKER_CONTENT)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    self._publish_no_clobber(temporary_marker, marker)
                    temporary_marker_created = False
                except FileExistsError:
                    if marker.read_text(encoding="utf-8") != _ARCHIVE_MARKER_CONTENT:
                        raise ProcessingError(f"Archivmarker ist ungültig: {dated_archive}")
            finally:
                if temporary_marker_created:
                    temporary_marker.unlink(missing_ok=True)
        # Copy to a private, unpredictable file first. Final PDF and metadata names are
        # then published without replacement, so a file winning either name race is
        # never overwritten or removed by this operation.
        temporary = dated_archive / f".{source.name}.{uuid.uuid4().hex}.archive.tmp"
        temporary_created = False
        try:
            # Only copy file contents so retention starts at the archive time, not at
            # the timestamp supplied by the scanner.
            archived_hash = self._copy_file_durable(source, temporary)
            temporary_created = True
            archived_size = temporary.stat().st_size
            reserved: set[Path] = set()
            while True:
                destination = self._available_destination(
                    dated_archive,
                    source.name,
                    reserved,
                )
                metadata = self._archive_metadata_path(destination)
                if metadata.exists():
                    reserved.add(destination)
                    continue

                owner_token = uuid.uuid4().hex
                try:
                    self._write_archive_metadata(
                        destination,
                        archived_hash,
                        archived_size,
                        owner_token,
                    )
                except FileExistsError:
                    reserved.add(destination)
                    continue

                try:
                    self._publish_no_clobber(temporary, destination)
                except FileExistsError:
                    self._remove_archive_metadata_if_owned(metadata, owner_token)
                    reserved.add(destination)
                    continue
                except Exception:
                    self._remove_archive_metadata_if_owned(metadata, owner_token)
                    raise

                temporary_created = False
                return destination, archived_hash, owner_token
        finally:
            if temporary_created:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    LOGGER.exception("Eigene temporäre Archivdatei konnte nicht entfernt werden: %s", temporary)

    def cleanup_archive(self) -> int:
        """Delete old PDFs only from directly owned, dated archive directories."""
        cutoff = datetime.now() - timedelta(days=self.settings.archive_retention_days)
        removed = 0
        try:
            archive = self._assert_safe_archive_root()
        except ProcessingError as error:
            LOGGER.error("Archivbereinigung aus Sicherheitsgründen abgebrochen: %s", error)
            return 0
        if not archive.exists():
            return 0
        try:
            protected = self._pending_archive_paths()
        except ProcessingError as error:
            LOGGER.error("Archivbereinigung wegen unbekanntem Pending-Zustand abgebrochen: %s", error)
            return 0

        for dated_archive in archive.iterdir():
            if (
                not dated_archive.is_dir()
                or not _DATE_FOLDER_PATTERN.fullmatch(dated_archive.name)
                or dated_archive.resolve(strict=False).parent != archive
                or not self._is_owned_archive_folder(dated_archive)
            ):
                continue
            for pdf in dated_archive.glob("*.pdf"):
                try:
                    if pdf.is_symlink() or pdf.resolve(strict=False).parent != dated_archive.resolve():
                        continue
                    if pdf.resolve(strict=False) in protected:
                        continue
                    owned_archive = self._owned_archive_identity(pdf)
                    if owned_archive is None:
                        continue
                    metadata, identity = owned_archive
                    if datetime.fromtimestamp(int(identity["mtime_ns"]) / 1_000_000_000) >= cutoff:
                        continue
                    if self._remove_archived_original(
                        pdf,
                        expected_sha256=str(identity["sha256"]),
                        expected_size=int(identity["size"]),
                        expected_mtime_ns=int(identity["mtime_ns"]),
                        expected_owner_token=(
                            str(metadata["owner_token"])
                            if isinstance(metadata.get("owner_token"), str)
                            else None
                        ),
                    ):
                        removed += 1
                except OSError:
                    LOGGER.exception("Archivdatei konnte nicht geprüft oder gelöscht werden: %s", pdf)
        return removed

    def _pending_archive_paths(self) -> set[Path]:
        """Return archive originals referenced by any readable pending manifest.

        A manifest need not be executable to protect its archive. This intentionally
        fails closed: cleanup must never make later manual repair or recovery impossible.
        """
        protected: set[Path] = set()
        if not self._pending_root.exists():
            return protected
        archive_root = self._archive_root
        try:
            entries = list(self._pending_root.iterdir())
        except OSError as error:
            raise ProcessingError(f"Pending-Ordner ist nicht lesbar ({error}).") from error
        for entry in entries:
            if not entry.is_dir():
                raise ProcessingError(f"Unbekannter Eintrag im Pending-Ordner: {entry.name}")
            job_file = entry / "job.json"
            if not job_file.is_file():
                raise ProcessingError(f"Pending-Vorgang ohne lesbare job.json: {entry.name}")
            try:
                job = self._load_job(job_file)
                archive_path = Path(str(job["archive_path"])).expanduser().resolve(strict=False)
            except Exception as error:
                raise ProcessingError(
                    f"Pending-Vorgang ist unbekannt oder unlesbar: {entry.name}"
                ) from error
            if (
                archive_path.parent.parent != archive_root
                or _DATE_FOLDER_PATTERN.fullmatch(archive_path.parent.name) is None
            ):
                raise ProcessingError(f"Unsicherer Archivverweis im Pending-Vorgang: {entry.name}")
            protected.add(archive_path)
        return protected

    def _assert_safe_archive_root(self) -> Path:
        if not self.settings.archive_folder.strip():
            raise ProcessingError("Archivordner ist nicht festgelegt.")
        archive = self._archive_root
        if archive == Path(archive.anchor):
            raise ProcessingError("Laufwerks- oder Dateisystemstamm darf nicht als Archiv verwendet werden.")
        configured = [
            Path(self.settings.input_folder).expanduser().resolve(strict=False),
            Path(self.settings.output_folder).expanduser().resolve(strict=False),
            self.settings.review_folder_path.expanduser().resolve(strict=False),
        ]
        for other in configured:
            if self._paths_overlap(archive, other):
                raise ProcessingError("Archivordner darf nicht in einem anderen Arbeitsordner liegen.")
        return archive

    @staticmethod
    def _paths_overlap(first: Path, second: Path) -> bool:
        first_key = os.path.normcase(str(first))
        second_key = os.path.normcase(str(second))
        if first_key == second_key:
            return True
        try:
            common = os.path.normcase(os.path.commonpath((first_key, second_key)))
        except ValueError:
            return False
        return common in {first_key, second_key}

    @staticmethod
    def _available_destination(
        folder: Path,
        filename: str,
        reserved: set[Path] | None = None,
    ) -> Path:
        folder = Path(folder).expanduser().resolve(strict=False)
        reserved = reserved or set()
        candidate = folder / Path(filename).name
        counter = 2
        while candidate.exists() or candidate in reserved:
            candidate = folder / f"{Path(filename).stem}_{counter}{Path(filename).suffix}"
            counter += 1
        return candidate

    @staticmethod
    def _plan_item(job_folder: Path, stage: Path, destination: Path) -> dict[str, str]:
        return {
            "stage": stage.relative_to(job_folder).as_posix(),
            "destination": str(destination),
            "sha256": DocumentProcessor._sha256(stage),
        }

    @staticmethod
    def _stage_path(job_folder: Path, relative: str) -> Path:
        stage = (job_folder / Path(relative)).resolve(strict=False)
        staging_root = (job_folder / "staging").resolve(strict=False)
        if stage.parent != staging_root:
            raise PendingJobError("Staging-Pfad verlässt den kontrollierten Jobordner.")
        return stage

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _capture_source_identity(cls, path: Path) -> dict[str, int | str]:
        before = path.stat()
        digest = cls._sha256(path)
        after = path.stat()
        before_signature = (before.st_size, before.st_mtime_ns)
        after_signature = (after.st_size, after.st_mtime_ns)
        if before_signature != after_signature:
            raise ProcessingError(f"Datei wurde während der Identitätsprüfung verändert: {path}")
        return {
            "size": after.st_size,
            "mtime_ns": after.st_mtime_ns,
            "sha256": digest,
        }

    @classmethod
    def _source_matches_job(cls, source: Path, job: dict[str, Any]) -> bool:
        expected = job.get("source_identity")
        if not isinstance(expected, dict):
            return False
        try:
            actual = cls._capture_source_identity(source)
        except (OSError, ProcessingError):
            return False
        return actual == expected

    def _claim_source_for_job(self, job_file: Path, job: dict[str, Any]) -> None:
        """Atomically detach the expected input before it can be deleted.

        The rename/link happens before identity verification. Therefore a replacement
        arriving in the former check-to-unlink window is captured but never deleted:
        a mismatching claim is restored under the original or a unique PDF name.
        """
        self._validate_job(job_file, job, require_plan=job.get("status") == "ready")
        source = Path(str(job["source_path"]))
        claim = Path(str(job["source_claim"]))

        if not claim.exists() and source.exists():
            try:
                self._move_no_clobber(source, claim)
            except FileNotFoundError:
                pass
            except OSError as error:
                raise PendingJobError(
                    f"Eingangsdatei konnte nicht atomar beansprucht werden ({error})"
                ) from error

        if not claim.exists():
            return

        if self._source_matches_job(claim, job):
            try:
                # POSIX hard-link claiming can be interrupted between link and source
                # unlink. Remove that second name only when both paths are the same inode.
                if source.exists() and self._same_file(source, claim):
                    source.unlink()
                claim.unlink()
            except OSError as error:
                raise PendingJobError(
                    f"Beanspruchte Eingangsdatei konnte nicht entfernt werden ({error})"
                ) from error
            return

        # The path was replaced after the job was created. Restore that newer file;
        # if another scan already occupies the original name, choose a unique PDF name.
        try:
            if source.exists() and self._same_file(source, claim):
                claim.unlink()
                restored = source
            else:
                restored = source if not source.exists() else self._available_destination(
                    source.parent,
                    source.name,
                )
                self._move_no_clobber(claim, restored)
        except OSError as error:
            raise PendingJobError(
                f"Neu eingetroffene Datei konnte aus dem Claim nicht sicher zurückgestellt werden ({error})"
            ) from error
        LOGGER.info(
            "Neu eingetroffene Datei nach Claim-Identitätsprüfung erhalten; alter_job=%s; datei=%s",
            job["job_id"],
            restored,
        )

    @staticmethod
    def _move_no_clobber(source: Path, destination: Path) -> None:
        """Move within one folder without ever replacing an existing name."""
        if os.name != "nt":
            # This application targets Windows Server. A portable link+unlink sequence
            # would expose two names and is not an atomic move, so fail before touching
            # the source on unsupported platforms.
            raise OSError("Atomarer No-Clobber-Claim wird nur unter Windows unterstützt.")
        # MoveFileW, used by os.rename on Windows, fails when destination exists.
        os.rename(source, destination)

    @staticmethod
    def _publish_no_clobber(temporary: Path, destination: Path) -> None:
        """Atomically publish a complete target-side temp without replacement."""
        if os.name == "nt":
            # Contrary to os.replace, os.rename is a no-clobber operation on Windows.
            os.rename(temporary, destination)
            return
        # link(2) atomically fails with EEXIST and never overwrites. Both paths are in
        # the same destination directory, so no cross-device move is involved.
        os.link(temporary, destination)
        try:
            temporary.unlink()
        except OSError:
            destination.unlink(missing_ok=True)
            raise

    @staticmethod
    def _same_file(first: Path, second: Path) -> bool:
        try:
            return os.path.samefile(first, second)
        except OSError:
            return False

    @staticmethod
    def _copy_file_durable(source: Path, destination: Path) -> str:
        digest = hashlib.sha256()
        destination_created = False
        try:
            with Path(source).open("rb") as source_stream, Path(destination).open("xb") as target_stream:
                destination_created = True
                for chunk in iter(lambda: source_stream.read(1024 * 1024), b""):
                    target_stream.write(chunk)
                    digest.update(chunk)
                target_stream.flush()
                os.fsync(target_stream.fileno())
        except Exception:
            if destination_created:
                try:
                    Path(destination).unlink(missing_ok=True)
                except OSError:
                    LOGGER.exception(
                        "Eigene unvollständige Kopie konnte nicht entfernt werden: %s",
                        destination,
                    )
            raise
        return digest.hexdigest()

    @staticmethod
    def _fsync_file(path: Path) -> None:
        with path.open("r+b") as stream:
            os.fsync(stream.fileno())

    @classmethod
    def _file_matches(cls, path: Path, expected_hash: str) -> bool:
        try:
            return path.is_file() and cls._sha256(path) == expected_hash
        except OSError:
            return False

    @staticmethod
    def _remove_job_folder(job_folder: Path) -> None:
        try:
            shutil.rmtree(job_folder)
        except FileNotFoundError:
            pass
        except OSError:
            LOGGER.exception("Abgeschlossener Pending-Ordner konnte nicht entfernt werden: %s", job_folder)

    @staticmethod
    def _safe_job_source_name(job_file: Path) -> str:
        try:
            raw = json.loads(job_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return str(raw.get("source_name") or job_file.parent.name)
        except Exception:
            pass
        return job_file.parent.name

    @staticmethod
    def _deferred_result(
        source_name: str,
        operation_id: str,
        started: float,
        reason: str,
    ) -> ProcessResult:
        duration = time.perf_counter() - started
        LOGGER.error(
            "Vorgang zurückgestellt; id=%s; status=offen; datei=%s; gesamt_s=%.3f; grund=%s",
            operation_id,
            source_name,
            duration,
            reason,
        )
        return ProcessResult(
            source_name,
            False,
            f"Verarbeitung zurückgestellt; Original ist sicher archiviert und wird automatisch erneut "
            f"verarbeitet ({reason}); Dauer: {duration:.2f} s.",
        )

    @staticmethod
    def _is_owned_archive_folder(folder: Path) -> bool:
        marker = folder / _ARCHIVE_MARKER
        try:
            return marker.is_file() and marker.read_text(encoding="utf-8") == _ARCHIVE_MARKER_CONTENT
        except (OSError, UnicodeError):
            return False

    @staticmethod
    def _archive_metadata_path(archive_file: Path) -> Path:
        return archive_file.with_name(f".{archive_file.name}{_ARCHIVE_METADATA_SUFFIX}")

    def _write_archive_metadata(
        self,
        archive_file: Path,
        archive_hash: str,
        archive_size: int,
        owner_token: str,
    ) -> None:
        metadata = self._archive_metadata_path(archive_file)
        temporary = metadata.with_name(f".{metadata.name}.{uuid.uuid4().hex}.tmp")
        payload = {
            "schema_version": _JOB_SCHEMA_VERSION,
            "filename": archive_file.name,
            "size": archive_size,
            "sha256": archive_hash,
            "owner_token": owner_token,
        }
        temporary_created = False
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as stream:
                temporary_created = True
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._publish_no_clobber(temporary, metadata)
            temporary_created = False
        finally:
            if temporary_created:
                temporary.unlink(missing_ok=True)

    def _remove_archive_metadata_if_owned(self, metadata: Path, owner_token: str) -> bool:
        """Claim and remove only the metadata reservation carrying our token.

        Moving to an unpredictable no-clobber claim closes the public check-to-unlink
        window. A replacement at the original path is never read, removed or replaced.
        """
        claim = metadata.with_name(f".{metadata.name}.{uuid.uuid4().hex}.metadata-delete-claim")
        try:
            self._publish_no_clobber(metadata, claim)
        except FileNotFoundError:
            return True
        except OSError:
            LOGGER.exception("Archivreservierung konnte nicht atomar beansprucht werden: %s", metadata)
            return False

        try:
            raw = json.loads(claim.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("owner_token") != owner_token:
                self._restore_archive_claim(claim, metadata, True)
                return False

            # A second read immediately before unlink also rejects a modification of
            # the private claim after its first validation.
            final = json.loads(claim.read_text(encoding="utf-8"))
            if final != raw or not isinstance(final, dict) or final.get("owner_token") != owner_token:
                self._restore_archive_claim(claim, metadata, True)
                return False
            claim.unlink()
            return True
        except (OSError, UnicodeError, json.JSONDecodeError):
            LOGGER.exception("Beanspruchte Archivreservierung konnte nicht sicher entfernt werden: %s", claim)
            self._restore_archive_claim(claim, metadata, True)
            return False

    def _owned_archive_identity(
        self,
        archive_file: Path,
    ) -> tuple[dict[str, Any], dict[str, int | str]] | None:
        return self._archive_pair_identity(
            archive_file,
            self._archive_metadata_path(archive_file),
            archive_file.name,
        )

    def _archive_pair_identity(
        self,
        archive_file: Path,
        metadata: Path,
        expected_filename: str,
    ) -> tuple[dict[str, Any], dict[str, int | str]] | None:
        try:
            raw = json.loads(metadata.read_text(encoding="utf-8"))
            identity = self._capture_source_identity(archive_file)
            metadata_size = raw.get("size") if isinstance(raw, dict) else None
            if (
                not isinstance(raw, dict)
                or raw.get("schema_version") != _JOB_SCHEMA_VERSION
                or raw.get("filename") != expected_filename
                or raw.get("sha256") != identity["sha256"]
                or (
                    metadata_size is not None
                    and (
                        not isinstance(metadata_size, int)
                        or isinstance(metadata_size, bool)
                        or metadata_size != identity["size"]
                    )
                )
            ):
                return None
            return raw, identity
        except (OSError, UnicodeError, json.JSONDecodeError, ProcessingError):
            return None

    def _is_owned_archive_file(self, archive_file: Path) -> bool:
        return self._owned_archive_identity(archive_file) is not None

    def _remove_archived_original(
        self,
        archive_file: Path,
        *,
        expected_sha256: str | None = None,
        expected_size: int | None = None,
        expected_mtime_ns: int | None = None,
        expected_owner_token: str | None = None,
    ) -> bool:
        """Atomically claim and delete only the archive pair validated by this call.

        The random claim closes the check-to-unlink window: a replacement appearing
        after the first validation either remains at the public path or is restored
        after the claimed pair fails the second validation.
        """
        archive_file = Path(archive_file)
        metadata = self._archive_metadata_path(archive_file)
        initial = self._owned_archive_identity(archive_file)
        if initial is None:
            return False
        initial_metadata, initial_identity = initial
        if (
            (expected_sha256 is not None and initial_identity["sha256"] != expected_sha256)
            or (expected_size is not None and initial_identity["size"] != expected_size)
            or (expected_mtime_ns is not None and initial_identity["mtime_ns"] != expected_mtime_ns)
            or (
                expected_owner_token is not None
                and initial_metadata.get("owner_token") != expected_owner_token
            )
        ):
            return False

        claim_id = uuid.uuid4().hex
        archive_claim = archive_file.with_name(f".{archive_file.name}.{claim_id}.delete-claim")
        metadata_claim = metadata.with_name(f".{metadata.name}.{claim_id}.delete-claim")
        archive_claimed = False
        metadata_claimed = False
        try:
            self._publish_no_clobber(archive_file, archive_claim)
            archive_claimed = True
            self._publish_no_clobber(metadata, metadata_claim)
            metadata_claimed = True
        except OSError:
            self._restore_archive_claim(archive_claim, archive_file, archive_claimed)
            self._restore_archive_claim(metadata_claim, metadata, metadata_claimed)
            return False

        claimed = self._archive_pair_identity(
            archive_claim,
            metadata_claim,
            archive_file.name,
        )
        if claimed is None:
            self._restore_archive_claim(archive_claim, archive_file, True)
            self._restore_archive_claim(metadata_claim, metadata, True)
            return False
        claimed_metadata, claimed_identity = claimed
        if claimed_metadata != initial_metadata or any(
            claimed_identity[key] != initial_identity[key]
            for key in ("size", "mtime_ns", "sha256")
        ):
            self._restore_archive_claim(archive_claim, archive_file, True)
            self._restore_archive_claim(metadata_claim, metadata, True)
            return False

        try:
            # Revalidate the unpredictable claims immediately before deletion. Public
            # replacements at the original names are deliberately never touched.
            final = self._archive_pair_identity(
                archive_claim,
                metadata_claim,
                archive_file.name,
            )
            if final is None or final[0] != claimed_metadata or any(
                final[1][key] != claimed_identity[key]
                for key in ("size", "mtime_ns", "sha256")
            ):
                self._restore_archive_claim(archive_claim, archive_file, True)
                self._restore_archive_claim(metadata_claim, metadata, True)
                return False
            archive_claim.unlink()
            metadata_claim.unlink()
            return True
        except OSError:
            # A failed deletion keeps every remaining claim. Never delete or replace a
            # public path while ownership is no longer certain.
            LOGGER.exception(
                "Eigentumsgebundene Archivlöschung blieb unvollständig; Claims bleiben erhalten: %s, %s",
                archive_claim,
                metadata_claim,
            )
            return False

    def _restore_archive_claim(self, claim: Path, destination: Path, claimed: bool) -> bool:
        if not claimed or not claim.exists():
            return True
        if destination.exists():
            LOGGER.error(
                "Archiv-Claim konnte wegen eines neuen Race-Gewinners nicht zurückgestellt werden; "
                "Datei bleibt sicher erhalten: %s",
                claim,
            )
            return False
        try:
            self._publish_no_clobber(claim, destination)
            return True
        except OSError:
            LOGGER.exception("Archiv-Claim konnte nicht zurückgestellt werden: %s", claim)
            return False
