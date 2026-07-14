from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter

from scanner_sorter.config import Settings
from scanner_sorter.models import DetectedDocument
from scanner_sorter.processing import DocumentProcessor


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

            result = processor.process(source)

            self.assertTrue(result.success)
            self.assertFalse(source.exists())
            self.assertEqual(
                ["LS-Nowak-4781776.pdf", "LS-Nowak-4783774.pdf", "LS-Nowak-4783804.pdf"],
                sorted(path.name for path in output.glob("*.pdf")),
            )
            self.assertEqual(1, len(list(archive.rglob("scan.pdf"))))

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

    def test_no_outputs_are_created_when_source_cannot_be_removed(self) -> None:
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
            original_unlink = Path.unlink

            def guarded_unlink(path: Path, missing_ok: bool = False) -> None:
                if path == source:
                    raise PermissionError("Test: Eingangsdatei darf nicht gelöscht werden")
                original_unlink(path, missing_ok=missing_ok)

            with patch.object(Path, "unlink", new=guarded_unlink):
                result = processor.process(source)

            self.assertFalse(result.success)
            self.assertTrue(source.exists())
            self.assertEqual([], list(output.glob("*.pdf")))
            self.assertEqual([], list(archive.rglob("*.pdf")))


if __name__ == "__main__":
    unittest.main()
