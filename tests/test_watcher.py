from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pypdf import PdfWriter

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


if __name__ == "__main__":
    unittest.main()
