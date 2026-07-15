from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from scanner_sorter.config import Settings
from scanner_sorter.models import DetectedDocument, ProcessResult
from scanner_sorter.processing import DocumentProcessor, PendingJobError, ProcessingError


class StubRecognizer:
    def __init__(self, detections: list[DetectedDocument | None]):
        self.detections = iter(detections)

    def recognise(self, _page: object) -> DetectedDocument | None:
        return next(self.detections)


class ProcessorIntegrationTests(unittest.TestCase):
    def _create_pdf(self, path: Path, page_count: int) -> None:
        writer = PdfWriter()
        for _ in range(page_count):
            writer.add_blank_page(width=595, height=842)
        with path.open("wb") as stream:
            writer.write(stream)

    def test_pending_recovery_stops_before_starting_second_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            pending = archive / ".dokumentensortierer" / "pending"
            for name in ("job-1", "job-2"):
                folder = pending / name
                folder.mkdir(parents=True)
                (folder / "job.json").write_text("{}", encoding="utf-8")
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            stop_requested = threading.Event()
            loaded_jobs: list[str] = []

            def load_job(job_file: Path) -> dict[str, object]:
                return {"job_id": job_file.parent.name, "source_size": 1}

            def begin_job(job_file: Path, _job: dict[str, object]) -> None:
                loaded_jobs.append(job_file.parent.name)
                if job_file.parent.name == "job-1":
                    stop_requested.set()

            def finish_job(job_file: Path, *_args: object) -> ProcessResult:
                return ProcessResult(job_file.parent.name, True, "Wiederhergestellt.")

            with (
                patch.object(processor, "_load_job", side_effect=load_job),
                patch.object(processor, "_claim_source_for_job", side_effect=begin_job),
                patch.object(processor, "_run_pending_job", side_effect=finish_job),
            ):
                results = processor.recover_incomplete_jobs(should_stop=stop_requested.is_set)

            self.assertEqual(["job-1"], loaded_jobs)
            self.assertEqual(1, len(results))
            self.assertTrue((pending / "job-2" / "job.json").is_file())

    def test_splits_documents_archives_source_and_keeps_continuation_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 4)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer(
                [
                    DetectedDocument("LS", "4783804", "Nowak"),
                    DetectedDocument("LS", "4783774", "Nowak"),
                    DetectedDocument("LS", "4781776", "Nowak"),
                    None,
                ]
            )

            with self.assertLogs("scanner_sorter.processing", level="INFO") as captured:
                result = processor.process(source)

            self.assertTrue(result.success)
            self.assertFalse(source.exists())
            self.assertEqual(
                ["LS-Nowak-4781776.pdf", "LS-Nowak-4783774.pdf", "LS-Nowak-4783804.pdf"],
                sorted(path.name for path in output.glob("*.pdf")),
            )
            self.assertEqual(1, len(list(archive.rglob("scan.pdf"))))
            summary = next(message for message in captured.output if "status=erfolgreich" in message)
            self.assertIn("seiten=4", summary)
            self.assertIn("dokumente=3", summary)
            self.assertIn("typen=LS:1S,LS:1S,LS:2S", summary)
            self.assertIn("archiv_s=", summary)
            self.assertIn("erkennung_s=", summary)
            self.assertIn("ausgabe_s=", summary)
            self.assertIn("gesamt_s=", summary)
            self.assertIn("Dauer:", result.message)

    def test_forwards_original_without_renaming_on_unrecognised_first_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "unklar.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer([None])

            result = processor.process(source)

            self.assertFalse(result.success)
            self.assertFalse(source.exists())
            self.assertTrue((output / "unklar.pdf").exists())
            self.assertTrue((output / "Nicht_erkannt" / "unklar.pdf").exists())
            self.assertEqual(1, len(list(archive.rglob("unklar.pdf"))))
            self.assertEqual(2, len(result.created_files))

    def test_archive_retention_starts_when_original_is_archived(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "alter_scan.pdf"
            self._create_pdf(source, 1)
            old_timestamp = time.time() - 60 * 24 * 60 * 60
            os.utime(source, (old_timestamp, old_timestamp))
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "032606201")])

            archived_after = time.time()
            result = processor.process(source)
            archived = next(archive.rglob("alter_scan.pdf"))

            self.assertTrue(result.success)
            self.assertGreaterEqual(archived.stat().st_mtime, archived_after - 1)
            self.assertEqual(0, processor.cleanup_archive())
            self.assertTrue(archived.exists())

    def test_pending_job_recovers_when_source_cannot_be_atomically_claimed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "nicht_loeschbar.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer([DetectedDocument("LS", "4783596", "Nowak")])
            original_move = DocumentProcessor._move_no_clobber

            def guarded_move(source_path: Path, destination: Path) -> None:
                if destination.suffix == ".claim":
                    raise PermissionError("Test: Eingangsdatei darf nicht beansprucht werden")
                original_move(source_path, destination)

            with patch.object(DocumentProcessor, "_move_no_clobber", side_effect=guarded_move):
                result = processor.process(source)

            self.assertFalse(result.success)
            self.assertTrue(source.exists())
            self.assertEqual([], list(output.glob("*.pdf")))
            self.assertEqual(1, len(list(archive.glob("????-??-??/*.pdf"))))
            self.assertEqual(
                1,
                len(list((archive / ".dokumentensortierer" / "pending").glob("*/job.json"))),
            )

            recovered = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(recovered))
            self.assertTrue(recovered[0].success)
            self.assertFalse(source.exists())
            self.assertTrue((output / "LS-Nowak-4783596.pdf").is_file())

    def test_multi_document_publication_rolls_back_completely_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "mehrteilig.pdf"
            self._create_pdf(source, 2)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer(
                [DetectedDocument("AM", "3250001"), DetectedDocument("EM", "6250002")]
            )
            original_publish = DocumentProcessor._publish_no_clobber

            def fail_second_publication(source_path: Path, destination: Path) -> None:
                destination_path = Path(destination)
                if destination_path.parent == output.resolve() and destination_path.name == "EM_6250002.pdf":
                    raise PermissionError("Test: zweite Zieldatei blockiert")
                original_publish(source_path, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=fail_second_publication,
            ):
                first_result = processor.process(source)

            self.assertFalse(first_result.success)
            self.assertFalse(source.exists())
            self.assertEqual([], list(output.glob("*.pdf")))
            self.assertEqual([], list(output.glob("*.tmp")))
            self.assertEqual(1, len(list((archive / ".dokumentensortierer" / "pending").glob("*/job.json"))))

            recovered = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(recovered))
            self.assertTrue(recovered[0].success)
            self.assertEqual(["AM_3250001.pdf", "EM_6250002.pdf"], sorted(path.name for path in output.glob("*.pdf")))
            self.assertEqual([], list(output.glob("*.tmp")))
            self.assertEqual([], list((archive / ".dokumentensortierer" / "pending").glob("*/job.json")))

    def test_unrecognised_forwarding_waits_for_review_folder_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            review = root / "pruefen"
            incoming.mkdir()
            source = incoming / "unklar.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(
                Settings(str(incoming), str(output), str(archive), str(review))
            )
            processor.recognizer = StubRecognizer([None])
            original_copy2 = shutil.copy2

            def fail_review_copy(source_path: str | Path, destination: str | Path) -> str:
                if Path(destination).parent == review.resolve():
                    raise PermissionError("Test: Prüfordner blockiert")
                return str(original_copy2(source_path, destination))

            with patch("scanner_sorter.processing.shutil.copy2", side_effect=fail_review_copy):
                first_result = processor.process(source)

            self.assertFalse(first_result.success)
            self.assertIn("zurückgestellt", first_result.message)
            self.assertEqual([], list(output.glob("*.pdf")))
            self.assertEqual([], list(output.glob("*.tmp")))
            self.assertEqual([], list(review.glob("*.pdf")))
            self.assertEqual([], list(review.glob("*.tmp")))

            recovered = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(recovered))
            self.assertFalse(recovered[0].success)  # Fachstatus "nicht erkannt" bleibt erhalten.
            self.assertTrue((output / "unklar.pdf").is_file())
            self.assertTrue((review / "unklar.pdf").is_file())
            self.assertEqual(2, len(recovered[0].created_files))

    def test_permanent_pdf_split_error_forwards_unchanged_original_and_review_copy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            review = root / "pruefen"
            incoming.mkdir()
            source = incoming / "sonderfall.pdf"
            self._create_pdf(source, 1)
            original_bytes = source.read_bytes()
            processor = DocumentProcessor(
                Settings(str(incoming), str(output), str(archive), str(review))
            )
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "3250999")])

            with patch(
                "scanner_sorter.processing.PdfReader",
                side_effect=ValueError("Test: pypdf kann Sonderfall nicht trennen"),
            ):
                result = processor.process(source)

            self.assertFalse(result.success)
            self.assertIn("nicht sicher getrennt", result.message)
            self.assertFalse(source.exists())
            self.assertEqual(original_bytes, (output / "sonderfall.pdf").read_bytes())
            self.assertEqual(original_bytes, (review / "sonderfall.pdf").read_bytes())
            self.assertEqual([], list(output.glob("AM_*.pdf")))

    def test_cleanup_removes_only_owned_direct_archive_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "eigen.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(
                Settings(
                    str(incoming),
                    str(output),
                    str(archive),
                    archive_retention_days=1,
                )
            )
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "3250003")])
            self.assertTrue(processor.process(source).success)
            owned = next(archive.glob("????-??-??/eigen.pdf"))

            foreign_dated = archive / "2000-01-01"
            foreign_dated.mkdir()
            foreign = foreign_dated / "fremd.pdf"
            self._create_pdf(foreign, 1)
            nested = owned.parent / "fremder_unterordner" / "fremd.pdf"
            nested.parent.mkdir()
            self._create_pdf(nested, 1)
            foreign_in_owned_folder = owned.parent / "fremd_ohne_nachweis.pdf"
            self._create_pdf(foreign_in_owned_folder, 1)
            pending_pdf = archive / ".dokumentensortierer" / "fremd.pdf"
            pending_pdf.parent.mkdir(parents=True, exist_ok=True)
            self._create_pdf(pending_pdf, 1)
            old_timestamp = time.time() - 10 * 24 * 60 * 60
            for path in (owned, foreign, nested, foreign_in_owned_folder, pending_pdf):
                os.utime(path, (old_timestamp, old_timestamp))

            removed = processor.cleanup_archive()

            self.assertEqual(1, removed)
            self.assertFalse(owned.exists())
            self.assertTrue(foreign.exists())
            self.assertTrue(nested.exists())
            self.assertTrue(foreign_in_owned_folder.exists())
            self.assertTrue(pending_pdf.exists())

    def test_cleanup_refuses_overlapping_archive_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_folder = root / "arbeitsbereich"
            output = input_folder / "ziel"
            input_folder.mkdir()
            old_pdf = input_folder / "2000-01-01" / "nicht_loeschen.pdf"
            old_pdf.parent.mkdir()
            self._create_pdf(old_pdf, 1)
            os.utime(old_pdf, (0, 0))
            processor = DocumentProcessor(
                Settings(str(input_folder), str(output), str(input_folder))
            )

            self.assertEqual(0, processor.cleanup_archive())
            self.assertTrue(old_pdf.exists())

    def test_old_pending_job_never_claims_new_files_at_the_same_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "3251001")])

            with patch(
                "scanner_sorter.processing.shutil.copy2",
                side_effect=PermissionError("Test: alter Vorgang bleibt offen"),
            ):
                old_result = processor.process(source)

            self.assertFalse(old_result.success)
            self.assertFalse(source.exists())
            self.assertEqual(
                1,
                len(list((archive / ".dokumentensortierer" / "pending").glob("*/job.json"))),
            )

            # A second scan arrives under exactly the same scanner filename. Calling
            # process directly must not reuse the older pending manifest by path only.
            self._create_pdf(source, 2)
            processor.recognizer = StubRecognizer([DetectedDocument("EM", "6251002"), None])
            new_result = processor.process(source)

            self.assertTrue(new_result.success)
            self.assertTrue((output / "EM_6251002.pdf").is_file())
            self.assertFalse(source.exists())

            # A third scan arrives before recovery. Recovery may publish the old job,
            # but must preserve the new file byte-for-byte for its own later process.
            self._create_pdf(source, 3)
            new_bytes = source.read_bytes()
            new_stat = source.stat()

            recovered = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(recovered))
            self.assertTrue(recovered[0].success)
            self.assertTrue((output / "AM_3251001.pdf").is_file())
            self.assertTrue(source.is_file())
            self.assertEqual(new_bytes, source.read_bytes())
            self.assertEqual(new_stat.st_size, source.stat().st_size)
            self.assertEqual(new_stat.st_mtime_ns, source.stat().st_mtime_ns)

            processor.recognizer = StubRecognizer(
                [DetectedDocument("MI", "3251003"), None, None]
            )
            third_result = processor.process(source)

            self.assertTrue(third_result.success)
            self.assertTrue((output / "MI_3251003.pdf").is_file())
            self.assertFalse(source.exists())

    def test_rollback_preserves_matching_file_that_existed_before_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "mehrteilig.pdf"
            self._create_pdf(source, 2)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer(
                [DetectedDocument("AM", "3252001"), DetectedDocument("EM", "6252002")]
            )
            original_publish = DocumentProcessor._publish_no_clobber

            def fail_second_publication(source_path: Path, destination: Path) -> None:
                destination_path = Path(destination)
                if destination_path.parent == output.resolve() and destination_path.name == "EM_6252002.pdf":
                    raise PermissionError("Test: zweite Zieldatei blockiert")
                original_publish(source_path, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=fail_second_publication,
            ):
                self.assertFalse(processor.process(source).success)

            job_file = next(
                (archive / ".dokumentensortierer" / "pending").glob("*/job.json")
            )
            job = json.loads(job_file.read_text(encoding="utf-8"))
            first_item = job["plan"][0]
            first_stage = job_file.parent / first_item["stage"]
            first_destination = Path(first_item["destination"])
            first_destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(first_stage, first_destination)
            existing_bytes = first_destination.read_bytes()

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=fail_second_publication,
            ):
                failed_retry = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(failed_retry))
            self.assertFalse(failed_retry[0].success)
            self.assertTrue(first_destination.is_file())
            self.assertEqual(existing_bytes, first_destination.read_bytes())
            self.assertEqual([], list(output.glob("*.tmp")))

            final_retry = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(final_retry))
            self.assertTrue(final_retry[0].success)
            self.assertTrue((output / "AM_3252001.pdf").is_file())
            self.assertTrue((output / "EM_6252002.pdf").is_file())

    def test_foreign_file_winning_publish_race_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "3253001")])
            original_publish = DocumentProcessor._publish_no_clobber
            foreign_bytes = b"FREMDE DATEI - NICHT UEBERSCHREIBEN"
            injected = False

            def inject_foreign_file(temporary: Path, destination: Path) -> None:
                nonlocal injected
                if destination.name == "AM_3253001.pdf" and not injected:
                    destination.write_bytes(foreign_bytes)
                    injected = True
                original_publish(temporary, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=inject_foreign_file,
            ):
                first_result = processor.process(source)

            raced_destination = output / "AM_3253001.pdf"
            self.assertFalse(first_result.success)
            self.assertEqual(foreign_bytes, raced_destination.read_bytes())
            self.assertEqual([], list(output.glob("*.tmp")))

            recovered = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(recovered))
            self.assertTrue(recovered[0].success)
            self.assertEqual(foreign_bytes, raced_destination.read_bytes())
            self.assertTrue((output / "AM_3253001_2.pdf").is_file())

    def test_archive_name_race_preserves_foreign_pdf_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            source_bytes = source.read_bytes()
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_write_metadata = processor._write_archive_metadata
            foreign_pdf = b"FREMDE ARCHIVDATEI"
            foreign_metadata = b"FREMDE METADATEN"
            injected = False

            def inject_foreign_archive_pair(
                archive_file: Path,
                archive_hash: str,
                archive_size: int,
                owner_token: str,
            ) -> None:
                nonlocal injected
                if archive_file.name == "scan.pdf" and not injected:
                    archive_file.write_bytes(foreign_pdf)
                    processor._archive_metadata_path(archive_file).write_bytes(foreign_metadata)
                    injected = True
                original_write_metadata(
                    archive_file,
                    archive_hash,
                    archive_size,
                    owner_token,
                )

            with patch.object(
                processor,
                "_write_archive_metadata",
                side_effect=inject_foreign_archive_pair,
            ):
                archived, _archive_hash, _owner_token = processor._archive_original(source)

            dated_archive = archived.parent
            raced_pdf = dated_archive / "scan.pdf"
            raced_metadata = processor._archive_metadata_path(raced_pdf)
            self.assertEqual("scan_2.pdf", archived.name)
            self.assertEqual(source_bytes, archived.read_bytes())
            self.assertEqual(foreign_pdf, raced_pdf.read_bytes())
            self.assertEqual(foreign_metadata, raced_metadata.read_bytes())
            self.assertTrue(processor._is_owned_archive_file(archived))

    def test_archive_marker_race_preserves_and_accepts_valid_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_publish = DocumentProcessor._publish_no_clobber
            winner_mtime_ns = 1_600_000_000_000_000_000
            injected = False

            def inject_valid_marker(temporary: Path, destination: Path) -> None:
                nonlocal injected
                if destination.name == ".dokumentensortierer-archiv-v1" and not injected:
                    destination.write_text(
                        "DokumentenScannerSortierung archive v1\n",
                        encoding="utf-8",
                    )
                    os.utime(destination, ns=(winner_mtime_ns, winner_mtime_ns))
                    injected = True
                original_publish(temporary, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=inject_valid_marker,
            ):
                archived, _archive_hash, _owner_token = processor._archive_original(source)

            marker = archived.parent / ".dokumentensortierer-archiv-v1"
            self.assertEqual(winner_mtime_ns, marker.stat().st_mtime_ns)
            self.assertTrue(archived.is_file())

    def test_archive_marker_race_rejects_invalid_winner_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_publish = DocumentProcessor._publish_no_clobber
            invalid_marker = b"FREMDER UNGUELTIGER MARKER"
            injected = False

            def inject_invalid_marker(temporary: Path, destination: Path) -> None:
                nonlocal injected
                if destination.name == ".dokumentensortierer-archiv-v1" and not injected:
                    destination.write_bytes(invalid_marker)
                    injected = True
                original_publish(temporary, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=inject_invalid_marker,
            ):
                with self.assertRaisesRegex(ProcessingError, "Archivmarker ist ungültig"):
                    processor._archive_original(source)

            marker = next(archive.glob("????-??-??/.dokumentensortierer-archiv-v1"))
            self.assertEqual(invalid_marker, marker.read_bytes())
            self.assertEqual([], list(archive.glob("????-??-??/*.pdf")))

    def test_archive_pdf_race_removes_only_own_metadata_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            source_bytes = source.read_bytes()
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_publish = DocumentProcessor._publish_no_clobber
            foreign_pdf = b"FREMDE DATEI GEWINNT PDF-RACE"
            injected = False

            def inject_foreign_pdf(temporary: Path, destination: Path) -> None:
                nonlocal injected
                if destination.name == "scan.pdf" and not injected:
                    destination.write_bytes(foreign_pdf)
                    injected = True
                original_publish(temporary, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=inject_foreign_pdf,
            ):
                archived, _archive_hash, _owner_token = processor._archive_original(source)

            raced_pdf = archived.parent / "scan.pdf"
            self.assertEqual("scan_2.pdf", archived.name)
            self.assertEqual(source_bytes, archived.read_bytes())
            self.assertEqual(foreign_pdf, raced_pdf.read_bytes())
            self.assertFalse(processor._archive_metadata_path(raced_pdf).exists())
            self.assertTrue(processor._is_owned_archive_file(archived))

    def test_metadata_cleanup_claim_preserves_public_race_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            metadata = root / "reservation.json"
            owner_token = "own-reservation-token"
            metadata.write_text(
                json.dumps({"owner_token": owner_token}),
                encoding="utf-8",
            )
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_read_text = Path.read_text
            public_winner = b"FREMDE METADATEN AM OEFFENTLICHEN NAMEN"
            injected = False

            def inject_public_winner(
                path: Path,
                encoding: str | None = None,
                errors: str | None = None,
            ) -> str:
                nonlocal injected
                content = original_read_text(path, encoding=encoding, errors=errors)
                if path.name.endswith(".metadata-delete-claim") and not injected:
                    metadata.write_bytes(public_winner)
                    injected = True
                return content

            with patch.object(Path, "read_text", new=inject_public_winner):
                removed = processor._remove_archive_metadata_if_owned(metadata, owner_token)

            self.assertTrue(removed)
            self.assertEqual(public_winner, metadata.read_bytes())
            self.assertEqual([], list(root.glob("*.metadata-delete-claim")))

    def test_metadata_cleanup_never_overwrites_winner_when_restoring_foreign_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            metadata = root / "reservation.json"
            expected_token = "expected-owner-token"
            metadata.write_text(
                json.dumps({"owner_token": expected_token}),
                encoding="utf-8",
            )
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_publish = DocumentProcessor._publish_no_clobber
            foreign_claim = json.dumps({"owner_token": "foreign-claim"}).encode()
            public_winner = b"NEUER FREMDER RACE-GEWINNER"
            source_replaced = False
            restore_raced = False

            def inject_two_races(temporary: Path, destination: Path) -> None:
                nonlocal source_replaced, restore_raced
                if temporary == metadata and not source_replaced:
                    metadata.write_bytes(foreign_claim)
                    source_replaced = True
                elif (
                    temporary.name.endswith(".metadata-delete-claim")
                    and destination == metadata
                    and not restore_raced
                ):
                    metadata.write_bytes(public_winner)
                    restore_raced = True
                original_publish(temporary, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=inject_two_races,
            ):
                removed = processor._remove_archive_metadata_if_owned(metadata, expected_token)

            claims = list(root.glob("*.metadata-delete-claim"))
            self.assertFalse(removed)
            self.assertEqual(public_winner, metadata.read_bytes())
            self.assertEqual(1, len(claims))
            self.assertEqual(foreign_claim, claims[0].read_bytes())

    def test_metadata_cleanup_unlink_failure_restores_own_claim(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            metadata = root / "reservation.json"
            owner_token = "own-reservation-token"
            own_metadata = json.dumps({"owner_token": owner_token}).encode()
            metadata.write_bytes(own_metadata)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_unlink = Path.unlink
            failed = False

            def fail_claim_unlink(path: Path, missing_ok: bool = False) -> None:
                nonlocal failed
                if path.name.endswith(".metadata-delete-claim") and not failed:
                    failed = True
                    raise PermissionError("Test: Claim vorübergehend gesperrt")
                original_unlink(path, missing_ok=missing_ok)

            with patch.object(Path, "unlink", new=fail_claim_unlink):
                removed = processor._remove_archive_metadata_if_owned(metadata, owner_token)

            self.assertFalse(removed)
            self.assertEqual(own_metadata, metadata.read_bytes())
            self.assertEqual([], list(root.glob("*.metadata-delete-claim")))

    def test_archive_deletion_claim_revalidates_race_winner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            archived, archive_hash, owner_token = processor._archive_original(source)
            metadata = processor._archive_metadata_path(archived)
            original_publish = DocumentProcessor._publish_no_clobber
            foreign_pdf = b"FREMDER RACE-GEWINNER DARF NICHT GELOESCHT WERDEN"
            injected = False

            def replace_after_validation(temporary: Path, destination: Path) -> None:
                nonlocal injected
                if temporary == archived and destination.name.endswith(".delete-claim") and not injected:
                    archived.write_bytes(foreign_pdf)
                    foreign_metadata = json.loads(metadata.read_text(encoding="utf-8"))
                    foreign_metadata["size"] = len(foreign_pdf)
                    foreign_metadata["sha256"] = processor._sha256(archived)
                    foreign_metadata["owner_token"] = "foreign-race-winner"
                    metadata.write_text(
                        json.dumps(foreign_metadata, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    injected = True
                original_publish(temporary, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=replace_after_validation,
            ):
                removed = processor._remove_archived_original(
                    archived,
                    expected_sha256=archive_hash,
                    expected_owner_token=owner_token,
                )

            self.assertFalse(removed)
            self.assertEqual(foreign_pdf, archived.read_bytes())
            self.assertEqual(
                "foreign-race-winner",
                json.loads(metadata.read_text(encoding="utf-8"))["owner_token"],
            )
            self.assertEqual([], list(archived.parent.glob("*.delete-claim")))

    def test_tampered_pending_identity_cannot_claim_unrelated_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            original_source = incoming / "ursprung.pdf"
            unrelated_source = incoming / "neuer_scan.pdf"
            self._create_pdf(original_source, 1)
            self._create_pdf(unrelated_source, 2)
            unrelated_bytes = unrelated_source.read_bytes()
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            original_identity = processor._capture_source_identity(original_source)
            archive_path, _archive_hash, _owner_token = processor._archive_original(original_source)
            job_file = processor._create_pending_job(
                operation_id="manipulation",
                source=original_source,
                source_name=original_source.name,
                source_size=original_source.stat().st_size,
                source_identity=original_identity,
                archive_path=archive_path,
            )
            job = json.loads(job_file.read_text(encoding="utf-8"))
            job["source_name"] = unrelated_source.name
            job["source_path"] = str(unrelated_source.resolve())
            job["source_claim"] = str(incoming.resolve() / f".{job['job_id']}.claim")
            job["source_size"] = unrelated_source.stat().st_size
            job["source_identity"] = processor._capture_source_identity(unrelated_source)
            processor._write_job(job_file, job)

            with self.assertRaisesRegex(PendingJobError, "archivierten Original"):
                processor._claim_source_for_job(job_file, job)

            self.assertEqual(unrelated_bytes, unrelated_source.read_bytes())
            self.assertFalse(Path(job["source_claim"]).exists())
            self.assertTrue(archive_path.is_file())

    def test_cleanup_protects_old_archive_referenced_by_pending_job(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "offen.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(
                Settings(
                    str(incoming),
                    str(output),
                    str(archive),
                    archive_retention_days=1,
                )
            )
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "3253002")])
            original_publish = DocumentProcessor._publish_no_clobber

            def block_output_only(temporary: Path, destination: Path) -> None:
                if destination.parent == output.resolve():
                    raise PermissionError("Test: Ziel vorübergehend blockiert")
                original_publish(temporary, destination)

            with patch.object(
                DocumentProcessor,
                "_publish_no_clobber",
                side_effect=block_output_only,
            ):
                self.assertFalse(processor.process(source).success)

            archived = next(archive.glob("????-??-??/offen.pdf"))
            old_timestamp = time.time() - 10 * 24 * 60 * 60
            os.utime(archived, (old_timestamp, old_timestamp))

            self.assertEqual(0, processor.cleanup_archive())
            self.assertTrue(archived.is_file())

            recovered = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(recovered))
            self.assertTrue(recovered[0].success)
            self.assertTrue((output / "AM_3253002.pdf").is_file())
            self.assertTrue(archived.is_file())
            self.assertEqual(1, processor.cleanup_archive())
            self.assertFalse(archived.exists())

    def test_claim_left_by_interruption_is_recovered_without_input_loss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "scan.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(Settings(str(incoming), str(output), str(archive)))
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "3253003")])
            original_unlink = Path.unlink
            interrupted = False

            def interrupt_claim_cleanup(path: Path, missing_ok: bool = False) -> None:
                nonlocal interrupted
                if path.suffix == ".claim" and not interrupted:
                    interrupted = True
                    raise PermissionError("Test: Abbruch direkt nach atomarem Claim")
                original_unlink(path, missing_ok=missing_ok)

            with patch.object(Path, "unlink", new=interrupt_claim_cleanup):
                first_result = processor.process(source)

            claims = list(incoming.glob(".*.claim"))
            self.assertFalse(first_result.success)
            self.assertFalse(source.exists())
            self.assertEqual(1, len(claims))
            self.assertEqual([], list(output.glob("*.pdf")))

            recovered = processor.recover_incomplete_jobs()

            self.assertEqual(1, len(recovered))
            self.assertTrue(recovered[0].success)
            self.assertEqual([], list(incoming.glob(".*.claim")))
            self.assertTrue((output / "AM_3253003.pdf").is_file())

    def test_cleanup_fails_closed_when_any_pending_manifest_is_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            incoming = root / "eingang"
            output = root / "ziel"
            archive = root / "archiv"
            incoming.mkdir()
            source = incoming / "archiviert.pdf"
            self._create_pdf(source, 1)
            processor = DocumentProcessor(
                Settings(
                    str(incoming),
                    str(output),
                    str(archive),
                    archive_retention_days=1,
                )
            )
            processor.recognizer = StubRecognizer([DetectedDocument("AM", "3253004")])
            self.assertTrue(processor.process(source).success)
            archived = next(archive.glob("????-??-??/archiviert.pdf"))
            old_timestamp = time.time() - 10 * 24 * 60 * 60
            os.utime(archived, (old_timestamp, old_timestamp))
            corrupt_job = archive / ".dokumentensortierer" / "pending" / "kaputt" / "job.json"
            corrupt_job.parent.mkdir(parents=True)
            corrupt_job.write_text("{unlesbar", encoding="utf-8")

            removed = processor.cleanup_archive()

            self.assertEqual(0, removed)
            self.assertTrue(archived.is_file())

    def test_non_windows_claim_fails_before_touching_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.pdf"
            destination = Path(directory) / "claim.pending"
            source.write_bytes(b"Original bleibt erhalten")

            with patch("scanner_sorter.processing.os.name", "posix"):
                with self.assertRaisesRegex(OSError, "nur unter Windows"):
                    DocumentProcessor._move_no_clobber(source, destination)

            self.assertEqual(b"Original bleibt erhalten", source.read_bytes())
            self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
