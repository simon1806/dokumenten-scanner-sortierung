from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


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

        configured = [Path(value).resolve() for value in named_paths.values() if value.strip()]
        if len(configured) != len(set(configured)):
            errors.append("Eingangs-, Ziel-, Archiv- und Prüfordner müssen unterschiedlich sein.")
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

    raw = json.loads(path.read_text(encoding="utf-8"))
    known_fields = {field.name for field in fields(Settings)}
    values = {key: value for key, value in raw.items() if key in known_fields}
    return Settings(**values)


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or default_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
