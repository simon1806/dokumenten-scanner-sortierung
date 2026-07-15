from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scanner_sorter.config import ConfigurationError, Settings, load_settings, save_settings


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

    def test_default_review_subfolder_is_allowed_but_other_nested_work_folders_are_not(self) -> None:
        valid = Settings(
            input_folder="basis/eingang",
            output_folder="basis/ziel",
            archive_folder="basis/archiv",
        )
        unsafe = Settings(
            input_folder="basis",
            output_folder="basis/ziel",
            archive_folder="basis/archiv",
            review_folder="basis/pruefung",
        )

        self.assertFalse(any("ineinander" in error for error in valid.validate()))
        self.assertTrue(any("ineinander" in error for error in unsafe.validate()))

    def test_review_folder_may_be_below_target_but_never_its_parent(self) -> None:
        valid = Settings(
            input_folder="basis/eingang",
            output_folder="basis/ziel",
            archive_folder="basis/archiv",
            review_folder="basis/ziel/pruefung",
        )
        unsafe = Settings(
            input_folder="basis/eingang",
            output_folder="basis/ziel/ausgabe",
            archive_folder="basis/archiv",
            review_folder="basis/ziel",
        )

        self.assertFalse(any("ineinander" in error for error in valid.validate()))
        self.assertTrue(any("ineinander" in error for error in unsafe.validate()))

    def test_filesystem_root_cannot_be_used_as_any_work_folder(self) -> None:
        root = Path.cwd().anchor
        for field_name in ("input_folder", "output_folder", "archive_folder", "review_folder"):
            values = {
                "input_folder": "eingang",
                "output_folder": "ziel",
                "archive_folder": "archiv",
                "review_folder": "pruefung",
            }
            values[field_name] = root
            with self.subTest(field_name=field_name):
                self.assertTrue(
                    any("stamm" in error.lower() for error in Settings(**values).validate())
                )

    def test_corrupt_or_wrongly_typed_settings_raise_clear_configuration_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text("{kaputt", encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "beschädigt oder nicht lesbar"):
                load_settings(path)

            path.write_text(json.dumps({"settle_seconds": "zwei"}), encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "settle_seconds"):
                load_settings(path)

    def test_save_settings_is_atomic_and_preserves_existing_file_on_replace_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            original = '{"input_folder": "alt"}\n'
            path.write_text(original, encoding="utf-8")

            with patch("scanner_sorter.config.os.replace", side_effect=OSError("Testfehler")):
                with self.assertRaises(OSError):
                    save_settings(Settings(input_folder="neu"), path)

            self.assertEqual(original, path.read_text(encoding="utf-8"))
            self.assertEqual([], list(path.parent.glob(".settings.json.*.tmp")))

    def test_save_and_load_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            settings = Settings(
                input_folder="eingang",
                output_folder="ziel",
                archive_folder="archiv",
                archive_retention_days=45,
            )

            save_settings(settings, path)

            self.assertEqual(settings, load_settings(path))
            self.assertFalse(any(name.endswith(".tmp") for name in os.listdir(path.parent)))


if __name__ == "__main__":
    unittest.main()
