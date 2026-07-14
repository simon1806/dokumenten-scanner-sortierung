from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
            self.assertEqual(1, len(list(archive.rglob("unklar.pdf"))))


if __name__ == "__main__":
    unittest.main()
