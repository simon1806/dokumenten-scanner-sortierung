from __future__ import annotations

import unittest
from unittest.mock import patch

from scanner_sorter.config import Settings
from scanner_sorter.recognition import (
    MAX_RENDER_PIXELS,
    OCR_TIMEOUT_SECONDS,
    PageRecognizer,
    detect_document_from_text,
    has_supported_document_signal,
    is_assignment_declaration,
    is_nowak_header,
)


class RecognitionTests(unittest.TestCase):
    @patch("pytesseract.image_to_string", return_value="Montagebericht Auftrag: 3260551")
    @patch("scanner_sorter.recognition.find_tesseract_executable", return_value=None)
    def test_ocr_uses_server_timeout(self, _mock_find: object, image_to_string: object) -> None:
        recognizer = PageRecognizer(Settings())

        recognizer._read_ocr(object())

        self.assertEqual(OCR_TIMEOUT_SECONDS, image_to_string.call_args.kwargs["timeout"])

    def test_render_rejects_unusually_large_page(self) -> None:
        class Rect:
            width = MAX_RENDER_PIXELS
            height = 2

        class Page:
            rect = Rect()

            @staticmethod
            def get_pixmap(**_kwargs: object) -> object:
                raise AssertionError("Eine zu grosse Seite darf nicht gerendert werden.")

        with self.assertRaisesRegex(RuntimeError, "Render-Limit"):
            PageRecognizer._render(Page())

    def test_typed_barcode_skips_slow_ocr(self) -> None:
        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=object()),
            patch.object(recognizer, "_read_barcodes", return_value=("AM_3250672",)),
            patch.object(recognizer, "_read_ocr", side_effect=AssertionError("OCR darf nicht laufen")),
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("AM_3250672.pdf", detected.filename)

    def test_code39_barcode_with_padding_and_check_character_skips_ocr(self) -> None:
        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=object()),
            patch.object(recognizer, "_read_barcodes", return_value=("EM-06260367G",)),
            patch.object(recognizer, "_read_ocr", side_effect=AssertionError("OCR darf nicht laufen")),
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("EM_6260367.pdf", detected.filename)

    def test_header_ocr_skips_full_page_ocr_when_document_is_detected(self) -> None:
        crop_boxes: list[tuple[int, int, int, int]] = []

        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        class ScanImage:
            size = (1000, 1400)

            @staticmethod
            def crop(box: tuple[int, int, int, int]) -> object:
                crop_boxes.append(box)
                return "Kopfbereich"

        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=ScanImage()),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(
                recognizer,
                "_read_ocr",
                side_effect=("Kein Nowak-Lieferschein", "Montagebericht Auftrag: 3260635"),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("MI_3260635.pdf", detected.filename)
        self.assertEqual([(390, 35, 750, 287), (0, 0, 1000, 490)], crop_boxes)
        self.assertEqual(("Kopfbereich",), read_ocr.call_args_list[1].args)

    def test_montage_fast_area_skips_large_header_ocr(self) -> None:
        crop_boxes: list[tuple[int, int, int, int]] = []

        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        class ScanImage:
            size = (1000, 1400)

            @staticmethod
            def crop(box: tuple[int, int, int, int]) -> object:
                crop_boxes.append(box)
                return ("Ausschnitt", box)

        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=ScanImage()),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(
                recognizer,
                "_read_ocr",
                side_effect=("Auftrag: 3260455", "Montagebericht Auftrag: 3260455"),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("MI_3260455.pdf", detected.filename)
        self.assertEqual([(390, 35, 750, 287), (0, 28, 1000, 336)], crop_boxes)
        self.assertEqual(2, read_ocr.call_count)

    def test_full_page_ocr_remains_fallback_after_unsuccessful_header(self) -> None:
        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        class ScanImage:
            size = (1000, 1400)

            @staticmethod
            def crop(_box: tuple[int, int, int, int]) -> object:
                return "Kopfbereich"

        image = ScanImage()
        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=image),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(
                recognizer,
                "_read_ocr",
                side_effect=(
                    "Kein Nowak-Lieferschein",
                    "Empfangsschein ohne lesbare Nummer",
                    "Empfangsschein-Nr. 6260367",
                ),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("EM_6260367.pdf", detected.filename)
        self.assertEqual(("Kopfbereich",), read_ocr.call_args_list[1].args)
        self.assertEqual((image,), read_ocr.call_args_list[2].args)

    def test_unknown_header_skips_full_page_ocr(self) -> None:
        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        class ScanImage:
            size = (1000, 1400)

            @staticmethod
            def crop(box: tuple[int, int, int, int]) -> object:
                return ("Ausschnitt", box)

        image = ScanImage()
        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=image),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(
                recognizer,
                "_read_ocr",
                side_effect=("Unbekannt", "Rechnung eines fremden Lieferanten"),
            ) as read_ocr,
            self.assertLogs("scanner_sorter.recognition", level="INFO") as captured,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNone(detected)
        self.assertEqual(2, read_ocr.call_count)
        self.assertIn("Ganzseiten-OCR uebersprungen", "\n".join(captured.output))

    def test_assignment_declaration_reads_targeted_order_field(self) -> None:
        crop_boxes: list[tuple[int, int, int, int]] = []

        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        class ScanImage:
            size = (1000, 1400)

            @staticmethod
            def crop(box: tuple[int, int, int, int]) -> object:
                crop_boxes.append(box)
                return ("Ausschnitt", box)

        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=ScanImage()),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(
                recognizer,
                "_read_ocr",
                side_effect=(
                    "Kein Nowak-Lieferschein",
                    "Abtretungserklaerung bei Versicherungsschaeden",
                    "Auftrag/Angebot 3260569",
                ),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("ABTRET_3260569.pdf", detected.filename)
        self.assertEqual([(390, 35, 750, 287), (0, 0, 1000, 490), (80, 602, 780, 938)], crop_boxes)
        self.assertEqual(3, read_ocr.call_count)

    def test_aufmassblatt(self) -> None:
        detected = detect_document_from_text("AUFMASSBLATT 3250672\nKunden-Nummer 11959", ["3250672"])
        self.assertIsNotNone(detected)
        self.assertEqual("AM_3250672.pdf", detected.filename)

    def test_empfangsschein(self) -> None:
        detected = detect_document_from_text("Empfangsschein-Nr. 6260347\nzu Auftrag 3260551")
        self.assertIsNotNone(detected)
        self.assertEqual("EM_6260347.pdf", detected.filename)

    def test_empfangsschein_accepts_ocr_spaces_around_dash(self) -> None:
        detected = detect_document_from_text("EMPFANGSSCHEIN - NR. 6260347\nGlas Hagen")
        self.assertIsNotNone(detected)
        self.assertEqual("EM_6260347.pdf", detected.filename)

    def test_montagebericht(self) -> None:
        detected = detect_document_from_text("Montagebericht Auftrag: 3260551 [MI-Nr. 1]")
        self.assertIsNotNone(detected)
        self.assertEqual("MI_3260551.pdf", detected.filename)

    def test_assignment_declaration(self) -> None:
        detected = detect_document_from_text(
            "Abtretungserklaerung bei Versicherungsschaeden\nAuftrag / Angebot 3260569"
        )
        self.assertIsNotNone(detected)
        self.assertEqual("ABTRET_3260569.pdf", detected.filename)

    def test_assignment_declaration_accepts_52_prefix(self) -> None:
        detected = detect_document_from_text(
            "Abtretungserklaerung bei Versicherungsschaeden\nAuftrag/Angebot: 5212345"
        )
        self.assertIsNotNone(detected)
        self.assertEqual("ABTRET_5212345.pdf", detected.filename)

    def test_assignment_declaration_accepts_scanner_ocr_for_auftrag_label(self) -> None:
        detected = detect_document_from_text(
            "Abtretungserkldrung bei Versicherungsschaden\nAuttrag/Angebot 3260569"
        )
        self.assertIsNotNone(detected)
        self.assertEqual("ABTRET_3260569.pdf", detected.filename)

    def test_assignment_declaration_requires_document_type_and_expected_number_prefix(self) -> None:
        self.assertTrue(is_assignment_declaration("Abtretungserklaerung bei Versicherungsschaeden"))
        self.assertTrue(is_assignment_declaration("Abtretungserkldrung bei Versicherungsschaden"))
        self.assertTrue(has_supported_document_signal("Abtretungserklaerung bei Versicherungsschaeden"))
        self.assertIsNone(detect_document_from_text("Auftrag/Angebot 3260569"))
        self.assertIsNone(
            detect_document_from_text(
                "Abtretungserklaerung bei Versicherungsschaeden\nAuftrag/Angebot 6260569"
            )
        )

    def test_nowak_delivery_note_keeps_complete_number(self) -> None:
        detected = detect_document_from_text("NOWAK GLAS\nLIEFERSCHEIN 4783804")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4783804.pdf", detected.filename)

    def test_nowak_delivery_note_without_lieferschein_word(self) -> None:
        detected = detect_document_from_text("NOWAK GLAS\nFirma Inh. Andreas Hagen 4783804 Kreuzstrasse")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4783804.pdf", detected.filename)

    def test_nowak_delivery_note_accepts_previous_46_prefix(self) -> None:
        detected = detect_document_from_text("NOWAK GLAS\nLIEFERSCHEIN 4683804")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4683804.pdf", detected.filename)

    def test_nowak_delivery_note_accepts_future_48_prefix(self) -> None:
        detected = detect_document_from_text("Glas-Nowak Marl GmbH\nLIEFERSCHEIN 4883804")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4883804.pdf", detected.filename)

    def test_nowak_ocr_contact_signature_tolerates_imperfect_logo(self) -> None:
        detected = detect_document_from_text(
            "x Nowak Gis\nTel: 02365/60686-0\nLIEFERSCHEIN\n4783804"
        )
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4783804.pdf", detected.filename)

    def test_nowak_header_requires_name_or_stable_contact_signature(self) -> None:
        self.assertTrue(is_nowak_header("NOWAK GLAS"))
        self.assertTrue(is_nowak_header("LIEFERSCHEIN 4783804 TEL: 02365/60686-0"))
        self.assertFalse(is_nowak_header("LIEFERSCHEIN 4783804"))

    def test_supported_document_signal_does_not_accept_generic_lieferschein(self) -> None:
        self.assertTrue(has_supported_document_signal("Montageinfo ohne Auftragsnummer"))
        self.assertTrue(has_supported_document_signal("Heitzer Lieferschein"))
        self.assertFalse(has_supported_document_signal("Lieferschein eines unbekannten Lieferanten"))

    def test_numeric_barcode_without_nowak_signature_is_not_a_nowak_document(self) -> None:
        detected = detect_document_from_text("Unbekannter Lieferant", ["4783804"])
        self.assertIsNone(detected)

    def test_nowak_fast_area_skips_large_ocr_regions(self) -> None:
        class ScanPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return ""

        class ScanImage:
            size = (1000, 1400)

            @staticmethod
            def crop(box: tuple[int, int, int, int]) -> object:
                return ("Ausschnitt", box)

        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=ScanImage()),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(
                recognizer,
                "_read_ocr",
                return_value="Tel: 02365/60686-0 LIEFERSCHEIN 4883804",
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4883804.pdf", detected.filename)
        read_ocr.assert_called_once_with(("Ausschnitt", (390, 35, 750, 287)))

    def test_heitzer_delivery_note(self) -> None:
        detected = detect_document_from_text("Heitzer AG\nLIEFERSCHEIN 26060887 vom 16.06.2026")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Heitzer-26060887.pdf", detected.filename)

    def test_pauli_delivery_note(self) -> None:
        detected = detect_document_from_text(
            "Pauli+ Sohn GmbH-Metallwaren\nLieferschein\nNummer/Datum: 82079358 vom 24.02.2026"
        )
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Pauli-82079358.pdf", detected.filename)


if __name__ == "__main__":
    unittest.main()
