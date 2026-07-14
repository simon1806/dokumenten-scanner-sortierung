from __future__ import annotations

import io
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from .config import Settings, find_tesseract_executable
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

    if "NOWAK GLAS" in normalised:
        number = extract_number(
            normalised,
            r"(?:LIEFERSCHEIN\s*(?:NR\.?\s*)?)?\b(47\d{5,10})\b",
            barcode_values,
        )
        if number:
            return DetectedDocument("LS", number, "Nowak")

    if "HEITZER AG" in normalised and "LIEFERSCHEIN" in normalised:
        number = extract_number(normalised, rf"LIEFERSCHEIN\s*(?:NR\.?\s*)?{NUMBER}", barcode_values)
        if number:
            return DetectedDocument("LS", number, "Heitzer")

    if "PAULI" in normalised and "SOHN" in normalised and "LIEFERSCHEIN" in normalised:
        number = extract_number(
            normalised,
            rf"(?:NUMMER\s*/\s*DATUM|BELEG[- ]?NR\.?\s*/\s*DATUM)\s*:?\s*{NUMBER}",
            barcode_values,
        )
        if number:
            return DetectedDocument("LS", number, "Pauli")

    if "EMPFANGSSCHEIN" in normalised:
        number = extract_number(
            normalised,
            rf"EMPFANGSSCHEIN\s*(?:[-–—]\s*)?(?:NR\.?\s*)?(?::\s*)?{NUMBER}",
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

    def recognise_document(self, source: Path) -> list[DetectedDocument | None]:
        """Recognise pages in order while allowing two OCR processes to work concurrently."""
        import fitz

        with fitz.open(source) as document:
            page_count = document.page_count
        if page_count == 0:
            return []
        if page_count == 1:
            return [self._recognise_file_page(source, 0)]

        with ThreadPoolExecutor(max_workers=min(2, page_count), thread_name_prefix="ocr") as executor:
            return list(executor.map(lambda index: self._recognise_file_page(source, index), range(page_count)))

    def _recognise_file_page(self, source: Path, page_index: int) -> DetectedDocument | None:
        import fitz

        with fitz.open(source) as document:
            return self.recognise(document.load_page(page_index))

    def recognise(self, page: object) -> DetectedDocument | None:
        embedded_text = ""
        try:
            embedded_text = page.get_text("text")
        except Exception:
            # Reine Bildscans besitzen üblicherweise keine eingebettete Textebene.
            pass

        if embedded_text:
            detected = detect_document_from_text(embedded_text)
            if detected:
                return detected

        image = self._render(page)
        barcodes = self._read_barcodes(image)
        detected = detect_document_from_text(embedded_text, barcodes)
        if detected:
            return detected

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

        tesseract_path = find_tesseract_executable(self.settings.tesseract_path)
        if tesseract_path:
            pytesseract.pytesseract.tesseract_cmd = str(tesseract_path)
        elif self.settings.tesseract_path.strip():
            raise RuntimeError("Der eingetragene Tesseract-Pfad wurde nicht gefunden.")

        languages = [language.strip() for language in self.settings.ocr_languages.split(",") if language.strip()]
        if "eng" not in languages:
            languages.append("eng")

        last_error: Exception | None = None
        for language in languages:
            try:
                return pytesseract.image_to_string(image, lang=language, config="--psm 6")
            except pytesseract.TesseractNotFoundError as error:
                raise RuntimeError(
                    "Tesseract OCR ist nicht installiert oder wurde nicht mit der Anwendung gefunden."
                ) from error
            except pytesseract.TesseractError as error:
                last_error = error
        raise RuntimeError("Tesseract OCR konnte nicht gestartet werden.") from last_error
