from __future__ import annotations

import unittest
from pathlib import Path


class TesseractVendorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (
            Path(__file__).parents[1] / "scripts" / "prepare-tesseract-vendor.ps1"
        ).read_text(encoding="utf-8")

    def test_preparation_pins_tesseract_5_5_2_and_package_hash(self) -> None:
        self.assertIn("tesseract-ocr-5.5.2-1-any.pkg.tar.zst", self.script)
        self.assertIn("6667BE5FCD6A9489D65B84C954DAF21B3994155ADA92AD703EDCEC72B374D2EA", self.script)
        self.assertIn('"^tesseract 5\\.5\\.2$"', self.script)

    def test_preparation_pins_required_gcc_runtime(self) -> None:
        self.assertIn("libgomp-1.dll", self.script)
        self.assertIn("libwinpthread-1.dll", self.script)
        self.assertIn("AA560F5438C35B71C3E7B24FD5BECBCA028F70C5B4D1F1697A86FF80FEC947DA", self.script)
        self.assertIn("8F12DC1BE987165FAAB6363A159921553B4A2AC64E443CD0E7C501C343C2A92A", self.script)

    def test_preparation_verifies_all_required_languages(self) -> None:
        self.assertIn('@("deu", "eng", "osd")', self.script)

    def test_preparation_handles_installer_fallback_location(self) -> None:
        self.assertIn('Join-Path $env:ProgramFiles "Tesseract-OCR"', self.script)
        self.assertIn('Copy-Item -Path (Join-Path $installedBase "*")', self.script)


if __name__ == "__main__":
    unittest.main()
