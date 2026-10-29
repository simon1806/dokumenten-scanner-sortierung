from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


class TesseractVendorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (
            Path(__file__).parents[1] / "scripts" / "prepare-tesseract-vendor.ps1"
        ).read_text(encoding="utf-8")
        cls.source_script = (
            Path(__file__).parents[1] / "scripts" / "build-tesseract-source.ps1"
        ).read_text(encoding="utf-8")
        cls.build_tools = json.loads((
            Path(__file__).parents[1] / "scripts" / "tesseract-build-tools.lock.json"
        ).read_text(encoding="utf-8"))
        cls.runtime_packages = json.loads((
            Path(__file__).parents[1] / "scripts" / "tesseract-runtime.lock.json"
        ).read_text(encoding="utf-8"))

    def test_preparation_pins_tesseract_5_5_4_source_and_hash(self) -> None:
        self.assertIn("archive/refs/tags/5.5.4.tar.gz", self.source_script)
        self.assertIn("A8DBF8B8C8EE427FF6AD1D99116502FEAF59440AF1ADB893CAF60A6576826CF4", self.source_script)
        self.assertIn('"^tesseract 5\\.5\\.4$"', self.script)
        self.assertNotIn("tesseract-ocr-5.5.3", self.script)

    def test_preparation_pins_required_gcc_runtime(self) -> None:
        self.assertIn("libgomp-1.dll", self.source_script)
        self.assertIn("libwinpthread-1.dll", self.source_script)
        names = {package["name"] for package in self.build_tools}
        for name in ("gcc", "cmake", "ninja", "libgcc", "libstdc++", "libgomp",
                     "libatomic", "libquadmath", "libwinpthread"):
            self.assertIn("mingw-w64-x86_64-" + name, names)

    def test_build_tool_lock_has_unique_safe_names_and_sha256_hashes(self) -> None:
        names = [package["filename"] for package in self.build_tools]
        self.assertEqual(len(names), len(set(names)))
        for package in self.build_tools:
            with self.subTest(package=package["name"]):
                self.assertRegex(package["filename"], r"^mingw-w64-x86_64-[a-zA-Z0-9_.+~\-]+\.pkg\.tar\.zst$")
                self.assertTrue(re.fullmatch(r"[a-fA-F0-9]{64}", package["sha256"]))
                self.assertGreater(package["size"], 0)

    def test_source_build_checks_standalone_candidate_before_installation(self) -> None:
        self.assertIn('$env:PATH = [Environment]::SystemDirectory', self.source_script)
        self.assertIn('$env:PATH = $previousPath', self.source_script)
        self.assertIn('$env:TESSDATA_PREFIX = Join-Path $Candidate "tessdata"', self.source_script)
        self.assertIn('"-DENABLE_NATIVE=OFF"', self.source_script)
        self.assertIn('"-DOPENMP_BUILD=ON"', self.source_script)
        self.assertIn('"-DDISABLE_CURL=ON"', self.source_script)
        self.assertIn('"-DDISABLE_ARCHIVE=ON"', self.source_script)
        self.assertIn('foreach ($language in @("deu", "eng", "osd"))', self.source_script)
        self.assertLess(self.script.index('"build-tesseract-source.ps1"'),
                        self.script.index('Move-Item -LiteralPath $candidate -Destination $Destination'))

    def test_preparation_pins_leptonica_1_87_0_and_package_hash(self) -> None:
        leptonica = next(p for p in self.runtime_packages if p["name"] == "mingw-w64-x86_64-leptonica")
        self.assertEqual("mingw-w64-x86_64-leptonica-1.87.0-2-any.pkg.tar.zst", leptonica["filename"])
        self.assertRegex(leptonica["sha256"], r"^[a-f0-9]{64}$")
        self.assertIn('"leptonica-1\\.87\\.0"', self.script)

    def test_preparation_verifies_all_required_languages(self) -> None:
        self.assertIn('@("deu", "eng", "osd")', self.script)

    def test_preparation_uses_clean_runtime_and_restores_backup_on_failure(self) -> None:
        self.assertNotIn("BaseInstaller", self.script)
        self.assertIn('"mingw64\\bin\\*.dll"', self.script)
        self.assertIn('Move-Item -LiteralPath $Destination -Destination $backup', self.script)
        self.assertIn('Move-Item -LiteralPath $backup -Destination $Destination', self.script)

    def test_runtime_lock_includes_all_image_libraries_with_unique_hashes(self) -> None:
        names = {p["name"] for p in self.runtime_packages}
        for name in ("leptonica", "libpng", "libtiff", "libwebp", "libjpeg-turbo", "openjpeg2", "zlib"):
            self.assertIn("mingw-w64-x86_64-" + name, names)
        self.assertEqual(len(names), len(self.runtime_packages))
        for package in self.runtime_packages:
            self.assertRegex(package["sha256"], r"^[a-f0-9]{64}$")

    def test_preparation_rejects_vendor_destination_outside_project(self) -> None:
        self.assertIn("Der Vendor-Zielordner muss innerhalb des Projekts liegen", self.script)
        self.assertIn("[System.StringComparison]::OrdinalIgnoreCase", self.script)


if __name__ == "__main__":
    unittest.main()
