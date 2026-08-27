from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from scanner_sorter.config import Settings
from scanner_sorter.models import DetectedDocument
from scanner_sorter.recognition import (
    MAX_RENDER_PIXELS,
    OCR_TIMEOUT_SECONDS,
    PageRecognizer,
    complete_signed_offer_pages,
    detect_document_from_text,
    has_supported_document_signal,
    is_assignment_declaration,
    is_bohle_header,
    is_neuma_order,
    is_nowak_header,
    is_pauli_measurement_attachment,
    is_signed_offer,
    offer_number_from_text,
    scan_date_from_source,
)


class RecognitionTests(unittest.TestCase):
    @patch("pytesseract.image_to_string", return_value="Montagebericht Auftrag: 3260551")
    @patch("scanner_sorter.recognition.find_tesseract_executable", return_value=None)
    def test_ocr_uses_server_timeout(self, _mock_find: object, image_to_string: object) -> None:
        recognizer = PageRecognizer(Settings())

        recognizer._read_ocr(object())

        self.assertEqual(OCR_TIMEOUT_SECONDS, image_to_string.call_args.kwargs["timeout"])

    def test_ocr_timeout_is_limited_by_document_deadline(self) -> None:
        recognizer = PageRecognizer(Settings(processing_timeout_seconds=90))
        with patch("scanner_sorter.recognition.time.monotonic", return_value=100.2):
            recognizer._processing_deadline = 108.0
            self.assertEqual(8, recognizer._remaining_ocr_seconds())

    def test_ocr_deadline_rejects_late_work(self) -> None:
        recognizer = PageRecognizer(Settings(processing_timeout_seconds=90))
        with patch("scanner_sorter.recognition.time.monotonic", return_value=101.0):
            recognizer._processing_deadline = 101.0
            with self.assertRaisesRegex(RuntimeError, "OCR-Gesamtzeitlimit"):
                recognizer._remaining_ocr_seconds()

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

    def test_code39_barcode_with_numeric_check_character_skips_ocr(self) -> None:
        detected = detect_document_from_text("", ("EM-062604004",))

        self.assertIsNotNone(detected)
        self.assertEqual("EM_6260400.pdf", detected.filename)

    def test_code39_barcode_with_invalid_check_character_is_rejected(self) -> None:
        detected = detect_document_from_text("", ("EM-062604005",))

        self.assertIsNone(detected)

    @patch("zxingcpp.read_barcodes")
    def test_unreadable_full_page_barcode_retries_enlarged_top_left_area(self, read_barcodes: object) -> None:
        crop_boxes: list[tuple[int, int, int, int]] = []
        resize_sizes: list[tuple[int, int]] = []

        class BarcodeArea:
            width = 450
            height = 224

            @staticmethod
            def resize(size: tuple[int, int]) -> str:
                resize_sizes.append(size)
                return "Vergroesserter Barcode"

        class ScanImage:
            size = (1000, 1400)

            @staticmethod
            def crop(box: tuple[int, int, int, int]) -> BarcodeArea:
                crop_boxes.append(box)
                return BarcodeArea()

        read_barcodes.side_effect = ((), (SimpleNamespace(text="EM-062604059"),))

        values = PageRecognizer._read_barcodes(ScanImage())

        self.assertEqual(("EM-062604059",), values)
        self.assertEqual([(30, 28, 480, 252)], crop_boxes)
        self.assertEqual([(900, 448)], resize_sizes)
        self.assertEqual("Vergroesserter Barcode", read_barcodes.call_args_list[1].args[0])

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
        self.assertEqual([(390, 35, 750, 287), (0, 0, 1000, 630)], crop_boxes)
        self.assertEqual(("Kopfbereich",), read_ocr.call_args_list[1].args)

    def test_header_crop_reaches_lower_empfangsschein_title(self) -> None:
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
                side_effect=("Unbekannter Lieferant", "Empfangsschein-Nr. 6260453"),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("EM_6260453.pdf", detected.filename)
        self.assertEqual([(390, 35, 750, 287), (0, 0, 1000, 630)], crop_boxes)
        self.assertEqual(2, read_ocr.call_count)

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

    def test_montage_fast_area_accepts_known_misread_header_without_large_ocr(self) -> None:
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
                side_effect=("Auftrag: 3260576", "Montageber’cht Auftrag: 3260576"),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("MI_3260576.pdf", detected.filename)
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
        self.assertEqual([(390, 35, 750, 287), (0, 0, 1000, 630), (80, 602, 780, 938)], crop_boxes)
        self.assertEqual(3, read_ocr.call_count)

    def test_signed_offer_reads_targeted_confirmation_area(self) -> None:
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
            patch.object(recognizer, "_has_signed_offer_mark", return_value=True),
            patch.object(
                recognizer,
                "_read_ocr",
                side_effect=(
                    "Kein Nowak-Lieferschein",
                    "Angebot Nr. 5260661 Kunden-Nummer 26054",
                    "Ich erteile Ihnen den Auftrag zur Ausfuehrung der angebotenen Leistung. "
                    "Datum / Unterschrift: 04.08.2026",
                ),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("AG_5260661_UNTERS.pdf", detected.filename)
        self.assertEqual(
            [(390, 35, 750, 287), (0, 0, 1000, 630), (20, 588, 980, 1288)],
            crop_boxes,
        )
        self.assertEqual(3, read_ocr.call_count)

    def test_offer_without_handwritten_mark_is_not_recognised(self) -> None:
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
            patch.object(recognizer, "_has_signed_offer_mark", return_value=False),
            patch.object(
                recognizer,
                "_read_ocr",
                side_effect=(
                    "Kein Nowak-Lieferschein",
                    "Angebot Nr. 5260661 Kunden-Nummer 26054",
                    "Ich erteile Ihnen den Auftrag zur Ausfuehrung der angebotenen Leistung. "
                    "Datum / Unterschrift:",
                ),
            ),
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNone(detected)

    def test_handwritten_offer_mark_is_distinguished_from_empty_signature_line(self) -> None:
        empty = Image.new("RGB", (1000, 1400), "white")
        empty_draw = ImageDraw.Draw(empty)
        empty_draw.line((260, 1120, 600, 1120), fill="black", width=2)

        signed = empty.copy()
        signed_draw = ImageDraw.Draw(signed)
        signed_draw.line(
            ((300, 1100), (380, 1050), (460, 1090), (540, 1035), (700, 1095)),
            fill="blue",
            width=4,
        )

        self.assertFalse(PageRecognizer._has_signed_offer_mark(empty))
        self.assertTrue(PageRecognizer._has_signed_offer_mark(signed))

    def test_unsigned_offer_is_not_recognised(self) -> None:
        detected = detect_document_from_text(
            "Angebot Nr. 5260661\nDatum / Unterschrift\nNoch keine Auftragserteilung"
        )

        self.assertIsNone(detected)

    def test_signed_offer_accepts_known_ocr_variants(self) -> None:
        text = (
            "Angebot Nr. 5250798\n"
            "Ich erteile Innen den Auftrag zur Ausfuehrung der angebotenen Leistung:\n"
            "Datum / Unterschritt: 04.08.26"
        )

        detected = detect_document_from_text(text)

        self.assertTrue(is_signed_offer(text))
        self.assertEqual("5250798", offer_number_from_text(text))
        self.assertIsNotNone(detected)
        self.assertEqual("AG_5250798_UNTERS.pdf", detected.filename)

    def test_signed_offer_detection_is_copied_to_normal_and_reversed_pages(self) -> None:
        signed_offer = detect_document_from_text(
            "Angebot Nr. 5260615\n"
            "Ich erteile Ihnen den Auftrag zur Ausfuehrung der angebotenen Leistung.\n"
            "Datum / Unterschrift"
        )
        self.assertIsNotNone(signed_offer)

        normal_order = complete_signed_offer_pages([None, signed_offer])
        reversed_order = complete_signed_offer_pages([signed_offer, None])

        self.assertEqual([signed_offer, signed_offer], normal_order)
        self.assertEqual([signed_offer, signed_offer], reversed_order)

    def test_signed_offer_does_not_absorb_another_document_type(self) -> None:
        signed_offer = DetectedDocument("AG", "5260615_UNTERS")
        receipt = DetectedDocument("EM", "6260416")

        self.assertEqual(
            [signed_offer, None, receipt],
            complete_signed_offer_pages([signed_offer, None, receipt]),
        )

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

    def test_montagebericht_accepts_known_ocr_apostrophe_for_valid_order(self) -> None:
        detected = detect_document_from_text("Montageber’cht Auftrag: 3260576 [MI-Nr. 1]")
        self.assertIsNotNone(detected)
        self.assertEqual("MI_3260576.pdf", detected.filename)

    def test_montagebericht_without_number_uses_scan_date(self) -> None:
        detected = detect_document_from_text(
            "Montagebericht\nDurchgeführte Arbeiten",
            mi_scan_date="2026-07-22",
        )

        self.assertIsNotNone(detected)
        self.assertEqual("MI_2026-07-22.pdf", detected.filename)

    def test_montagebericht_barcode_still_has_priority_over_scan_date(self) -> None:
        detected = detect_document_from_text(
            "Montagebericht\nDurchgeführte Arbeiten",
            ["MI_3260635"],
            mi_scan_date="2026-07-22",
        )

        self.assertIsNotNone(detected)
        self.assertEqual("MI_3260635.pdf", detected.filename)

    def test_scan_date_is_read_from_konica_scanner_filename(self) -> None:
        source = Path("KM_C250i26072216510.pdf")

        self.assertEqual("2026-07-22", scan_date_from_source(source))

    def test_scan_date_falls_back_to_file_timestamp(self) -> None:
        source = Path("ohne_scanner_datum.pdf")
        timestamp = datetime(2026, 7, 23, 10, 30).timestamp()

        with patch.object(Path, "stat", return_value=type("Stat", (), {"st_mtime": timestamp})()):
            self.assertEqual("2026-07-23", scan_date_from_source(source))

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

    def test_neuma_signed_customer_receipt(self) -> None:
        detected = detect_document_from_text(
            "NEUMA\nNeue Marler Baugesellschaft mbH\nAuftrag I-2026-003443 vom 05.06.2026"
        )
        self.assertIsNotNone(detected)
        self.assertEqual("EM-NEUMA-I-2026-003443.pdf", detected.filename)

    def test_neuma_order_normalises_ocr_variants_of_i(self) -> None:
        for prefix in ("1", "|"):
            text = f"NEUMA\nAuftrag {prefix}-2026-003061 vom 18.05.2026"
            detected = detect_document_from_text(text)
            self.assertIsNotNone(detected)
            self.assertEqual("EM-NEUMA-I-2026-003061.pdf", detected.filename)
            self.assertTrue(is_neuma_order(text))

    def test_neuma_order_requires_customer_signature(self) -> None:
        self.assertIsNone(detect_document_from_text("Auftrag I-2026-003443 vom 05.06.2026"))

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

    def test_pauli_measurement_attachments_are_not_document_starts(self) -> None:
        for text in (
            "Pauli + Sohn GmbH\nFlamea+\nAUFMASS zu Set-Nr. 12-101\n1 / 3",
            "Pauli + Sohn GmbH\nGLASBESTELLUNG zu Set-Nr. 12-204\n3 / 4",
        ):
            with self.subTest(text=text):
                self.assertTrue(is_pauli_measurement_attachment(text))
                self.assertFalse(has_supported_document_signal(text))

        delivery_note = (
            "Pauli + Sohn GmbH\nLieferschein\nNummer/Datum: 82079358\n"
            "Set-Nr. 12-101"
        )
        self.assertFalse(is_pauli_measurement_attachment(delivery_note))
        self.assertTrue(has_supported_document_signal(delivery_note))

    def test_embedded_pauli_measurement_attachment_skips_rendering(self) -> None:
        class TextPage:
            @staticmethod
            def get_text(_mode: str) -> str:
                return "Pauli + Sohn GmbH\nAUFMASS zu Set-Nr. 12-301R"

        recognizer = PageRecognizer(Settings())
        with patch.object(
            recognizer,
            "_render",
            side_effect=AssertionError("Pauli-Anlage darf nicht gerendert werden."),
        ):
            self.assertIsNone(recognizer.recognise(TextPage()))

    def test_scanned_pauli_measurement_attachment_skips_full_page_ocr(self) -> None:
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
                side_effect=(
                    "Pauli + Sohn GmbH",
                    "Flamea+ AUFMASS zu Set-Nr. 12-101",
                ),
            ) as read_ocr,
        ):
            self.assertIsNone(recognizer.recognise(ScanPage()))

        self.assertEqual(2, read_ocr.call_count)

    def test_bohle_delivery_note(self) -> None:
        for text, expected_number in (
            ("Bohle AG\nLieferschein Nummer: 34484", "34484"),
            ("www.bohle.com\nLieferschein: 35224", "35224"),
            ("Lieferschein\nNummer: 12349\nBohle AG", "12349"),
        ):
            with self.subTest(expected_number=expected_number):
                detected = detect_document_from_text(text)
                self.assertIsNotNone(detected)
                self.assertEqual(f"LS-Bohle-{expected_number}.pdf", detected.filename)

    def test_bohle_header_requires_supplier_identity(self) -> None:
        self.assertTrue(is_bohle_header("Bohle AG Dieselstrasse 10"))
        self.assertTrue(is_bohle_header("info@bohle.de www.bohle.com"))
        self.assertIsNone(detect_document_from_text("Lieferschein Nummer: 34484"))

    def test_bohle_fast_area_skips_large_ocr_regions(self) -> None:
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
                side_effect=("Bohle AG", "Lieferschein Nummer: 35224"),
            ) as read_ocr,
        ):
            detected = recognizer.recognise(ScanPage())

        self.assertIsNotNone(detected)
        self.assertEqual("LS-Bohle-35224.pdf", detected.filename)
        self.assertEqual(2, read_ocr.call_count)
        self.assertEqual(
            ("Ausschnitt", (20, 21, 460, 182)),
            read_ocr.call_args_list[1].args[0],
        )


if __name__ == "__main__":
    unittest.main()
