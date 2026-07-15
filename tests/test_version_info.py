from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from scanner_sorter import __version__
from scanner_sorter.version_info import collect_version_information, parse_tesseract_versions


class VersionInformationTests(unittest.TestCase):
    def test_parses_tesseract_and_leptonica_versions(self) -> None:
        output = "tesseract v5.5.0.20241111\n leptonica-1.85.0\n Found AVX2"

        self.assertEqual(("5.5.0.20241111", "1.85.0"), parse_tesseract_versions(output))

    @patch(
        "scanner_sorter.version_info._tesseract_versions",
        return_value=("5.5.0.20241111", "1.85.0", Path("tesseract.exe")),
    )
    def test_collects_application_ocr_and_library_versions(self, _mock_tesseract: object) -> None:
        report = collect_version_information(tcl_version="8.6", tk_version="8.6")

        self.assertEqual(__version__, report.application[0].version)
        self.assertIn(("Tesseract OCR", "5.5.0.20241111"), [(item.name, item.version) for item in report.ocr])
        self.assertIn(("Leptonica", "1.85.0"), [(item.name, item.version) for item in report.ocr])
        self.assertEqual(
            {"PyMuPDF", "pypdf", "Pillow", "pytesseract", "zxing-cpp", "pystray"},
            {item.name for item in report.libraries},
        )


if __name__ == "__main__":
    unittest.main()
