from __future__ import annotations

import io
import logging
import math
import re
import time
import unicodedata
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .config import Settings, find_tesseract_executable
from .models import DetectedDocument

NUMBER = r"(\d{6,12})"
NOWAK_NUMBER = r"(\d{7,12})"
NOWAK_CONTACT_FRAGMENT = "60686"
NOWAK_FAST_CROP = (0.39, 0.025, 0.75, 0.205)
MONTAGE_FAST_CROP = (0.0, 0.02, 1.0, 0.24)
ASSIGNMENT_DECLARATION_SIGNAL = "ABTRETUNGSERKLARUNG"
ASSIGNMENT_NUMBER_CROP = (0.08, 0.43, 0.78, 0.67)
ASSIGNMENT_NUMBER = r"((?:32|52)\d{5})"
SCANNER_TIMESTAMP = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})\d{4,6}(?!\d)")
NEUMA_ORDER = r"(?:I|1|\|)\s*[-–—]\s*(20\d{2})\s*[-–—]\s*(\d{6})"
SUPPORTED_DOCUMENT_SIGNALS = (
    "AUFMASSBLATT",
    "AUFMASS SCHEIN",
    "EMPFANGSSCHEIN",
    "MONTAGEBERICHT",
    "MONTAGEINFO",
    "HEITZER",
    "PAULI",
    "GLAS-NOWAK",
    "GLAS NOWAK",
    "NOWAK",
    ASSIGNMENT_DECLARATION_SIGNAL,
    "NEUMA",
)
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


def is_nowak_header(text: str) -> bool:
    """Recognise the stable Nowak header even when the logo OCR is imperfect."""
    has_name = bool(
        re.search(r"\bNOWAK\s+G[A-Z]{1,5}\b", text)
        or "GLAS-NOWAK" in text
        or "GLAS NOWAK" in text
    )
    has_contact = "LIEFERSCHEIN" in text and NOWAK_CONTACT_FRAGMENT in text
    return has_name or has_contact


def has_supported_document_signal(text: str) -> bool:
    """Return whether header OCR warrants the expensive full-page OCR fallback."""
    normalised = normalise(text)
    return (
        is_assignment_declaration(normalised)
        or is_montage_report(normalised)
        or any(signal in normalised for signal in SUPPORTED_DOCUMENT_SIGNALS)
    )


def is_assignment_declaration(text: str) -> bool:
    """Return whether the page is a Glas Hagen assignment declaration."""
    # Scanner-OCR occasionally reads the "la" in "Erklaerung" as "ld".
    # The fixed word stem remains specific enough to avoid accepting unrelated
    # documents while still recognising the scanned original template.
    return bool(re.search(r"\bABTRETUNGSERK[A-Z]{0,5}RUNG\b", normalise(text)))


def has_montage_order_hint(text: str) -> bool:
    """Return whether the small top-right OCR crop warrants an MI lookup."""
    return bool(re.search(r"\bAUFTRAG\s*:", normalise(text)))


def is_montage_report(text: str) -> bool:
    """Recognise a Montagebericht despite the known one-character OCR slip.

    On the scanner form, Tesseract can turn the ``i`` in ``Montagebericht``
    into a typographic apostrophe (``Montageber’cht``).  This is accepted only
    as the document header; ``detect_document_from_text`` still requires the
    explicit ``Auftrag:`` label and a valid document number before naming a
    scan as a Montageinfo.
    """
    normalised = normalise(text)
    return "MONTAGEINFO" in normalised or bool(
        re.search(r"\bMONTAGEBER(?:I|['’`])?CHT\b", normalised)
    )


def scan_date_from_source(source: Path) -> str:
    """Return an ISO scan date from the scanner filename, with mtime as fallback."""
    match = SCANNER_TIMESTAMP.search(source.stem)
    if match:
        year, month, day = (int(part) for part in match.groups())
        try:
            return date(2000 + year, month, day).isoformat()
        except ValueError:
            pass
    try:
        return datetime.fromtimestamp(source.stat().st_mtime).date().isoformat()
    except OSError:
        return date.today().isoformat()


def is_neuma_order(text: str) -> bool:
    """Return whether OCR text contains a Neue Marler Baugesellschaft order."""
    normalised = normalise(text)
    return "NEUMA" in normalised and bool(re.search(rf"\bAUFTRAG\s+{NEUMA_ORDER}\b", normalised))


def detect_document_from_text(
    text: str,
    barcodes: Iterable[str] = (),
    mi_scan_date: str | None = None,
) -> DetectedDocument | None:
    """Recognise the supported document headers from OCR text and barcode values."""
    normalised = normalise(text)
    barcode_values = tuple(barcodes)

    if is_assignment_declaration(normalised):
        match = re.search(
            rf"AUF?T{{1,2}}RAG\s*/\s*ANGEBOT\s*(?:NR\.?\s*)?(?::\s*)?{ASSIGNMENT_NUMBER}",
            normalised,
        )
        if match:
            return DetectedDocument("ABTRET", match.group(1))

    if is_neuma_order(normalised):
        match = re.search(rf"\bAUFTRAG\s+{NEUMA_ORDER}\b", normalised)
        if match:
            year, sequence = match.groups()
            return DetectedDocument("EM", f"I-{year}-{sequence}", "NEUMA")

    if is_nowak_header(normalised):
        number = extract_number(
            normalised,
            rf"LIEFERSCHEIN\s*(?:NR\.?\s*)?{NUMBER}",
            barcode_values,
        )
        if not number:
            # Manche OCR-Laeufe erkennen das Wort "Lieferschein" nicht, lesen
            # die sieben- bis zwoelfstellige Belegnummer unter dem Nowak-Kopf
            # aber korrekt. Kurze Kunden- und Routennummern bleiben unberuehrt.
            match = re.search(rf"\b{NOWAK_NUMBER}\b", normalised)
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

    if is_montage_report(normalised):
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
    if mi_scan_date and is_montage_report(normalised):
        # Rarely a Montageinfo is issued without an order number and without a
        # usable MI barcode. It remains a valid one-page report, so preserve it
        # under the scanner date rather than forwarding it as unrecognised.
        return DetectedDocument("MI", mi_scan_date)
    return None


class PageRecognizer:
    """Renders a page, reads its barcodes and uses OCR as a fallback."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._processing_deadline: float | None = None

    def recognise_document(self, source: Path) -> list[DetectedDocument | None]:
        """Recognise pages in order while allowing two OCR processes to work concurrently."""
        previous_deadline = self._processing_deadline
        self._processing_deadline = time.monotonic() + self.settings.processing_timeout_seconds
        try:
            return self._recognise_document_with_deadline(source)
        finally:
            self._processing_deadline = previous_deadline

    def _recognise_document_with_deadline(self, source: Path) -> list[DetectedDocument | None]:
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

    def _remaining_ocr_seconds(self) -> int:
        if self._processing_deadline is None:
            return OCR_TIMEOUT_SECONDS
        remaining = self._processing_deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"OCR-Gesamtzeitlimit von {self.settings.processing_timeout_seconds} Sekunden überschritten."
            )
        return max(1, min(OCR_TIMEOUT_SECONDS, math.ceil(remaining)))

    def _recognise_file_page(self, source: Path, page_index: int) -> DetectedDocument | None:
        import fitz

        with fitz.open(source) as document:
            return self.recognise(document.load_page(page_index), scan_date_from_source(source))

    def recognise(self, page: object, mi_scan_date: str | None = None) -> DetectedDocument | None:
        embedded_text = ""
        try:
            embedded_text = page.get_text("text")
        except Exception:
            # Reine Bildscans besitzen üblicherweise keine eingebettete Textebene.
            pass

        if embedded_text:
            detected = detect_document_from_text(embedded_text, mi_scan_date=mi_scan_date)
            if detected:
                return detected

        image = self._render(page)
        barcodes = self._read_barcodes(image)
        detected = detect_document_from_text(embedded_text, barcodes, mi_scan_date)
        if detected:
            return detected

        # Nowak druckt Lieferant, Belegart und Lieferscheinnummer stets in
        # einem kleinen Bereich oben rechts direkt neben dem Barcode. Dieser
        # gezielte OCR-Lauf benoetigt weniger als ein Fuenftel des bisherigen
        # Kopfbereichs. Andere Dokumenttypen werden hier absichtlich nicht
        # akzeptiert und durchlaufen weiterhin die allgemeine Erkennung.
        nowak_text = self._read_ocr(self._nowak_header_crop(image))
        detected = detect_document_from_text(nowak_text, barcodes, mi_scan_date)
        if detected and detected.supplier == "Nowak":
            LOGGER.info("Nowak-Schnellerkennung verwendet; lieferschein=%s", detected.number)
            return detected

        # Montageberichte drucken ihre Auftragsnummer im selben kleinen Bereich
        # wie Nowak oben rechts. Sie erhalten nur bei diesem Hinweis einen
        # schmalen Formularstreifen statt des deutlich größeren Kopfbereichs.
        if has_montage_order_hint(nowak_text):
            montage_text = self._read_ocr(self._montage_header_crop(image))
            detected = detect_document_from_text(montage_text, barcodes, mi_scan_date)
            if detected and detected.document_type == "MI":
                LOGGER.info("Montageinfo-Schnellerkennung verwendet; auftrag=%s", detected.number)
                return detected

        header_text = self._read_ocr(self._header_crop(image))
        detected = detect_document_from_text(header_text, barcodes, mi_scan_date)
        if detected:
            return detected

        if is_assignment_declaration(header_text):
            assignment_text = self._read_ocr(self._assignment_number_crop(image))
            detected = detect_document_from_text(f"{header_text}\n{assignment_text}", barcodes, mi_scan_date)
            if detected:
                LOGGER.info("Abtretungserklaerung-Schnellerkennung verwendet; auftrag=%s", detected.number)
                return detected

        if not has_supported_document_signal(header_text):
            LOGGER.info(
                "Ganzseiten-OCR uebersprungen; keine bekannte Dokument-Signatur im Kopfbereich."
            )
            return None

        text = self._read_ocr(image)
        return detect_document_from_text(text, barcodes, mi_scan_date)

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
        left, top, right, bottom = NOWAK_FAST_CROP
        return image.crop(
            (
                round(width * left),
                round(height * top),
                max(1, round(width * right)),
                max(1, round(height * bottom)),
            )
        )

    @staticmethod
    def _montage_header_crop(image: object):
        """Read the short form band containing a Montagebericht's order number."""
        width, height = image.size
        left, top, right, bottom = MONTAGE_FAST_CROP
        return image.crop(
            (
                round(width * left),
                round(height * top),
                max(1, round(width * right)),
                max(1, round(height * bottom)),
            )
        )

    @staticmethod
    def _assignment_number_crop(image: object):
        """Read the fixed Auftrag/Angebot field of an assignment declaration."""
        width, height = image.size
        left, top, right, bottom = ASSIGNMENT_NUMBER_CROP
        return image.crop(
            (
                round(width * left),
                round(height * top),
                max(1, round(width * right)),
                max(1, round(height * bottom)),
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
                    timeout=self._remaining_ocr_seconds(),
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
