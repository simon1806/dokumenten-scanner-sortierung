from __future__ import annotations

import logging
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scanner_sorter.app import DailyFileHandler, log_file_path


class DailyLoggingTests(unittest.TestCase):
    def test_log_file_path_contains_calendar_date(self) -> None:
        settings = Path("settings.json")

        path = log_file_path(settings, date(2026, 7, 15))

        self.assertEqual(Path("logs/dokumentensortierer-2026-07-15.log"), path)

    def test_handler_switches_file_when_calendar_day_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            days = iter((date(2026, 7, 15), date(2026, 7, 16)))
            handler = DailyFileHandler(Path(directory), date_provider=lambda: next(days))
            handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
            first = logging.LogRecord("test", logging.INFO, __file__, 1, "Erster Tag", (), None)
            second = logging.LogRecord("test", logging.WARNING, __file__, 2, "Zweiter Tag", (), None)

            handler.emit(first)
            handler.emit(second)

            first_path = Path(directory) / "dokumentensortierer-2026-07-15.log"
            second_path = Path(directory) / "dokumentensortierer-2026-07-16.log"
            self.assertEqual("INFO Erster Tag\n", first_path.read_text(encoding="utf-8"))
            self.assertEqual("WARNING Zweiter Tag\n", second_path.read_text(encoding="utf-8"))
            first_path.rename(first_path.with_suffix(".verschoben"))
