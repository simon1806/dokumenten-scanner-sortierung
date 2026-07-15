from __future__ import annotations

import importlib.metadata
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import find_tesseract_executable


@dataclass(frozen=True, slots=True)
class VersionEntry:
    name: str
    version: str


@dataclass(frozen=True, slots=True)
class VersionInformation:
    application: tuple[VersionEntry, ...]
    ocr: tuple[VersionEntry, ...]
    libraries: tuple[VersionEntry, ...]
    tesseract_path: Path | None


LIBRARIES = (
    ("PyMuPDF", "PyMuPDF"),
    ("pypdf", "pypdf"),
    ("Pillow", "Pillow"),
    ("pytesseract", "pytesseract"),
    ("zxing-cpp", "zxing-cpp"),
    ("pystray", "pystray"),
)


def parse_tesseract_versions(output: str) -> tuple[str, str]:
    tesseract_match = re.search(r"(?im)^tesseract\s+v?([^\s]+)", output)
    leptonica_match = re.search(r"(?im)^\s*leptonica[-\s]+([^\s]+)", output)
    tesseract = tesseract_match.group(1) if tesseract_match else "Unbekannt"
    leptonica = leptonica_match.group(1) if leptonica_match else "Unbekannt"
    return tesseract, leptonica


def _tesseract_versions(configured_path: str) -> tuple[str, str, Path | None]:
    executable = find_tesseract_executable(configured_path)
    if executable is None:
        return "Nicht gefunden", "Nicht verfügbar", None

    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return "Nicht ermittelbar", "Nicht ermittelbar", executable

    tesseract, leptonica = parse_tesseract_versions(result.stdout + "\n" + result.stderr)
    return tesseract, leptonica, executable


def collect_version_information(
    configured_tesseract_path: str = "",
    tcl_version: str = "",
    tk_version: str = "",
) -> VersionInformation:
    tesseract, leptonica, tesseract_path = _tesseract_versions(configured_tesseract_path)
    application = [
        VersionEntry("Dokumenten-Scanner-Sortierung", __version__),
        VersionEntry("Python", platform.python_version()),
    ]
    if tcl_version and tk_version:
        application.append(VersionEntry("Tcl/Tk", f"{tcl_version} / {tk_version}"))

    libraries = tuple(
        VersionEntry(label, _distribution_version(distribution)) for label, distribution in LIBRARIES
    )
    return VersionInformation(
        application=tuple(application),
        ocr=(VersionEntry("Tesseract OCR", tesseract), VersionEntry("Leptonica", leptonica)),
        libraries=libraries,
        tesseract_path=tesseract_path,
    )


def _distribution_version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "Nicht ermittelbar"
