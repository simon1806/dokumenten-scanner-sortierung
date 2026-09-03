from __future__ import annotations

import io
import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from .config import Settings, find_tesseract_executable, tesseract_runtime_source
from .models import DetectedDocument

NUMBER = r"(\d{6,12})"
NOWAK_NUMBER = r"(\d{7,12})"
NOWAK_CONTACT_FRAGMENT = "60686"
NOWAK_FAST_CROP = (0.39, 0.025, 0.75, 0.205)
BOHLE_NUMBER = r"(\d{5,12})"
BOHLE_NUMBER_FAST_CROP = (0.02, 0.015, 0.46, 0.13)
MONTAGE_FAST_CROP = (0.0, 0.02, 1.0, 0.24)
INTERNAL_BARCODE_FAST_CROP = (0.03, 0.02, 0.48, 0.18)
GENERAL_HEADER_BOTTOM = 0.45
NEUMA_FAST_HEADER_BOTTOM = 0.35
HEITZER_PAGE_TITLE_BOTTOM = 0.14
HEITZER_PAGE_FOOTER_TOP = 0.82
CODE39_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-. $/+%"
ASSIGNMENT_DECLARATION_SIGNAL = "ABTRETUNGSERKLARUNG"
ASSIGNMENT_NUMBER_CROP = (0.08, 0.43, 0.78, 0.67)
ASSIGNMENT_NUMBER = r"((?:32|52)\d{5})"
SIGNED_OFFER_CONFIRMATION_CROP = (0.02, 0.42, 0.98, 0.92)
SIGNED_OFFER_LINE_SEARCH = (0.22, 0.74, 0.78, 0.895)
SIGNED_OFFER_INK_X_RANGE = (0.26, 0.78)
SIGNED_OFFER_INK_ABOVE_LINE = (0.065, 0.004)
SIGNED_OFFER_MIN_LINE_DARK_RATIO = 0.18
SIGNED_OFFER_MIN_INK_RATIO = 0.003
SCANNER_TIMESTAMP = re.compile(r"(?<!\d)(\d{2})(\d{2})(\d{2})\d{4,6}(?!\d)")
NEUMA_ORDER = r"(?:I|1|\|)\s*[-–—]\s*(20\d{2})\s*[-–—]\s*(\d{6})"
ZEIDLER_EXECUTION_SIGNAL = "AUSFUHRUNGSBESTATIGUNG"
ZEIDLER_FAX_DIGITS = "03420238724"
SUPPORTED_DOCUMENT_SIGNALS = (
    "AUFMASSBLATT",
    "AUFMASS SCHEIN",
    "EMPFANGSSCHEIN",
    "MONTAGEBERICHT",
    "MONTAGEINFO",
    "HEITZER",
    "BOHLE",
    "GLAS-NOWAK",
    "GLAS NOWAK",
    "NOWAK",
    ASSIGNMENT_DECLARATION_SIGNAL,
    "NEUMA",
    ZEIDLER_EXECUTION_SIGNAL,
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


def code39_mod43_check_character(value: str) -> str:
    """Return the Mod-43 check character used by Glas Hagen's Code-39 labels."""
    try:
        checksum = sum(CODE39_ALPHABET.index(character) for character in value.upper()) % 43
    except ValueError:
        return ""
    return CODE39_ALPHABET[checksum]


def internal_document_from_barcode(barcode: str) -> DetectedDocument | None:
    """Read an AM/EM/MI barcode and remove verified padding/check characters."""
    value = barcode.strip().upper()
    checked = re.fullmatch(r"(AM|EM|MI)([-_]?)(\d{8})([0-9A-Z])", value)
    if checked:
        document_type, separator, payload, check_character = checked.groups()
        checked_value = f"{document_type}{separator}{payload}"
        if code39_mod43_check_character(checked_value) == check_character:
            number = payload[1:] if payload.startswith("0") else payload
            return DetectedDocument(document_type, number)
        return None

    match = re.fullmatch(r"(AM|EM|MI)[-_]?(\d{6,12})(?:[A-Z])?", value)
    if not match:
        return None
    number = match.group(2)
    if len(number) == 8 and number.startswith("0"):
        number = number[1:]
    return DetectedDocument(match.group(1), number)


def is_nowak_header(text: str) -> bool:
    """Recognise the stable Nowak header even when the logo OCR is imperfect."""
    has_name = bool(
        re.search(r"\bNOWAK\s+G[A-Z]{1,5}\b", text)
        or "GLAS-NOWAK" in text
        or "GLAS NOWAK" in text
    )
    has_contact = "LIEFERSCHEIN" in text and NOWAK_CONTACT_FRAGMENT in text
    return has_name or has_contact


def is_bohle_header(text: str) -> bool:
    """Recognise the stable Bohle supplier header."""
    normalised = normalise(text)
    return "BOHLE AG" in normalised or "BOHLE.COM" in normalised


def is_pauli_measurement_attachment(text: str) -> bool:
    """Recognise Pauli measurement/order sheets that belong to a preceding AM.

    These supplier pages do not carry a Glas-Hagen document number. Returning
    no document start keeps them attached to the preceding Aufmassschein while
    avoiding an unnecessary full-page OCR run. A real Pauli delivery note is
    deliberately excluded.
    """
    normalised = normalise(text)
    has_supplier = "PAULI" in normalised and (
        "SOHN" in normalised or "FLAMEA" in normalised
    )
    has_set_number = bool(re.search(r"\bSET\s*[- ]?\s*NR\.?", normalised))
    has_sheet_heading = "AUFMASS" in normalised or "GLASBESTELLUNG" in normalised
    return (
        has_supplier
        and has_set_number
        and has_sheet_heading
        and "LIEFERSCHEIN" not in normalised
    )


def has_supported_document_signal(text: str) -> bool:
    """Return whether header OCR warrants the expensive full-page OCR fallback."""
    normalised = normalise(text)
    return (
        is_assignment_declaration(normalised)
        or is_montage_report(normalised)
        or (
            "PAULI" in normalised
            and "SOHN" in normalised
            and "LIEFERSCHEIN" in normalised
        )
        or any(signal in normalised for signal in SUPPORTED_DOCUMENT_SIGNALS)
    )


def heitzer_page_reference(text: str) -> tuple[str, int, int] | None:
    """Read a Heitzer delivery-note number and its printed page reference."""
    normalised = normalise(text)
    number_match = re.search(rf"\bLIEFERSCHEIN\s+{NUMBER}\b", normalised)
    page_match = re.search(r"\bSEITE\s+(\d{1,3})\s+VON\s+(\d{1,3})\b", normalised)
    if not number_match or not page_match:
        return None
    page_number, page_count = (int(value) for value in page_match.groups())
    if page_count < 1 or page_number < 1 or page_number > page_count:
        return None
    return number_match.group(1), page_number, page_count


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


def has_neuma_header_signal(text: str) -> bool:
    """Return whether the small initial crop contains the distinctive NEUMA logo."""

    return "NEUMA" in normalise(text)


def is_zeidler_execution_confirmation(text: str) -> bool:
    """Recognise the stable Zeidler execution-confirmation form header.

    Some scans lose the Zeidler logo during OCR. The printed fax number and
    slogan are therefore accepted as supplier signals, but only together with
    the specific document heading. This keeps generic confirmations from being
    assigned to Zeidler accidentally.
    """
    normalised = normalise(text)
    digits = re.sub(r"\D", "", normalised)
    has_supplier_signal = (
        "ZEIDLER" in normalised
        or ZEIDLER_FAX_DIGITS in digits
        or ("WIR MACHEN" in normalised and "EINFACH" in normalised)
    )
    return ZEIDLER_EXECUTION_SIGNAL in normalised and has_supplier_signal


def offer_number_from_text(text: str) -> str | None:
    """Read the number printed in a Glas Hagen offer header."""
    match = re.search(
        rf"\bANGEBOT\s*(?:[-–—]\s*)?NR\.?\s*:?[ ]*{NUMBER}",
        normalise(text),
    )
    return match.group(1) if match else None


def is_signed_offer(text: str) -> bool:
    """Recognise the acceptance block of a customer-signed Glas Hagen offer."""
    normalised = normalise(text)
    has_acceptance = bool(
        re.search(
            r"\bICH\s+ERTEILE\s+I(?:HN|NN)EN\s+DEN\s+AUFTRAG\s+ZUR\s+"
            r"AUSF(?:U|UE)HRUNG\s+DER\s+ANGEBOTENEN\s+LEISTUNG\b",
            normalised,
        )
    )
    has_signature_field = bool(
        re.search(r"\bDATUM\s*/\s*UNTERSCHR[A-Z]{2,8}\b", normalised)
    )
    return has_acceptance and has_signature_field


def complete_signed_offer_pages(
    detections: list[DetectedDocument | None],
) -> list[DetectedDocument | None]:
    """Attach all pages when one page identifies a single signed offer.

    Customers sometimes return the offer with its pages scanned in reverse
    order. Only the acceptance page identifies the document type, so the
    remaining otherwise-unrecognised pages inherit that detection when no
    other document type is present in the same scan.
    """
    recognised = [detection for detection in detections if detection is not None]
    signed_offers = [
        detection for detection in recognised if detection.document_type == "AG"
    ]
    if not signed_offers:
        return detections

    signed_offer = signed_offers[0]
    if any(detection.key != signed_offer.key for detection in recognised):
        return detections
    return [detection or signed_offer for detection in detections]


def detect_document_from_text(
    text: str,
    barcodes: Iterable[str] = (),
    mi_scan_date: str | None = None,
) -> DetectedDocument | None:
    """Recognise the supported document headers from OCR text and barcode values."""
    normalised = normalise(text)
    barcode_values = tuple(barcodes)

    offer_number = offer_number_from_text(normalised)
    if offer_number and is_signed_offer(normalised):
        return DetectedDocument("AG", f"{offer_number}_UNTERS")

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

    if is_zeidler_execution_confirmation(normalised):
        match = re.search(
            rf"\bAUFTRAGS?\s*[-–—]?\s*NR\.?\s*:?\s*{NUMBER}",
            normalised,
        )
        if match:
            return DetectedDocument("Ausführung", match.group(1), "Zeidler")

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

    if is_bohle_header(normalised) and "LIEFERSCHEIN" in normalised:
        match = re.search(
            rf"(?:LIEFERSCHEIN|NUMMER)\s*:?\s*{BOHLE_NUMBER}",
            normalised,
        )
        if match:
            return DetectedDocument("LS", match.group(1), "Bohle")

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
        detected = internal_document_from_barcode(barcode)
        if detected:
            return detected
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
        self._metrics_lock = threading.Lock()
        self._active_metrics: dict[str, object] | None = None
        self._last_metrics = self._new_metrics()

    def _new_metrics(self) -> dict[str, object]:
        return {
            "render_seconds": 0.0,
            "barcode_seconds": 0.0,
            "ocr_seconds": 0.0,
            "ocr_calls": 0,
            "ocr_pixels": 0,
            "ocr_max_seconds": 0.0,
            "recognition_paths": Counter(),
            "tesseract_source": tesseract_runtime_source(self.settings.tesseract_path),
        }

    def _start_metrics(self) -> None:
        with self._metrics_lock:
            self._active_metrics = self._new_metrics()

    def _finish_metrics(self) -> None:
        with self._metrics_lock:
            metrics = self._active_metrics or self._new_metrics()
            paths = metrics["recognition_paths"]
            self._last_metrics = {
                **metrics,
                "recognition_paths": dict(sorted(paths.items())),
            }
            self._active_metrics = None

    @property
    def last_metrics(self) -> dict[str, object]:
        """Return path-free diagnostics for the most recent document."""

        with self._metrics_lock:
            result = dict(self._last_metrics)
            result["recognition_paths"] = dict(result["recognition_paths"])
            return result

    def _add_metric(self, name: str, value: float | int) -> None:
        with self._metrics_lock:
            if self._active_metrics is not None:
                self._active_metrics[name] = self._active_metrics[name] + value

    def _record_ocr_call(self, seconds: float, pixels: int) -> None:
        with self._metrics_lock:
            if self._active_metrics is None:
                return
            self._active_metrics["ocr_seconds"] = self._active_metrics["ocr_seconds"] + seconds
            self._active_metrics["ocr_calls"] = self._active_metrics["ocr_calls"] + 1
            self._active_metrics["ocr_pixels"] = self._active_metrics["ocr_pixels"] + pixels
            self._active_metrics["ocr_max_seconds"] = max(
                float(self._active_metrics["ocr_max_seconds"]), seconds
            )

    def _record_path(self, path: str) -> None:
        with self._metrics_lock:
            if self._active_metrics is not None:
                self._active_metrics["recognition_paths"][path] += 1

    def recognise_document(self, source: Path) -> list[DetectedDocument | None]:
        """Recognise pages in order while allowing two OCR processes to work concurrently."""
        previous_deadline = self._processing_deadline
        self._processing_deadline = time.monotonic() + self.settings.processing_timeout_seconds
        self._start_metrics()
        try:
            return self._recognise_document_with_deadline(source)
        finally:
            self._processing_deadline = previous_deadline
            self._finish_metrics()

    def recognise_document_pages(
        self,
        source: Path,
    ) -> list[tuple[int, DetectedDocument | None]]:
        """Recognise pages and expose a validated logical-to-source page order."""
        previous_deadline = self._processing_deadline
        self._processing_deadline = time.monotonic() + self.settings.processing_timeout_seconds
        self._start_metrics()
        try:
            detections = self._recognise_document_with_deadline(source)
            return self._order_heitzer_delivery_pages(source, detections)
        finally:
            self._processing_deadline = previous_deadline
            self._finish_metrics()

    def _recognise_document_with_deadline(self, source: Path) -> list[DetectedDocument | None]:
        import pymupdf

        source_size = source.stat().st_size
        if source_size > MAX_PDF_BYTES:
            raise RuntimeError(
                f"PDF ist mit {source_size / (1024 * 1024):.1f} MB groesser als das erlaubte Limit "
                f"von {MAX_PDF_BYTES // (1024 * 1024)} MB."
            )

        with pymupdf.open(source) as document:
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
            detections = [future.result() for future in futures]
            return complete_signed_offer_pages(detections)
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

    def _order_heitzer_delivery_pages(
        self,
        source: Path,
        detections: list[DetectedDocument | None],
    ) -> list[tuple[int, DetectedDocument | None]]:
        source_order = list(enumerate(detections))
        recognised = [detection for detection in detections if detection is not None]
        if len(detections) < 2 or not recognised:
            return source_order

        document = recognised[0]
        if document.supplier != "Heitzer" or any(
            detection.key != document.key for detection in recognised
        ):
            return source_order

        references: list[tuple[str, int, int]] = []
        for page_index in range(len(detections)):
            try:
                reference = self._read_heitzer_page_reference(source, page_index)
            except Exception as error:
                LOGGER.info(
                    "Heitzer-Seitenfolge konnte nicht sicher gelesen werden; "
                    "Quellreihenfolge bleibt erhalten (%s).",
                    type(error).__name__,
                )
                return source_order
            if reference is None or reference[0] != document.number:
                return source_order
            references.append(reference)

        page_counts = {reference[2] for reference in references}
        page_numbers = {reference[1] for reference in references}
        expected_count = len(detections)
        if page_counts != {expected_count} or page_numbers != set(
            range(1, expected_count + 1)
        ):
            return source_order

        ordered_indexes = sorted(
            range(expected_count), key=lambda page_index: references[page_index][1]
        )
        if ordered_indexes != list(range(expected_count)):
            LOGGER.info(
                "Heitzer-Seitenfolge korrigiert; lieferschein=%s; quellseiten=%s",
                document.number,
                ",".join(str(index + 1) for index in ordered_indexes),
            )
        return [(page_index, document) for page_index in ordered_indexes]

    def _read_heitzer_page_reference(
        self,
        source: Path,
        page_index: int,
    ) -> tuple[str, int, int] | None:
        import pymupdf
        from PIL import Image

        with pymupdf.open(source) as document:
            image = self._render_with_metrics(document.load_page(page_index))
        width, height = image.size
        title = image.crop(
            (0, 0, width, max(1, round(height * HEITZER_PAGE_TITLE_BOTTOM)))
        )
        footer = image.crop(
            (0, round(height * HEITZER_PAGE_FOOTER_TOP), width, height)
        )
        combined = Image.new(
            "RGB",
            (max(title.width, footer.width), title.height + footer.height),
            "white",
        )
        combined.paste(title, (0, 0))
        combined.paste(footer, (0, title.height))
        self._record_path("heitzer_seitenreferenz")
        return heitzer_page_reference(self._read_ocr(combined))

    def _recognise_file_page(self, source: Path, page_index: int) -> DetectedDocument | None:
        import pymupdf

        with pymupdf.open(source) as document:
            return self.recognise(document.load_page(page_index), scan_date_from_source(source))

    def recognise(self, page: object, mi_scan_date: str | None = None) -> DetectedDocument | None:
        embedded_text = ""
        try:
            embedded_text = page.get_text("text")
        except Exception:
            # Reine Bildscans besitzen üblicherweise keine eingebettete Textebene.
            pass

        if embedded_text:
            self._record_path("eingebetteter_text")
            detected = detect_document_from_text(embedded_text, mi_scan_date=mi_scan_date)
            if detected and detected.document_type != "AG":
                return detected
            if is_pauli_measurement_attachment(embedded_text):
                LOGGER.info(
                    "Pauli-Aufmassanlage erkannt; Bildrendering und Ganzseiten-OCR uebersprungen."
                )
                return None

        image = self._render_with_metrics(page)
        barcode_started = time.perf_counter()
        try:
            barcodes = self._read_barcodes(image)
        finally:
            self._add_metric("barcode_seconds", time.perf_counter() - barcode_started)
        self._record_path("barcode")
        detected = detect_document_from_text(embedded_text, barcodes, mi_scan_date)
        if detected and detected.document_type != "AG":
            return detected

        # Nowak druckt Lieferant, Belegart und Lieferscheinnummer stets in
        # einem kleinen Bereich oben rechts direkt neben dem Barcode. Dieser
        # gezielte OCR-Lauf benoetigt weniger als ein Fuenftel des bisherigen
        # Kopfbereichs. Andere Dokumenttypen werden hier absichtlich nicht
        # akzeptiert und durchlaufen weiterhin die allgemeine Erkennung.
        self._record_path("lieferantenkopf_klein")
        nowak_text = self._read_ocr(self._nowak_header_crop(image))
        detected = detect_document_from_text(nowak_text, barcodes, mi_scan_date)
        if detected and detected.supplier == "Nowak":
            LOGGER.info("Nowak-Schnellerkennung verwendet; lieferschein=%s", detected.number)
            return detected

        # Der kleine erste Ausschnitt erkennt das NEUMA-Logo zuverlässig, die
        # Auftragsnummer liegt jedoch etwas tiefer. In diesem eindeutigen Fall
        # genügt ein 35-Prozent-Kopf statt des allgemeinen 45-Prozent-Bereichs.
        if has_neuma_header_signal(nowak_text):
            self._record_path("neuma_kopf")
            neuma_text = self._read_ocr(self._neuma_header_crop(image))
            detected = detect_document_from_text(neuma_text, barcodes, mi_scan_date)
            if detected and detected.supplier == "NEUMA":
                LOGGER.info("NEUMA-Schnellerkennung verwendet; auftrag=%s", detected.number)
                return detected

        # Bohle druckt das Logo im bereits gelesenen rechten Kopfbereich und
        # die Lieferscheinnummer oben links. Nach dem Lieferantenhinweis wird
        # deshalb nur dieses kleine Nummernfeld statt des allgemeinen Kopfs
        # gelesen.
        if is_bohle_header(nowak_text):
            self._record_path("bohle_nummer")
            bohle_number_text = self._read_ocr(self._bohle_number_crop(image))
            detected = detect_document_from_text(
                f"{nowak_text}\n{bohle_number_text}",
                barcodes,
                mi_scan_date,
            )
            if detected and detected.supplier == "Bohle":
                LOGGER.info("Bohle-Schnellerkennung verwendet; lieferschein=%s", detected.number)
                return detected

        # Montageberichte drucken ihre Auftragsnummer im selben kleinen Bereich
        # wie Nowak oben rechts. Sie erhalten nur bei diesem Hinweis einen
        # schmalen Formularstreifen statt des deutlich größeren Kopfbereichs.
        if has_montage_order_hint(nowak_text):
            self._record_path("montage_kopf")
            montage_text = self._read_ocr(self._montage_header_crop(image))
            detected = detect_document_from_text(montage_text, barcodes, mi_scan_date)
            if detected and detected.document_type == "MI":
                LOGGER.info("Montageinfo-Schnellerkennung verwendet; auftrag=%s", detected.number)
                return detected

        self._record_path("allgemeiner_kopf")
        header_text = self._read_ocr(self._header_crop(image))
        detected = detect_document_from_text(header_text, barcodes, mi_scan_date)
        if detected:
            return detected

        if is_assignment_declaration(header_text):
            self._record_path("abtretung_nummer")
            assignment_text = self._read_ocr(self._assignment_number_crop(image))
            detected = detect_document_from_text(f"{header_text}\n{assignment_text}", barcodes, mi_scan_date)
            if detected:
                LOGGER.info("Abtretungserklaerung-Schnellerkennung verwendet; auftrag=%s", detected.number)
                return detected

        if offer_number_from_text(header_text):
            self._record_path("angebot_bestaetigung")
            confirmation_text = self._read_ocr(self._signed_offer_confirmation_crop(image))
            detected = detect_document_from_text(
                f"{header_text}\n{confirmation_text}",
                barcodes,
                mi_scan_date,
            )
            if detected and detected.document_type == "AG":
                if not self._has_signed_offer_mark(image):
                    LOGGER.info(
                        "Angebot ohne handschriftliche Eintragung im Unterschriftsbereich "
                        "bleibt unberuecksichtigt."
                    )
                    return None
                LOGGER.info(
                    "Unterschriebenes Angebot erkannt; angebot=%s",
                    detected.number.removesuffix("_UNTERS"),
                )
                return detected
            LOGGER.info("Angebot ohne erkennbare Auftragsbestaetigung bleibt unberuecksichtigt.")
            return None

        if is_pauli_measurement_attachment(
            f"{embedded_text}\n{nowak_text}\n{header_text}"
        ):
            LOGGER.info("Pauli-Aufmassanlage erkannt; Ganzseiten-OCR uebersprungen.")
            return None

        if not has_supported_document_signal(header_text):
            LOGGER.info(
                "Ganzseiten-OCR uebersprungen; keine bekannte Dokument-Signatur im Kopfbereich."
            )
            return None

        self._record_path("ganzseite")
        text = self._read_ocr(image)
        return detect_document_from_text(text, barcodes, mi_scan_date)

    def _render_with_metrics(self, page: object):
        started = time.perf_counter()
        try:
            return self._render(page)
        finally:
            self._add_metric("render_seconds", time.perf_counter() - started)

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
        pixmap = page.get_pixmap(matrix=__import__("pymupdf").Matrix(scale, scale), alpha=False)
        from PIL import Image

        return Image.open(io.BytesIO(pixmap.tobytes("png")))

    @staticmethod
    def _header_crop(image: object):
        width, height = image.size
        # Mehrseitige Empfangsscheine koennen auf der ersten Seite einen
        # grossen Adressblock ueber der Belegzeile enthalten. Die bisherige
        # 35-Prozent-Grenze schnitt diese Zeile ab und verhinderte dadurch die
        # ansonsten eindeutige Erkennung ueber Nummer und Dokumenttyp.
        return image.crop(
            (0, 0, width, max(1, round(height * GENERAL_HEADER_BOTTOM)))
        )

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
    def _neuma_header_crop(image: object):
        """Read the compact NEUMA header including its complete order number."""

        width, height = image.size
        return image.crop(
            (0, 0, width, max(1, round(height * NEUMA_FAST_HEADER_BOTTOM)))
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
    def _bohle_number_crop(image: object):
        """Read Bohle's delivery-note number from the small top-left field."""
        width, height = image.size
        left, top, right, bottom = BOHLE_NUMBER_FAST_CROP
        return image.crop(
            (
                round(width * left),
                round(height * top),
                max(1, round(width * right)),
                max(1, round(height * bottom)),
            )
        )

    @staticmethod
    def _signed_offer_confirmation_crop(image: object):
        """Read the lower acceptance and signature block of a Glas Hagen offer."""
        width, height = image.size
        left, top, right, bottom = SIGNED_OFFER_CONFIRMATION_CROP
        return image.crop(
            (
                round(width * left),
                round(height * top),
                max(1, round(width * right)),
                max(1, round(height * bottom)),
            )
        )

    @staticmethod
    def _has_signed_offer_mark(image: object) -> bool:
        """Return whether handwriting is present above the printed signature line."""
        grayscale = image.convert("L")
        width, height = grayscale.size
        left, top, right, bottom = SIGNED_OFFER_LINE_SEARCH
        x_start = round(width * left)
        x_end = max(x_start + 1, round(width * right))
        y_start = round(height * top)
        y_end = max(y_start + 1, round(height * bottom))

        darkest_row_count = 0
        signature_line_y = y_start
        for y_position in range(y_start, y_end):
            histogram = grayscale.crop((x_start, y_position, x_end, y_position + 1)).histogram()
            dark_pixels = sum(histogram[:170])
            if dark_pixels > darkest_row_count:
                darkest_row_count = dark_pixels
                signature_line_y = y_position

        line_width = x_end - x_start
        if darkest_row_count < line_width * SIGNED_OFFER_MIN_LINE_DARK_RATIO:
            return False

        ink_left, ink_right = SIGNED_OFFER_INK_X_RANGE
        above_start, above_end = SIGNED_OFFER_INK_ABOVE_LINE
        ink_box = (
            round(width * ink_left),
            max(0, signature_line_y - round(height * above_start)),
            max(1, round(width * ink_right)),
            max(1, signature_line_y - round(height * above_end)),
        )
        ink_area = grayscale.crop(ink_box)
        dark_ink = sum(ink_area.histogram()[:170])
        return dark_ink >= ink_area.width * ink_area.height * SIGNED_OFFER_MIN_INK_RATIO

    @staticmethod
    def _read_barcodes(image: object) -> tuple[str, ...]:
        try:
            import zxingcpp

            values = tuple(result.text.strip() for result in zxingcpp.read_barcodes(image) if result.text.strip())
            if values:
                return values

            # Glas Hagen druckt den langen Code-39-Belegcode oben links. Bei
            # bildbasierten Scanner-PDFs reicht die normale Seitenauflösung
            # gelegentlich nicht für die schmalen Balken. Nur dieser kleine
            # Ausschnitt wird deshalb vergrößert; ein doppeltes Rendern der
            # vollständigen A4-Seite würde Zeit und Speicher vervierfachen.
            width, height = image.size
            left, top, right, bottom = INTERNAL_BARCODE_FAST_CROP
            barcode_area = image.crop(
                (
                    round(width * left),
                    round(height * top),
                    max(1, round(width * right)),
                    max(1, round(height * bottom)),
                )
            )
            enlarged = barcode_area.resize((barcode_area.width * 2, barcode_area.height * 2))
            return tuple(
                result.text.strip()
                for result in zxingcpp.read_barcodes(enlarged)
                if result.text.strip()
            )
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
            timeout = self._remaining_ocr_seconds()
            size = getattr(image, "size", (0, 0))
            pixels = (
                int(size[0]) * int(size[1])
                if isinstance(size, tuple) and len(size) == 2
                else 0
            )
            ocr_started = time.perf_counter()
            try:
                return pytesseract.image_to_string(
                    image,
                    lang=language,
                    config="--psm 6",
                    timeout=timeout,
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
            finally:
                self._record_ocr_call(time.perf_counter() - ocr_started, pixels)
        raise RuntimeError("Tesseract OCR konnte nicht gestartet werden.") from last_error
