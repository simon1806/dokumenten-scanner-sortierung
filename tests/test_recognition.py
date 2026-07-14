from __future__ import annotations

import unittest

from scanner_sorter.recognition import detect_document_from_text


class RecognitionTests(unittest.TestCase):
    def test_aufmassblatt(self) -> None:
        detected = detect_document_from_text("AUFMASSBLATT 3250672\nKunden-Nummer 11959", ["3250672"])
        self.assertIsNotNone(detected)
        self.assertEqual("AM_3250672.pdf", detected.filename)

    def test_empfangsschein(self) -> None:
        detected = detect_document_from_text("Empfangsschein-Nr. 6260347\nzu Auftrag 3260551")
        self.assertIsNotNone(detected)
        self.assertEqual("EM_6260347.pdf", detected.filename)

    def test_montagebericht(self) -> None:
        detected = detect_document_from_text("Montagebericht Auftrag: 3260551 [MI-Nr. 1]")
        self.assertIsNotNone(detected)
        self.assertEqual("MI_3260551.pdf", detected.filename)

    def test_nowak_delivery_note_keeps_complete_number(self) -> None:
        detected = detect_document_from_text("NOWAK GLAS\nLIEFERSCHEIN 4783804")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4783804.pdf", detected.filename)

    def test_nowak_delivery_note_without_lieferschein_word(self) -> None:
        detected = detect_document_from_text("NOWAK GLAS\nFirma Inh. Andreas Hagen 4783804 Kreuzstrasse")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Nowak-4783804.pdf", detected.filename)

    def test_heitzer_delivery_note(self) -> None:
        detected = detect_document_from_text("Heitzer AG\nLIEFERSCHEIN 26060887 vom 16.06.2026")
        self.assertIsNotNone(detected)
        self.assertEqual("LS-Heitzer-26060887.pdf", detected.filename)


if __name__ == "__main__":
    unittest.main()
