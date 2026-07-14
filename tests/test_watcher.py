from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from pypdf import PdfWriter

from scanner_sorter.config import Settings
from scanner_sorter.watcher import FolderWatcher


class WatcherTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
