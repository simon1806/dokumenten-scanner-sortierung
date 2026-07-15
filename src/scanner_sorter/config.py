from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path


class ConfigurationError(ValueError):
    """Raised when the persisted application settings cannot be read safely."""


def _resolved_path(value: str | Path) -> Path:
    return Path(value).expanduser().resolve(strict=False)


def _paths_overlap(first: Path, second: Path) -> bool:
    """Return True if either directory contains the other one."""
    first_key = os.path.normcase(str(first))
    second_key = os.path.normcase(str(second))
    if first_key == second_key:
        return True
    try:
        common = os.path.normcase(os.path.commonpath((first_key, second_key)))
    except ValueError:  # Different Windows drives do not overlap.
        return False
    return common in {first_key, second_key}


@dataclass(slots=True)
class Settings:
    input_folder: str = ""
    output_folder: str = ""
    archive_folder: str = ""
    review_folder: str = ""
    archive_retention_days: int = 30
    settle_seconds: int = 2
    invalid_pdf_timeout_seconds: int = 60
    poll_interval_seconds: int = 1
    tesseract_path: str = ""
    ocr_languages: str = "deu+eng"

    def validate(self) -> list[str]:
        errors: list[str] = []
        named_paths = {
            "Eingangsordner": self.input_folder,
            "Zielordner": self.output_folder,
            "Archivordner": self.archive_folder,
            "Prüfordner": str(self.review_folder_path),
        }
        for label, value in named_paths.items():
            if not value.strip():
                errors.append(f"{label} ist nicht festgelegt.")

        configured_items = [
            (label, _resolved_path(value))
            for label, value in named_paths.items()
            if value.strip()
        ]
        configured = [path for _label, path in configured_items]
        if len(configured) != len(set(configured)):
            errors.append("Eingangs-, Ziel-, Archiv- und Prüfordner müssen unterschiedlich sein.")
        elif any(
            _paths_overlap(first, second)
            and not (
                first_label == "Zielordner"
                and second_label == "Prüfordner"
                and first in second.parents
            )
            for index, (first_label, first) in enumerate(configured_items)
            for second_label, second in configured_items[index + 1 :]
        ):
            errors.append(
                "Eingangs-, Ziel-, Archiv- und Prüfordner dürfen nicht ineinander liegen."
            )

        for label, path in configured_items:
            if path == Path(path.anchor):
                errors.append(f"{label} darf kein Laufwerks-, Freigabe- oder Dateisystemstamm sein.")
        if self.archive_retention_days < 1:
            errors.append("Die Archiv-Aufbewahrung muss mindestens einen Tag betragen.")
        if self.settle_seconds < 1:
            errors.append("Die Stabilitätszeit für vollständige Scans muss mindestens eine Sekunde betragen.")
        if self.invalid_pdf_timeout_seconds < self.settle_seconds:
            errors.append(
                "Die Wartezeit für beschädigte PDFs muss mindestens so lang wie die Dateistabilitätszeit sein."
            )
        if self.poll_interval_seconds < 1:
            errors.append("Das Prüfintervall muss mindestens eine Sekunde betragen.")
        return errors

    def ensure_directories(self) -> None:
        for value in (self.input_folder, self.output_folder, self.archive_folder, str(self.review_folder_path)):
            Path(value).mkdir(parents=True, exist_ok=True)

    @property
    def review_folder_path(self) -> Path:
        if self.review_folder.strip():
            return Path(self.review_folder)
        if self.output_folder.strip():
            return Path(self.output_folder) / "Nicht_erkannt"
        return Path("")


def default_settings_path() -> Path:
    base = Path(os.environ.get("APPDATA") or os.environ.get("PROGRAMDATA") or Path.home())
    return base / "DokumentenScannerSortierung" / "settings.json"


def application_folder() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def bundled_folder() -> Path | None:
    bundle_path = getattr(sys, "_MEIPASS", None)
    return Path(bundle_path).resolve() if bundle_path else None


def find_tesseract_executable(configured_path: str = "") -> Path | None:
    configured_path = configured_path.strip()
    if configured_path:
        configured = Path(configured_path)
        return configured if configured.exists() else None

    program_files = [os.environ.get("PROGRAMFILES"), os.environ.get("PROGRAMFILES(X86)")]
    roots = [folder for folder in (bundled_folder(), application_folder()) if folder]
    candidates = []
    for root in roots:
        candidates.extend(
            [
                root / "tesseract" / "tesseract.exe",
                root / "Tesseract-OCR" / "tesseract.exe",
            ]
        )
    candidates.extend(Path(path) / "Tesseract-OCR" / "tesseract.exe" for path in program_files if path)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def load_settings(path: Path | None = None) -> Settings:
    path = path or default_settings_path()
    if not path.exists():
        return Settings()

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            f"Die Einstellungsdatei '{path}' ist beschädigt oder nicht lesbar. "
            "Bitte stellen Sie eine Sicherung wieder her oder benennen Sie die Datei um."
        ) from error
    if not isinstance(raw, dict):
        raise ConfigurationError(
            f"Die Einstellungsdatei '{path}' hat ein ungültiges Format (JSON-Objekt erwartet)."
        )
    known_fields = {field.name for field in fields(Settings)}
    values = {key: value for key, value in raw.items() if key in known_fields}
    string_fields = {
        "input_folder",
        "output_folder",
        "archive_folder",
        "review_folder",
        "tesseract_path",
        "ocr_languages",
    }
    integer_fields = {
        "archive_retention_days",
        "settle_seconds",
        "invalid_pdf_timeout_seconds",
        "poll_interval_seconds",
    }
    invalid_fields = [
        key
        for key, value in values.items()
        if (key in string_fields and not isinstance(value, str))
        or (key in integer_fields and (not isinstance(value, int) or isinstance(value, bool)))
    ]
    if invalid_fields:
        raise ConfigurationError(
            f"Die Einstellungsdatei '{path}' enthält ungültige Werte für: "
            f"{', '.join(sorted(invalid_fields))}."
        )
    try:
        return Settings(**values)
    except (TypeError, ValueError) as error:
        raise ConfigurationError(
            f"Die Einstellungsdatei '{path}' enthält ungültige Werte."
        ) from error


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
