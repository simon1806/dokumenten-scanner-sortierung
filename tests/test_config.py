from __future__ import annotations

import unittest
from pathlib import Path

from scanner_sorter.config import Settings


class SettingsTests(unittest.TestCase):
    def test_review_folder_defaults_to_subfolder_of_output(self) -> None:
        settings = Settings(input_folder="eingang", output_folder="ziel", archive_folder="archiv")

        self.assertEqual(Path("ziel") / "Nicht_erkannt", settings.review_folder_path)

    def test_explicit_review_folder_is_used(self) -> None:
        settings = Settings(
            input_folder="eingang",
            output_folder="ziel",
            archive_folder="archiv",
            review_folder="pruefung",
        )

        self.assertEqual(Path("pruefung"), settings.review_folder_path)

    def test_invalid_pdf_timeout_must_not_be_shorter_than_settle_time(self) -> None:
        settings = Settings(
            input_folder="eingang",
            output_folder="ziel",
            archive_folder="archiv",
            settle_seconds=10,
            invalid_pdf_timeout_seconds=5,
        )

        self.assertTrue(any("beschädigte PDFs" in error for error in settings.validate()))


if __name__ == "__main__":
    unittest.main()
