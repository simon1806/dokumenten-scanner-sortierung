from __future__ import annotations

import unittest

from scanner_sorter.models import DetectedDocument
from scanner_sorter.processing import DocumentProcessor, ProcessingError, group_page_detections


class GroupingTests(unittest.TestCase):
    def test_continuation_page_stays_with_document(self) -> None:
        nowak = DetectedDocument("LS", "4781776", "Nowak")
        heitzer = DetectedDocument("LS", "26060887", "Heitzer")

        groups = group_page_detections([nowak, None, heitzer])

        self.assertEqual([[0, 1], [2]], [group.page_indexes for group in groups])

    def test_each_montageinfo_starts_a_new_document_even_with_same_order(self) -> None:
        montageinfo = DetectedDocument("MI", "3260558")

        groups = group_page_detections([montageinfo, montageinfo])

        self.assertEqual([[0], [1]], [group.page_indexes for group in groups])

    def test_first_page_must_be_recognised(self) -> None:
        with self.assertRaises(ProcessingError) as captured:
            group_page_detections([None, DetectedDocument("AM", "3250672")])

        self.assertEqual("dokumenttyp_nicht_erkannt", captured.exception.reason_code)
        self.assertEqual("seitenerkennung", captured.exception.stage)
        self.assertEqual(1, captured.exception.page_number)


class StructuredFailureLogTests(unittest.TestCase):
    def test_ocr_timeout_receives_stable_log_fields(self) -> None:
        details = DocumentProcessor._recognition_failure_details(
            RuntimeError("Tesseract OCR hat das Zeitlimit überschritten.")
        )

        self.assertEqual(("ocr_zeitlimit", "ocr", None), details)

    def test_free_text_reason_cannot_break_structured_log_line(self) -> None:
        value = DocumentProcessor._safe_log_field("erste Zeile; zweites Feld\r\ndritte Zeile")

        self.assertEqual("erste Zeile zweites Feld dritte Zeile", value)


if __name__ == "__main__":
    unittest.main()
