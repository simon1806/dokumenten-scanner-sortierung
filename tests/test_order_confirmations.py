from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw
from pypdf import PdfWriter

from scanner_sorter.config import Settings
from scanner_sorter.diagnostics import build_diagnostic_report, render_diagnostic_html
from scanner_sorter.models import DetectedDocument
from scanner_sorter.processing import DocumentProcessor, RecognitionGroupingError
from scanner_sorter.recognition import (
    PageRecognizer,
    detect_document_from_text,
    has_supported_document_signal,
    order_confirmation_number_from_text,
    unrecognised_document_reason,
)


HEADER = "Glas Hagen www.glashagen.de Auftragsbestätigung Nr. 3260829"
FORM = (
    "Bitte überprüfen Sie die Angaben und lassen Sie uns die Kopie der Auftragsbestätigung, "
    "von Ihnen unterschrieben, zukommen. Ort: ______, den ______ x ______"
)


class OrderConfirmationTests(unittest.TestCase):
    def test_unsigned_au_is_not_attached_to_signed_offer_in_either_scan_order(self) -> None:
        for au_index, preceding_type in ((i, kind) for i in (0, 1) for kind in ("AG", "EM", "LS")):
            with self.subTest(au_index=au_index, other=preceding_type), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "batch.pdf"
                writer = PdfWriter()
                for _ in range(2):
                    writer.add_blank_page(width=595, height=842)
                with source.open("wb") as stream:
                    writer.write(stream)
                processor = DocumentProcessor(Settings())
                recognizer = processor.recognizer

                def recognise_page(_source: Path, index: int):
                    if index != au_index:
                        return DetectedDocument(preceding_type, "5260615_UNTERS")
                    page = SimpleNamespace(number=index)
                    recognizer._remember_order_confirmation_header(page, HEADER)
                    return recognizer._unrecognised_page(page, "auftragsbestaetigung_ohne_handschrift")

                with patch.object(recognizer, "_recognise_file_page", side_effect=recognise_page):
                    with self.assertRaisesRegex(RecognitionGroupingError, "Auftragsbestätigung"):
                        processor._recognise_groups(source)

    def test_recipient_or_customer_stamp_does_not_establish_the_issuer(self) -> None:
        recognizer = PageRecognizer(Settings())
        foreign = f"Fremder Lieferant GmbH {HEADER} {FORM}"
        page = SimpleNamespace(number=0, get_text=lambda _format: foreign)
        with (
            patch.object(recognizer, "_render", return_value=Image.new("RGB", (1000, 1400), "white")),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(recognizer, "_read_ocr", side_effect=("", foreign, "Fremder Lieferant GmbH")),
            patch.object(recognizer, "_has_signed_order_confirmation_mark", return_value=True),
        ):
            recognizer._start_metrics()
            self.assertIsNone(recognizer.recognise(page))
            recognizer._finish_metrics()

    def test_reads_own_order_number_and_au_filename(self) -> None:
        for spelling in ("Auftragsbestätigung", "Auftragsbestaetigung", "Auftragsbestatigung"):
            with self.subTest(spelling=spelling):
                header = HEADER.replace("Auftragsbestätigung", spelling)
                self.assertEqual("3260829", order_confirmation_number_from_text(header))
                self.assertTrue(has_supported_document_signal(header))
                self.assertIsNone(detect_document_from_text(header))
                detected = detect_document_from_text(f"{header} {FORM}")
                self.assertIsNotNone(detected)
                self.assertEqual("AU_3260829_UNTERS.pdf", detected.filename)
        self.assertEqual(
            DetectedDocument("AU", "3260829_UNTERS"),
            detect_document_from_text(f"{HEADER} {FORM.replace('Auftragsbestätigung,', 'Auftragsbestdtigung,')}"),
        )

    def test_foreign_confirmation_and_customer_address_are_insufficient(self) -> None:
        for text in (
            "Pauli + Sohn Auftragsbestätigung Nr. 3260829 Glas Hagen Kreuzstraße 21",
            "Glas Hagen Auftragsbestätigung Nr. 3260829",
            "Glas Hagen www.glashagen.de Angebot Nr. 3260829",
        ):
            with self.subTest(text=text):
                self.assertIsNone(order_confirmation_number_from_text(text))
                self.assertIsNone(detect_document_from_text(f"{text} {FORM}"))

    def test_missing_number_or_return_block_stays_unrecognised(self) -> None:
        for text in (
            HEADER.replace("3260829", ""),
            HEADER.replace("3260829", "3260829123456"),
            f"{HEADER} Datum / Unterschrift",
        ):
            with self.subTest(text=text):
                self.assertIsNone(detect_document_from_text(text))
        self.assertEqual(
            "belegnummer_nicht_erkannt",
            unrecognised_document_reason(HEADER.replace("3260829", "")),
        )
        self.assertEqual(
            "dokumentart_nicht_unterstuetzt",
            unrecognised_document_reason("Pauli + Sohn Auftragsbestätigung Nummer/Datum 82118445"),
        )

    def test_every_text_route_requires_a_handwritten_customer_mark(self) -> None:
        full = f"{HEADER} {FORM}"
        for route in ("embedded", "header", "targeted", "full_page"):
            for signed in (True, False):
                with self.subTest(route=route, signed=signed):
                    recognizer = PageRecognizer(Settings())
                    embedded = full if route == "embedded" else ""
                    page = SimpleNamespace(number=0, get_text=lambda _format: embedded)
                    texts = {
                        "embedded": ("", "", FORM),
                        "header": ("", full),
                        "targeted": ("", HEADER, FORM),
                        "full_page": ("", HEADER.replace("3260829", ""), full),
                    }[route]
                    with (
                        patch.object(recognizer, "_render", return_value=Image.new("RGB", (1000, 1400), "white")),
                        patch.object(recognizer, "_read_barcodes", return_value=()),
                        patch.object(recognizer, "_read_ocr", side_effect=texts),
                        patch.object(recognizer, "_has_order_confirmation_issuer", return_value=True),
                        patch.object(recognizer, "_has_signed_order_confirmation_mark", return_value=signed) as mark,
                    ):
                        recognizer._start_metrics()
                        detected = recognizer.recognise(page)
                        recognizer._finish_metrics()
                    mark.assert_called_once()
                    if signed:
                        self.assertEqual(DetectedDocument("AU", "3260829_UNTERS"), detected)
                    else:
                        self.assertIsNone(detected)
                        self.assertEqual({0: "auftragsbestaetigung_ohne_handschrift"}, recognizer.last_page_reasons)

    def test_number_alone_cannot_bypass_missing_signature_field(self) -> None:
        recognizer = PageRecognizer(Settings())
        with (
            patch.object(recognizer, "_render", return_value=Image.new("RGB", (1000, 1400), "white")),
            patch.object(recognizer, "_read_barcodes", return_value=()),
            patch.object(recognizer, "_read_ocr", side_effect=("", HEADER, "Zahlungsbedingungen")),
            patch.object(recognizer, "_has_order_confirmation_issuer", return_value=True),
            patch.object(recognizer, "_has_signed_order_confirmation_mark") as mark,
        ):
            recognizer._start_metrics()
            self.assertIsNone(recognizer.recognise(SimpleNamespace(number=0)))
            recognizer._finish_metrics()
        mark.assert_not_called()
        self.assertEqual(
            {0: "auftragsbestaetigung_unterschriftsfeld_nicht_erkannt"}, recognizer.last_page_reasons
        )

    def test_signature_location_follows_ort_den_labels_and_ignores_date_on_left(self) -> None:
        recognizer = PageRecognizer(Settings())
        for line_y in (920, 1176):
            for ink in (None, "blue", "black"):
                with self.subTest(line_y=line_y, ink=ink):
                    image = Image.new("RGB", (1000, 1400), "white")
                    draw = ImageDraw.Draw(image)
                    draw.line((510, line_y, 880, line_y), fill="black", width=2)
                    # A handwritten date and place must not count as a signature.
                    draw.line((120, line_y - 20, 480, line_y - 20), fill="black", width=4)
                    if ink:
                        draw.line(((550, line_y - 15), (610, line_y - 45), (700, line_y - 10)), fill=ink, width=4)
                    words = {
                        "text": ["Ort:", "�den", "x"], "left": [50, 280, 450],
                        "top": [line_y - 14 - 630] * 3, "height": [14] * 3,
                    }
                    with patch.object(recognizer, "_read_ocr_words", return_value=words):
                        self.assertEqual(ink is not None, recognizer._has_signed_order_confirmation_mark(image))

    def test_unanchored_ink_is_not_a_customer_signature(self) -> None:
        recognizer = PageRecognizer(Settings())
        image = Image.new("RGB", (1000, 1400), "black")
        with patch.object(recognizer, "_read_ocr_words", return_value={
            "text": ["Ort:"], "left": [50], "top": [500], "height": [14],
        }):
            self.assertFalse(recognizer._has_signed_order_confirmation_mark(image))

    def test_only_matching_header_pages_inherit_a_signed_order(self) -> None:
        recognizer = PageRecognizer(Settings())
        signed = DetectedDocument("AU", "3260829_UNTERS")
        recognizer._start_metrics()
        for index, header in enumerate((HEADER, HEADER, HEADER.replace("3260829", "3260825"))):
            recognizer._remember_order_confirmation_header(
                SimpleNamespace(number=index), header, issuer_verified=True
            )
        self.assertEqual(
            [signed, signed, None, None],
            recognizer._complete_signed_order_confirmation_pages([None, signed, None, None]),
        )
        self.assertEqual([signed, signed], recognizer._complete_signed_order_confirmation_pages([signed, None]))
        receipt = DetectedDocument("EM", "6260453")
        self.assertEqual([receipt, signed], recognizer._complete_signed_order_confirmation_pages([receipt, signed]))
        recognizer._start_metrics()
        self.assertEqual([None, signed], recognizer._complete_signed_order_confirmation_pages([None, signed]))

    def test_unverified_matching_header_does_not_inherit_a_signed_au(self) -> None:
        recognizer = PageRecognizer(Settings())
        recognizer._start_metrics()
        recognizer._remember_order_confirmation_header(SimpleNamespace(number=0), HEADER)
        signed = DetectedDocument("AU", "3260829_UNTERS")
        self.assertEqual([None, signed], recognizer._complete_signed_order_confirmation_pages([None, signed]))

    def test_issuer_verification_uses_the_upper_right_letterhead_only(self) -> None:
        issuer = "GlasHagen Kreuzstraße 21 45770 Marl www.glashagen.de"
        recognizer = PageRecognizer(Settings())
        for own_logo, own_contacts in ((True, True), (False, True), (True, False), (False, False)):
            with self.subTest(own_logo=own_logo, own_contacts=own_contacts):
                image = Image.new("RGB", (1000, 1400), "white")
                draw = ImageDraw.Draw(image)
                # Recipient and customer-stamp text lies outside the issuer box.
                draw.rectangle((100, 200, 400, 400), fill="black")
                draw.rectangle((550, 1100, 850, 1200), fill="black")
                if own_logo:
                    draw.rectangle((650, 80, 850, 120), fill="black")
                if own_contacts:
                    draw.rectangle((850, 230, 950, 350), fill="black")

                def read_crop(crop):
                    return issuer if crop.convert("L").getextrema()[0] == 0 else ""

                with patch.object(recognizer, "_read_ocr", side_effect=read_crop):
                    self.assertEqual(own_logo and own_contacts, recognizer._has_order_confirmation_issuer(image))

    def test_all_au_candidate_routes_require_a_verified_issuer(self) -> None:
        full = f"{HEADER} {FORM}"
        for route in ("embedded", "header", "targeted", "full_page"):
            with self.subTest(route=route):
                recognizer = PageRecognizer(Settings())
                page = SimpleNamespace(number=0, get_text=lambda _format: full if route == "embedded" else "")
                texts = {
                    "embedded": ("", "", FORM),
                    "header": ("", full),
                    "targeted": ("", HEADER, FORM),
                    "full_page": ("", HEADER.replace("3260829", ""), full),
                }[route]
                with (
                    patch.object(recognizer, "_render", return_value=Image.new("RGB", (1000, 1400), "white")),
                    patch.object(recognizer, "_read_barcodes", return_value=()),
                    patch.object(recognizer, "_read_ocr", side_effect=texts),
                    patch.object(recognizer, "_has_order_confirmation_issuer", return_value=False),
                    patch.object(recognizer, "_has_signed_order_confirmation_mark", return_value=True) as mark,
                ):
                    recognizer._start_metrics()
                    self.assertIsNone(recognizer.recognise(page))
                    recognizer._finish_metrics()
                mark.assert_not_called()
                self.assertEqual({0: "dokumentart_nicht_unterstuetzt"}, recognizer.last_page_reasons)

    def test_unsigned_pages_do_not_become_au_without_a_signed_page(self) -> None:
        recognizer = PageRecognizer(Settings())
        recognizer._remember_order_confirmation_header(SimpleNamespace(number=0), HEADER)
        self.assertEqual([None, None], recognizer._complete_signed_order_confirmation_pages([None, None]))

    def test_diagnostics_preserve_au_path_and_unsigned_reason(self) -> None:
        self.assertEqual(
            {"auftragsbestaetigung": 1},
            DocumentProcessor._normalise_recognition_metrics({
                "recognition_paths": {"auftragsbestaetigung": 1}
            })["recognition_paths"],
        )
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "dokumentensortierer-2026-11-11.log"
            log.write_text(
                "2026-11-11 08:00:00,000 WARNING scanner_sorter.processing [worker]: "
                "Vorgang abgeschlossen; schema=2; ereignis=processing_completed; version=0.4.2; "
                "status=nicht_erkannt; grundcode=auftragsbestaetigung_ohne_handschrift; "
                "erkennungspfade=auftragsbestaetigung:1; gesamt_s=2.0\n",
                encoding="utf-8",
            )
            report = build_diagnostic_report(directory, days=7, end_date=date(2026, 11, 11))
        self.assertEqual(1, report["summary"]["recognition_diagnostics"]["recognition_path_usage"]["auftragsbestaetigung"])
        self.assertIn("Auftragsbestätigung ohne erkennbare handschriftliche Unterschrift", render_diagnostic_html(report))


if __name__ == "__main__":
    unittest.main()
