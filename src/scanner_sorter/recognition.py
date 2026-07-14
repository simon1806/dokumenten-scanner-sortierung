from __future__ import annotations

import io
import re
import unicodedata
from typing import Iterable

from .config import Settings
from .models import DetectedDocument

NUMBER = r"(\d{6,12})"


def normalise(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_diacritics = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_diacritics.upper())


def extract_number(text: str, expression: str, barcodes: Iterable[str]) -> str | None:
    match = re.search(expression, text, flags=re.IGNORECASE)
    if match:
        return match.group(1)

    for barcode in barcodes:
        barcode = barcode.strip()
        if re.fullmatch(NUMBER, barcode):
            return barcode
    return None


def detect_document_from_text(text: str, barcodes: Iterable[str] = ()) -> DetectedDocument | None:
    """Recognise the supported document headers from OCR text and barcode values."""
    normalised = normalise(text)
    barcode_values = tuple(barcodes)

    if "NOWAK GLAS" in normalised and "LIEFERSCHEIN" in normalised:
        number = extract_number(normalised, rf"LIEFERSCHEIN\s*(?:NR\.?\s*)?{NUMBER}", barcode_values)
        if number:
            return DetectedDocument("LS", number, "Nowak")

    if "HEITZER AG" in normalised and "LIEFERSCHEIN" in normalised:
        number = extract_number(normalised, rf"LIEFERSCHEIN\s*(?:NR\.?\s*)?{NUMBER}", barcode_values)
        if number:
            return DetectedDocument("LS", number, "Heitzer")

    if "EMPFANGSSCHEIN" in normalised:
        number = extract_number(
            normalised,
            rf"EMPFANGSSCHEIN\s*(?:[- ]?NR\.?)?\s*{NUMBER}",
            barcode_values,
        )
        if number:
            return DetectedDocument("EM", number)

    if "MONTAGEBERICHT" in normalised or "MONTAGEINFO" in normalised:
        number = extract_number(normalised, rf"AUFTRAG\s*(?:NR\.?)?\s*:\s*{NUMBER}", barcode_values)
        if number:
            return DetectedDocument("MI", number)

    if "AUFMASSBLATT" in normalised or "AUFMASS SCHEIN" in normalised:
        number = extract_number(
            normalised,
            rf"AUFMASS(?:BLATT| SCHEIN)\s*(?:[- ]?NR\.?)?\s*{NUMBER}",
            barcode_values,
        )
        if number:
            return DetectedDocument("AM", number)

    for barcode in barcode_values:
        match = re.fullmatch(r"(AM|EM|MI)[-_]?(\d{6,12})", barcode.strip(), flags=re.IGNORECASE)
        if match:
            return DetectedDocument(match.group(1).upper(), match.group(2))
    return None


class PageRecognizer:
    """Renders a page, reads its barcodes and uses OCR as a fallback."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def recognise(self, page: object) -> DetectedDocument | None:
        image = self._render(page)
        barcodes = self._read_barcodes(image)
        text = self._read_ocr(image)
        return detect_document_from_text(text, barcodes)

    @staticmethod
    def _render(page: object):
        pixmap = page.get_pixmap(matrix=__import__("fitz").Matrix(2.5, 2.5), alpha=False)
        from PIL import Image

        return Image.open(io.BytesIO(pixmap.tobytes("png")))

    @staticmethod
    def _read_barcodes(image: object) -> tuple[str, ...]:
        try:
            import zxingcpp

            return tuple(result.text.strip() for result in zxingcpp.read_barcodes(image) if result.text.strip())
        except Exception:
            # OCR remains available when a page contains no readable barcode.
            return ()

    def _read_ocr(self, image: object) -> str:
        try:
            import pytesseract
        except ImportError as error:  # pragma: no cover - dependency check at runtime
            raise RuntimeError("Die OCR-Abhängigkeit pytesseract ist nicht installiert.") from error

        if self.settings.tesseract_path.strip():
            pytesseract.pytesseract.tesseract_cmd = self.settings.tesseract_path

        languages = [language.strip() for language in self.settings.ocr_languages.split(",") if language.strip()]
        if "eng" not in languages:
            languages.append("eng")

        last_error: Exception | None = None
        for language in languages:
            try:
                return pytesseract.image_to_string(image, lang=language, config="--psm 6")
            except pytesseract.TesseractError as error:
                last_error = error
        raise RuntimeError("Tesseract OCR konnte nicht gestartet werden.") from last_error
