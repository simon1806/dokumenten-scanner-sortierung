from __future__ import annotations

import io
import logging
import math
import re
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Iterable

from .config import Settings, find_tesseract_executable
from .models import DetectedDocument

NUMBER = r"(\d{6,12})"
LOGGER = logging.getLogger(__name__)

# Schutzgrenzen fuer unbeaufsichtigte Serververarbeitung. Uebliche Scanner-PDFs
# liegen weit darunter; auffaellige Dateien werden unveraendert zur Pruefung
# weitergeleitet, statt den einzigen Verarbeitungs-Worker zu blockieren.
MAX_PDF_BYTES = 500 * 1024 * 1024
MAX_PDF_PAGES = 250
MAX_RENDER_PIXELS = 50_000_000
OCR_TIMEOUT_SECONDS = 60


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

    nowak_name = bool(
        re.search(r"\bNOWAK\s+G[A-Z]{1,5}\b", normalised)
        or "GLAS-NOWAK" in normalised
        or "GLAS NOWAK" in normalised
    )
    nowak_contact = "LIEFERSCHEIN" in normalised and "60686" in normalised
    if nowak_name or nowak_contact:
        number = extract_number(
            normalised,
            rf"LIEFERSCHEIN\s*(?:NR\.?\s*)?{NUMBER}",
            barcode_values,
        )
        if not number:
            # Manche OCR-Laeufe erkennen das Wort "Lieferschein" nicht, lesen
            # die sieben- bis zwoelfstellige Belegnummer unter dem Nowak-Kopf
            # aber korrekt. Kurze Kunden- und Routennummern bleiben unberuehrt.
            match = re.search(r"\b(\d{7,12})\b", normalised)
            number = match.group(1) if match else None
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
        match = re.fullmatch(r"(AM|EM|MI)[-_]?(\d{6,12})(?:[A-Z])?", barcode.strip(), flags=re.IGNORECASE)
        if match:
            number = match.group(2)
            if len(number) == 8 and number.startswith("0"):
                number = number[1:]
            return DetectedDocument(match.group(1).upper(), number)
    return None


class PageRecognizer:
    """Renders a page, reads its barcodes and uses OCR as a fallback."""

    def __init__(self, settings: Settings):
        self.settings = settings

    def recognise_document(self, source: Path) -> list[DetectedDocument | None]:
        """Recognise pages in order while allowing two OCR processes to work concurrently."""
        import fitz

        source_size = source.stat().st_size
        if source_size > MAX_PDF_BYTES:
            raise RuntimeError(
                f"PDF ist mit {source_size / (1024 * 1024):.1f} MB groesser als das erlaubte Limit "
                f"von {MAX_PDF_BYTES // (1024 * 1024)} MB."
            )

        with fitz.open(source) as document:
            page_count = document.page_count
        if page_count == 0:
            return []
        if page_count > MAX_PDF_PAGES:
            raise RuntimeError(
                f"PDF hat {page_count} Seiten und ueberschreitet das erlaubte Limit von "
                f"{MAX_PDF_PAGES} Seiten."
            )
        if page_count == 1:
            return [self._recognise_file_page(source, 0)]

        executor = ThreadPoolExecutor(max_workers=min(2, page_count), thread_name_prefix="ocr")
        futures: list[Future[DetectedDocument | None]] = []
        try:
            futures = [
                executor.submit(self._recognise_file_page, source, page_index)
                for page_index in range(page_count)
            ]
            # Read results in page order. If one page fails, queued pages are
            # cancelled instead of allowing a long PDF to continue spawning OCR
            # processes after the document has already been rejected.
            return [future.result() for future in futures]
        except Exception:
            for future in futures:
                future.cancel()
            raise
        finally:
            executor.shutdown(wait=True, cancel_futures=True)

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

        # Nowak druckt Lieferant, Belegart und Lieferscheinnummer stets in
        # einem kleinen Bereich oben rechts direkt neben dem Barcode. Dieser
        # gezielte OCR-Lauf benoetigt weniger als ein Fuenftel des bisherigen
        # Kopfbereichs. Andere Dokumenttypen werden hier absichtlich nicht
        # akzeptiert und durchlaufen weiterhin die allgemeine Erkennung.
        nowak_text = self._read_ocr(self._nowak_header_crop(image))
        detected = detect_document_from_text(nowak_text, barcodes)
        if detected and detected.supplier == "Nowak":
            LOGGER.info("Nowak-Schnellerkennung verwendet; lieferschein=%s", detected.number)
            return detected

        header_text = self._read_ocr(self._header_crop(image))
        detected = detect_document_from_text(header_text, barcodes)
        if detected:
            return detected

        text = self._read_ocr(image)
        return detect_document_from_text(text, barcodes)

    @staticmethod
    def _render(page: object):
        scale = 2.5
        page_rect = page.rect
        width = max(1, math.ceil(float(page_rect.width) * scale))
        height = max(1, math.ceil(float(page_rect.height) * scale))
        pixels = width * height
        if pixels > MAX_RENDER_PIXELS:
            raise RuntimeError(
                f"PDF-Seite wuerde {pixels:,} Pixel erzeugen und ueberschreitet das Render-Limit "
                f"von {MAX_RENDER_PIXELS:,} Pixeln."
            )
        pixmap = page.get_pixmap(matrix=__import__("fitz").Matrix(scale, scale), alpha=False)
        from PIL import Image

        return Image.open(io.BytesIO(pixmap.tobytes("png")))

    @staticmethod
    def _header_crop(image: object):
        width, height = image.size
        return image.crop((0, 0, width, max(1, round(height * 0.35))))

    @staticmethod
    def _nowak_header_crop(image: object):
        width, height = image.size
        return image.crop(
            (
                round(width * 0.39),
                round(height * 0.025),
                max(1, round(width * 0.75)),
                max(1, round(height * 0.205)),
            )
        )

    @staticmethod
    def _read_barcodes(image: object) -> tuple[str, ...]:
        try:
            import zxingcpp

            return tuple(result.text.strip() for result in zxingcpp.read_barcodes(image) if result.text.strip())
        except Exception:
            # OCR remains available when a page contains no readable barcode.
            LOGGER.warning("Barcode-Erkennung fehlgeschlagen; OCR wird als Ersatz verwendet.", exc_info=True)
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
                return pytesseract.image_to_string(
                    image,
                    lang=language,
                    config="--psm 6",
                    timeout=OCR_TIMEOUT_SECONDS,
                )
            except pytesseract.TesseractNotFoundError as error:
                raise RuntimeError(
                    "Tesseract OCR ist nicht installiert oder wurde nicht mit der Anwendung gefunden."
                ) from error
            except pytesseract.TesseractError as error:
                last_error = error
            except RuntimeError as error:
                if "timeout" in str(error).casefold():
                    raise RuntimeError(
                        f"Tesseract OCR hat das Zeitlimit von {OCR_TIMEOUT_SECONDS} Sekunden ueberschritten."
                    ) from error
                raise
        raise RuntimeError("Tesseract OCR konnte nicht gestartet werden.") from last_error
