from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(slots=True)
class Settings:
    input_folder: str = ""
    output_folder: str = ""
    archive_folder: str = ""
    archive_retention_days: int = 30
    settle_seconds: int = 15
    poll_interval_seconds: int = 5
    tesseract_path: str = ""
    ocr_languages: str = "deu+eng"

    def validate(self) -> list[str]:
        errors: list[str] = []
        named_paths = {
            "Eingangsordner": self.input_folder,
            "Zielordner": self.output_folder,
            "Archivordner": self.archive_folder,
        }
        for label, value in named_paths.items():
            if not value.strip():
                errors.append(f"{label} ist nicht festgelegt.")

        configured = [Path(value).resolve() for value in named_paths.values() if value.strip()]
        if len(configured) != len(set(configured)):
            errors.append("Eingangs-, Ziel- und Archivordner müssen unterschiedlich sein.")
        if self.archive_retention_days < 1:
            errors.append("Die Archiv-Aufbewahrung muss mindestens einen Tag betragen.")
        if self.settle_seconds < 1:
            errors.append("Die Wartezeit für vollständige Scans muss mindestens eine Sekunde betragen.")
        if self.poll_interval_seconds < 1:
            errors.append("Das Prüfintervall muss mindestens eine Sekunde betragen.")
        return errors

    def ensure_directories(self) -> None:
        for value in (self.input_folder, self.output_folder, self.archive_folder):
            Path(value).mkdir(parents=True, exist_ok=True)


def default_settings_path() -> Path:
    base = Path(os.environ.get("PROGRAMDATA", Path.home()))
    return base / "DokumentenScannerSortierung" / "settings.json"


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
