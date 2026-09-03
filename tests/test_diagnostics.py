from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scanner_sorter.diagnostics import (
    LEGACY_REASON_CODE,
    _statistics,
    build_diagnostic_report,
    export_diagnostic_report,
    render_diagnostic_html,
)


class DiagnosticReportTests(unittest.TestCase):
    report_day = date(2026, 8, 19)

    def _write_log(self, directory: str, content: str) -> Path:
        path = Path(directory) / "dokumentensortierer-2026-08-19.log"
        path.write_text(content, encoding="utf-8")
        return path

    def test_parser_reads_new_and_legacy_processing_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 06:00:00,000 INFO scanner_sorter.app [MainThread]: "
                "Anwendung gestartet; version=0.3.0\n"
                "2026-08-19 06:01:00,000 INFO scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; status=erfolgreich; version=0.3.0; "
                "datei=ok.pdf; groesse_bytes=1000; gesamt_s=2.000; typen=AM:4S\n"
                "2026-08-19 06:02:00,000 WARNING scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; status=nicht_erkannt; datei=alt.pdf; "
                "groesse_bytes=2000; gesamt_s=4.000\n",
            )

            report = build_diagnostic_report(
                directory, days=7, end_date=self.report_day
            )

            results = report["summary"]["processing_results"]
            self.assertEqual(2, results["total"])
            self.assertEqual(1, results["successful"])
            self.assertEqual(1, results["not_recognized"])
            self.assertEqual(0.5, results["recognition_rate"])
            self.assertEqual(1, report["parser_statistics"]["legacy_records"])
            self.assertEqual(1, report["grouped_by_reason_code"][LEGACY_REASON_CODE])
            self.assertNotIn("nicht_angegeben", report["grouped_by_reason_code"])
            self.assertEqual(1, report["grouped_by_document_type"]["AM"])
            self.assertEqual(2, report["summary"]["application_starts"] + 1)
            self.assertIn("0.3.0", report["grouped_by_version"])

    def test_parser_tolerates_partial_lines_unknown_fields_and_live_append_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = self._write_log(directory, "unvollständige Zeile\n")
            with path.open("a", encoding="utf-8") as writer:
                writer.write(
                    "2026-08-19 07:00:00,000 INFO scanner_sorter.processing [worker]: "
                    "Vorgang abgeschlossen; status=erfolgreich; version=0.3.0; "
                    "gesamt_s=1.0; zukunftsfeld=ignorieren\n"
                )
                writer.flush()
                report = build_diagnostic_report(
                    directory, days=7, end_date=self.report_day
                )

            self.assertEqual(1, report["summary"]["processing_results"]["total"])
            self.assertEqual(1, report["parser_statistics"]["malformed_lines"])
            self.assertEqual(1, report["parser_statistics"]["unknown_fields_ignored"])
            self.assertEqual(1, report["parser_statistics"]["records_with_unknown_fields"])
            self.assertEqual(
                {"zukunftsfeld": 1}, report["parser_statistics"]["unknown_field_names"]
            )

    def test_queue_folder_errors_and_starts_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 06:00:00,000 INFO scanner_sorter.app [MainThread]: "
                "Anwendung gestartet; version=0.3.0\n"
                "2026-08-19 06:05:00,000 INFO scanner_sorter.watcher [watcher]: "
                "Betriebsstatus; version=0.3.0; wartende_pdfs=7\n"
                "2026-08-19 06:06:00,000 ERROR scanner_sorter.watcher [watcher]: "
                "Fehler bei der Ordnerüberwachung; versuch=1; fehler=Test\n"
                "2026-08-19 06:07:00,000 INFO scanner_sorter.watcher [watcher]: "
                "Ordnerüberwachung wiederhergestellt; versuche=1\n",
            )

            report = build_diagnostic_report(
                directory, days=30, end_date=self.report_day
            )

            self.assertEqual(1, report["summary"]["application_starts"])
            self.assertEqual(7, report["summary"]["queue"]["maximum_waiting_pdfs"])
            self.assertEqual(1, report["summary"]["folder_monitoring"]["errors"])
            self.assertEqual(1, report["summary"]["folder_monitoring"]["recoveries"])

    def test_current_heartbeat_fields_are_known_and_session_gaps_are_evaluated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 06:00:00,000 INFO scanner_sorter.app [MainThread]: "
                "Anwendung gestartet; schema=2; ereignis=application_started; sitzung=abc; "
                "version=0.3.0; modus=SYSTEM/Headless; prozess_id=10\n"
                "2026-08-19 06:10:00,000 INFO scanner_sorter.watcher [worker]: "
                "Betriebsstatus; schema=2; ereignis=monitor_heartbeat; sitzung=abc; "
                "version=0.3.0; intervall_s=600; laufzeit_s=600; wartende_pdfs=0; "
                "eingang=erreichbar; ziel=erreichbar; archiv=erreichbar; "
                "pruefordner=erreichbar; fortlaufende_ordnerfehler=0\n"
                "2026-08-19 06:20:00,000 INFO scanner_sorter.watcher [worker]: "
                "Betriebsstatus; schema=2; ereignis=monitor_heartbeat; sitzung=abc; "
                "version=0.3.0; intervall_s=600; wartende_pdfs=1\n"
                "2026-08-19 06:21:00,000 INFO scanner_sorter.app [MainThread]: "
                "Anwendung beendet; schema=2; ereignis=application_stopped; sitzung=abc; "
                "version=0.3.0; modus=SYSTEM/Headless; grund=kontrolliert; exit_code=0; "
                "laufzeit_s=1260\n",
            )

            report = build_diagnostic_report(directory, days=7, end_date=self.report_day)

            self.assertEqual({}, report["parser_statistics"]["unknown_field_names"])
            self.assertEqual(1, report["summary"]["queue"]["maximum_waiting_pdfs"])
            lifecycle = report["summary"]["application_lifecycle"]
            self.assertEqual(1, lifecycle["identified_sessions_started"])
            self.assertEqual(1, lifecycle["identified_sessions_stopped"])
            self.assertEqual(0, lifecycle["sessions_without_stop"])
            self.assertEqual({"kontrolliert": 1}, lifecycle["shutdown_reasons"])
            continuity = report["summary"]["heartbeat_continuity"]
            self.assertEqual(1, continuity["gaps_evaluated"])
            self.assertEqual(0, continuity["suspected_interruptions"])
            self.assertEqual(600.0, continuity["maximum_gap_seconds"])

    def test_unclassified_warning_and_error_levels_remain_visible(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 07:00:00,000 WARNING scanner_sorter.recognition [worker]: "
                "Barcode-Erkennung fehlgeschlagen\n"
                "2026-08-19 07:01:00,000 ERROR scanner_sorter.processing [worker]: "
                "Unerwartete interne Meldung\n",
            )

            report = build_diagnostic_report(directory, days=7, end_date=self.report_day)

            health = report["summary"]["log_health"]
            self.assertEqual(1, health["unclassified_warning_lines"])
            self.assertEqual(1, health["unclassified_error_or_critical_lines"])
            self.assertEqual(
                {"ERROR": 1, "WARNING": 1},
                report["parser_statistics"]["unclassified_lines_by_level"],
            )

    def test_structured_technical_error_is_classified_with_reason_and_stage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 07:00:00,000 ERROR scanner_sorter.processing [worker]: "
                "Vorgang fehlgeschlagen; schema=2; ereignis=processing_failed; sitzung=abc; "
                "id=123; status=fehler; version=0.3.1; "
                "grundcode=archivierung_fehlgeschlagen; stufe=archivieren; "
                "datei=scan.pdf; groesse_bytes=50; gesamt_s=0.5; fehlerklasse=OSError\n",
            )

            report = build_diagnostic_report(directory, days=7, end_date=self.report_day)

            results = report["summary"]["processing_results"]
            self.assertEqual(1, results["technical_errors"])
            self.assertEqual(1, report["grouped_by_reason_code"]["archivierung_fehlgeschlagen"])
            self.assertEqual("archivieren", report["problem_cases"][0]["stage"])
            self.assertEqual(0, report["summary"]["log_health"]["unclassified_error_or_critical_lines"])

    def test_phase_statistics_and_slowest_cases_use_existing_log_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 08:00:00,000 INFO scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; status=erfolgreich; version=0.3.0; datei=schnell.pdf; "
                "archiv_s=1; erkennung_s=2; ausgabe_s=3; gesamt_s=6\n"
                "2026-08-19 08:01:00,000 INFO scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; status=erfolgreich; version=0.3.0; datei=langsam.pdf; "
                "archiv_s=2; erkennung_s=8; ausgabe_s=4; gesamt_s=14\n",
            )

            report = build_diagnostic_report(directory, days=7, end_date=self.report_day)

            phases = report["summary"]["phase_duration_seconds"]
            self.assertEqual(5.0, phases["recognition"]["average"])
            self.assertEqual(4.0, phases["output"]["maximum"])
            self.assertEqual(
                10.0,
                report["grouped_by_version"]["0.3.0"]["duration_seconds"]["average"],
            )
            self.assertEqual(14.0, report["slowest_cases"][0]["duration_seconds"])
            self.assertNotIn("filename", report["slowest_cases"][0])

    def test_recognition_metrics_and_runtime_sources_are_aggregated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 08:00:00,000 INFO scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; schema=2; ereignis=processing_completed; "
                "status=erfolgreich; version=0.3.4; gesamt_s=8; erkennung_s=7; "
                "render_s=1; barcode_s=0.5; ocr_s=5; ocr_aufrufe=2; "
                "ocr_pixel=1200000; ocr_max_s=3; "
                "erkennungspfade=barcode:1,lieferantenkopf_klein:1,neuma_kopf:1; "
                "tesseract_quelle=anwendungsverzeichnis\n",
            )

            report = build_diagnostic_report(directory, days=7, end_date=self.report_day)

            metrics = report["summary"]["recognition_diagnostics"]
            self.assertEqual(1.0, metrics["render_seconds"]["average"])
            self.assertEqual(2.0, metrics["ocr_calls"]["average"])
            self.assertEqual(1_200_000.0, metrics["ocr_pixels"]["maximum"])
            self.assertEqual(1, metrics["recognition_path_usage"]["neuma_kopf"])
            source = report["grouped_by_tesseract_source"]["anwendungsverzeichnis"]
            self.assertEqual(5.0, source["ocr_seconds"]["average"])
            self.assertEqual(2.0, source["ocr_calls"]["average"])
            self.assertEqual({}, report["parser_statistics"]["unknown_field_names"])
            html = render_diagnostic_html(report)
            self.assertIn("Ø Laufzeit (s)", html)
            self.assertIn("OCR-Laufzeitquellen", html)

    def test_statistics_cover_empty_single_median_p95_and_maximum(self) -> None:
        self.assertEqual(0, _statistics([])["count"])
        self.assertIsNone(_statistics([])["median"])
        self.assertEqual(
            {
                "count": 1,
                "average": 4.0,
                "median": 4.0,
                "p95": 4.0,
                "maximum": 4.0,
            },
            _statistics([4]),
        )
        values = list(range(1, 21))
        result = _statistics(values)
        self.assertEqual(10.5, result["median"])
        self.assertEqual(19.0, result["p95"])
        self.assertEqual(20.0, result["maximum"])

    def test_default_report_contains_neither_filename_nor_full_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 08:00:00,000 WARNING scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; status=nicht_erkannt; version=0.3.0; "
                "grundcode=kein_text; stufe=ocr; seite=1; "
                "datei=C:\\Geheim\\kunde.pdf; gesamt_s=3.0; "
                "tesseract_quelle=C:\\Geheim\\tesseract.exe; "
                "erkennungspfade=C:\\Geheim:1\n",
            )

            report = build_diagnostic_report(
                directory, days=7, end_date=self.report_day
            )
            serialized = json.dumps(report, ensure_ascii=False)

            self.assertNotIn("kunde.pdf", serialized)
            self.assertNotIn("Geheim", serialized)
            self.assertIn('"tesseract_source": "unbekannt"', serialized)
            self.assertNotIn("filename", report["problem_cases"][0])
            self.assertFalse(report["privacy"]["filenames_included"])
            self.assertFalse(report["privacy"]["full_paths_included"])

    def test_opt_in_includes_basename_only_and_html_escapes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_log(
                directory,
                "2026-08-19 08:00:00,000 WARNING scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; status=nicht_erkannt; grundcode=kein_text; "
                "datei=C:\\Geheim\\<script>.pdf\n",
            )

            report = build_diagnostic_report(
                directory,
                days=7,
                include_filenames=True,
                end_date=self.report_day,
            )
            html = render_diagnostic_html(report)

            self.assertEqual("<script>.pdf", report["problem_cases"][0]["filename"])
            self.assertNotIn("Geheim", json.dumps(report))
            self.assertNotIn("<script>.pdf", html)
            self.assertIn("&lt;script&gt;.pdf", html)
            self.assertNotIn("<script", html.lower())

    def test_export_zip_has_only_html_and_json_with_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log_directory = Path(directory) / "logs"
            log_directory.mkdir()
            self._write_log(str(log_directory), "")
            destination = Path(directory) / "bericht.zip"

            result = export_diagnostic_report(
                log_directory,
                destination,
                days=7,
                end_date=self.report_day,
                created_at=datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
            )

            self.assertEqual(destination, result)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(
                    {"diagnosebericht.html", "diagnosebericht.json"},
                    set(archive.namelist()),
                )
                report = json.loads(archive.read("diagnosebericht.json"))
                self.assertEqual(3, report["schema_version"])
                self.assertEqual(7, report["report_period"]["days"])
                self.assertIn("<!doctype html>", archive.read("diagnosebericht.html").decode())

    def test_failed_export_preserves_existing_file_and_removes_temporary_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "bericht.zip"
            destination.write_bytes(b"vorhanden")
            with patch(
                "scanner_sorter.diagnostics.zipfile.ZipFile",
                side_effect=OSError("Schreibfehler"),
            ):
                with self.assertRaises(OSError):
                    export_diagnostic_report(
                        directory,
                        destination,
                        days=7,
                        end_date=self.report_day,
                    )

            self.assertEqual(b"vorhanden", destination.read_bytes())
            self.assertEqual([destination], list(Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
