from __future__ import annotations

import argparse
import hashlib
import logging
import ntpath
import os
import platform
import queue
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

from . import __version__
from .config import (
    ConfigurationError,
    Settings,
    bundled_folder,
    default_settings_path,
    find_tesseract_executable,
    load_settings,
    save_settings,
)
from .models import ProcessResult
from .version_info import VersionEntry, collect_version_information
from .watcher import FolderWatcher
from .window_launcher import activate_existing_window


ERROR_ALREADY_EXISTS = 183
WINDOWS_APP_ID = "GlasHagen.DokumentenScannerSortierung"
LOG_RETENTION_DAYS = 90
LOG_FILENAME_PATTERN = re.compile(r"^dokumentensortierer-(\d{4}-\d{2}-\d{2})\.log$")
DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOTE = 4
NO_ERROR = 0
ERROR_MORE_DATA = 234


def initial_window_geometry(screen_width: int, screen_height: int) -> tuple[int, int, int, int]:
    """Return a large centered default size that still fits on smaller displays."""
    width = min(1580, max(1000, screen_width - 40))
    height = min(1120, max(720, screen_height - 40))
    x = max(0, (screen_width - width) // 2)
    y = max(0, (screen_height - height) // 2)
    return width, height, x, y


def ui_icon_path(name: str) -> Path:
    bundle = bundled_folder()
    if bundle is not None:
        return bundle / "scanner_sorter" / "assets" / "icons" / "tabler" / f"{name}.png"
    return Path(__file__).resolve().parent / "assets" / "icons" / "tabler" / f"{name}.png"


def app_asset_path(filename: str) -> Path:
    bundle = bundled_folder()
    if bundle is not None:
        return bundle / "scanner_sorter" / "assets" / "app" / filename
    return Path(__file__).resolve().parent / "assets" / "app" / filename


def configure_windows_app_identity() -> None:
    if os.name != "nt":
        return
    import ctypes

    shell32 = ctypes.windll.shell32
    shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [ctypes.c_wchar_p]
    shell32.SetCurrentProcessExplicitAppUserModelID.restype = ctypes.c_long
    result = shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    if result != 0:
        logging.warning("Windows-App-ID konnte nicht gesetzt werden: HRESULT %s", result)


def _windows_drive_type(root: str) -> int:
    import ctypes

    kernel32 = ctypes.windll.kernel32
    kernel32.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetDriveTypeW.restype = ctypes.c_uint
    return int(kernel32.GetDriveTypeW(root))


def _windows_universal_name(local_path: str) -> str | None:
    """Resolve a mapped path to its full UNC path using the Windows network API."""
    if os.name != "nt":
        return None
    import ctypes

    class UniversalNameInfo(ctypes.Structure):
        _fields_ = [("universal_name", ctypes.c_wchar_p)]

    mpr = ctypes.WinDLL("mpr", use_last_error=True)
    function = mpr.WNetGetUniversalNameW
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    function.restype = ctypes.c_uint
    required = ctypes.c_uint(0)
    result = int(function(local_path, 1, None, ctypes.byref(required)))
    if result != ERROR_MORE_DATA or required.value == 0:
        return None
    buffer = ctypes.create_string_buffer(required.value)
    result = int(function(local_path, 1, buffer, ctypes.byref(required)))
    if result != NO_ERROR:
        return None
    universal = ctypes.cast(buffer, ctypes.POINTER(UniversalNameInfo)).contents.universal_name
    return str(universal) if universal else None


def _windows_drive_connection(drive: str) -> str | None:
    """Resolve even a remembered/disconnected mapped drive to its UNC share."""
    if os.name != "nt":
        return None
    import ctypes

    mpr = ctypes.WinDLL("mpr", use_last_error=True)
    function = mpr.WNetGetConnectionW
    function.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint)]
    function.restype = ctypes.c_uint
    size = ctypes.c_uint(1024)
    for _attempt in range(2):
        buffer = ctypes.create_unicode_buffer(size.value)
        result = int(function(drive, buffer, ctypes.byref(size)))
        if result == NO_ERROR:
            return buffer.value or None
        if result != ERROR_MORE_DATA or size.value == 0:
            return None
    return None


def canonicalize_windows_network_path(
    path: str,
    drive_type_resolver: Callable[[str], int] = _windows_drive_type,
    universal_name_resolver: Callable[[str], str | None] = _windows_universal_name,
    drive_connection_resolver: Callable[[str], str | None] = _windows_drive_connection,
) -> str:
    """Canonicalize mapped drives to UNC and fail closed if their target is unclear."""
    drive, tail = ntpath.splitdrive(path)
    if not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith(("\\", "/")):
        return path
    root = f"{drive}\\"
    drive_type = drive_type_resolver(root)
    if drive_type not in {DRIVE_REMOTE, DRIVE_UNKNOWN, DRIVE_NO_ROOT_DIR}:
        return path

    universal = universal_name_resolver(path)
    if universal and universal.startswith("\\\\"):
        return universal

    connection = drive_connection_resolver(drive)
    if connection and connection.startswith("\\\\"):
        suffix = tail.lstrip("\\/")
        return connection.rstrip("\\/") + (f"\\{suffix}" if suffix else "")

    raise OSError(
        f"Das Laufwerk {drive} ist nicht verfügbar oder sein Netzwerkziel konnte nicht sicher ermittelt "
        "werden. Verwenden Sie für den Eingangsordner einen stabilen UNC-Pfad "
        r"(z. B. \\server\freigabe\eingang)."
    )


def single_instance_identity(settings_path: Path, input_folder: str = "") -> str:
    """Return a stable identity for an app or input-folder lock.

    An input folder takes precedence.  Until it has been configured, the
    normalized settings path is used as a safe fallback.
    """
    configured_input = input_folder.strip()
    raw_path = configured_input or str(settings_path)
    expanded = os.path.expandvars(os.path.expanduser(raw_path))
    if os.name == "nt" and configured_input:
        # Resolve mapped drives before Path.resolve() can replace the mapped
        # path with a server-specific alias. This keeps a mapped and a UNC
        # configuration on the same server-wide lock identity.
        expanded = canonicalize_windows_network_path(expanded)
    try:
        absolute = Path(expanded).resolve(strict=False)
    except OSError:
        absolute = Path(os.path.abspath(expanded))
    absolute_text = str(absolute)
    normalized = os.path.normcase(os.path.normpath(absolute_text)).casefold()
    identity_type = "input" if configured_input else "settings"
    return f"{identity_type}:{normalized}"


def single_instance_mutex_name(settings_path: Path, input_folder: str = "") -> str:
    identity = single_instance_identity(settings_path, input_folder).encode("utf-8")
    digest = hashlib.sha256(identity).hexdigest()[:20]
    return f"Global\\DokumentenScannerSortierung-{digest}"


def acquire_single_instance(
    settings_path: Path,
    input_folder: str = "",
) -> tuple[bool, tuple[object, int] | None]:
    """Acquire a server-wide Windows mutex for one settings file or input folder."""
    if os.name != "nt":
        return True, None

    import ctypes

    # Global\ spans all interactive and service sessions on a Windows server.
    mutex_name = single_instance_mutex_name(settings_path, input_folder)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, mutex_name)
    error = ctypes.get_last_error()
    if not handle:
        raise OSError(error, "Einzelinstanz-Sperre konnte nicht erstellt werden.")
    if error == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False, None
    return True, (kernel32, int(handle))


def release_single_instance(instance: tuple[object, int] | None) -> None:
    if instance is None:
        return
    kernel32, handle = instance
    kernel32.CloseHandle(handle)


def notify_already_running() -> None:
    message = (
        "Die Dokumenten-Scanner-Sortierung läuft bereits.\n\n"
        "Öffnen Sie die vorhandene Anwendung über das Symbol unten rechts im Windows-Infobereich."
    )
    if os.name == "nt":
        import ctypes

        if activate_existing_window():
            return
        ctypes.windll.user32.MessageBoxW(None, message, "Anwendung läuft bereits", 0x40)
    else:
        print(message, file=sys.stderr)


def notify_configuration_error(error: ConfigurationError) -> None:
    """Show a readable startup error without exposing a Python traceback."""
    message = f"Die Anwendung kann die Einstellungen nicht laden.\n\n{error}"
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Einstellungen beschädigt", 0x10)
    else:
        print(message, file=sys.stderr)


def log_file_path(settings_path: Path, log_date: date | None = None) -> Path:
    log_date = log_date or date.today()
    return settings_path.parent / "logs" / f"dokumentensortierer-{log_date.isoformat()}.log"


def cleanup_old_logs(
    log_directory: Path,
    retention_days: int = LOG_RETENTION_DAYS,
    today: date | None = None,
) -> int:
    """Delete only dated application logs older than the retention period."""
    cutoff = (today or date.today()) - timedelta(days=max(1, retention_days))
    removed = 0
    if not log_directory.exists():
        return removed
    for path in log_directory.iterdir():
        if not path.is_file():
            continue
        match = LOG_FILENAME_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        try:
            log_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if log_date < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                logging.warning("Alte Protokolldatei konnte nicht gelöscht werden: %s", path, exc_info=True)
    return removed


class DailyFileHandler(logging.Handler):
    """Write each record to the file for its local calendar day without holding a file lock."""

    terminator = "\n"

    def __init__(self, log_directory: Path, date_provider: Callable[[], date] | None = None):
        super().__init__()
        self.log_directory = log_directory
        self.date_provider = date_provider or date.today

    @property
    def current_log_path(self) -> Path:
        return self.log_directory / f"dokumentensortierer-{self.date_provider().isoformat()}.log"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            path = self.current_log_path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(self.format(record) + self.terminator)
        except Exception:
            self.handleError(record)


def configure_logging(settings_path: Path) -> Path:
    log_path = log_file_path(settings_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [DailyFileHandler(log_path.parent)]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s [%(threadName)s]: %(message)s",
        handlers=handlers,
        force=True,
    )
    removed_logs = cleanup_old_logs(log_path.parent)
    if removed_logs:
        logging.info("Protokollbereinigung: %s Datei(en) älter als %s Tage gelöscht.", removed_logs, LOG_RETENTION_DAYS)
    configured_tesseract_path = ""
    try:
        configured_tesseract_path = load_settings(settings_path).tesseract_path
    except ConfigurationError as error:
        logging.warning("Tesseract-Pfad konnte nicht aus den Einstellungen gelesen werden: %s", error)
    except Exception:
        logging.warning(
            "Tesseract-Pfad konnte für die Versionsabfrage nicht aus den Einstellungen gelesen werden.",
            exc_info=True,
        )
    report = collect_version_information(configured_tesseract_path)
    ocr_versions = {entry.name: entry.version for entry in report.ocr}
    logging.info(
        "Anwendung gestartet; version=%s; python=%s; tesseract=%s; leptonica=%s; "
        "system=%s; architektur=%s; prozess_id=%s; protokoll=%s",
        __version__,
        platform.python_version(),
        ocr_versions.get("Tesseract OCR", "Unbekannt"),
        ocr_versions.get("Leptonica", "Unbekannt"),
        platform.platform(),
        platform.machine(),
        os.getpid(),
        log_path,
    )
    return log_path


def run_self_test() -> int:
    """Exercise packaged runtime components without opening the GUI or changing data."""
    try:
        import fitz  # noqa: F401
        import PIL  # noqa: F401
        import pypdf  # noqa: F401
        import pystray  # noqa: F401
        import pytesseract  # noqa: F401
        import tkinter
        import zxingcpp  # noqa: F401

        # This catches missing _tkinter/Tcl data in a packed executable without
        # requiring an interactive desktop or creating a visible window.
        interpreter = tkinter.Tcl()
        if not interpreter.eval("info patchlevel"):
            raise RuntimeError("Tcl/Tk-Laufzeit meldet keine Version.")

        tesseract = find_tesseract_executable()
        if tesseract is None:
            raise RuntimeError("Tesseract OCR wurde im Laufzeitpaket nicht gefunden.")
        version_result = subprocess.run(
            [str(tesseract), "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        version_output = version_result.stdout + "\n" + version_result.stderr
        if (
            version_result.returncode != 0
            or re.search(r"(?im)^tesseract\s+v?5\.5\.2\b", version_output) is None
            or re.search(r"(?im)^\s*leptonica[-\s]+1\.87\.0\b", version_output) is None
        ):
            raise RuntimeError(
                "Tesseract OCR 5.5.2 mit Leptonica 1.87.0 konnte nicht erfolgreich gestartet werden."
            )
        language_result = subprocess.run(
            [str(tesseract), "--list-langs"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        languages = set(language_result.stdout.split())
        missing = {"deu", "eng", "osd"} - languages
        if language_result.returncode != 0 or missing:
            raise RuntimeError(
                "Tesseract-Sprachmodelle fehlen: " + ", ".join(sorted(missing or {"unbekannt"}))
            )
    except Exception as error:
        logging.exception("Laufzeit-Selbsttest fehlgeschlagen: %s", error)
        if sys.stderr is not None:
            print(f"Laufzeit-Selbsttest fehlgeschlagen: {error}", file=sys.stderr)
        return 4
    return 0


class ToolTip:
    """Small delayed help bubble for buttons and other controls."""

    def __init__(self, widget: object, text: str, delay_ms: int = 450):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id: str | None = None
        self._window: object | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        import tkinter as tk

        self._after_id = None
        if self._window is not None or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx()
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 7
        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass
        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            wraplength=360,
            background="#172A3A",
            foreground="#FFFFFF",
            padx=10,
            pady=7,
            font=("Segoe UI", 9),
        )
        label.pack()
        self._window = window

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None


class SettingsWindow:
    def __init__(self, settings_path: Path, *, start_monitoring: bool = False):
        import tkinter as tk
        from tkinter import ttk

        configure_windows_app_identity()
        self.tk = tk
        self.ttk = ttk
        self.settings_path = settings_path
        self.log_path = log_file_path(settings_path)
        self.settings = load_settings(settings_path)
        self.watcher: FolderWatcher | None = None
        self._monitor_instance: tuple[object, int] | None = None
        self._worker_messages: queue.Queue[str] = queue.Queue()
        self._worker_poll_after_id: str | None = None
        self.tray_icon: object | None = None
        self._quitting = False
        self._tooltips: list[ToolTip] = []
        self._button_images: dict[tuple[str, str], object] = {}
        self._window_icon: object | None = None
        self._header_logo: object | None = None
        self._native_icon_handles: list[int] = []
        self._info_dialog: object | None = None
        self._info_logo: object | None = None

        self.root = tk.Tk()
        self.root.title("Dokumenten-Scanner-Sortierung")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width, height, x, y = initial_window_geometry(screen_width, screen_height)
        self.root.geometry(f"{width}x{height}+{x}+{y}")
        self.root.minsize(min(1100, screen_width - 40), min(800, screen_height - 40))
        self.root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        default_review_folder = self.settings.review_folder
        if not default_review_folder and self.settings.output_folder:
            default_review_folder = str(Path(self.settings.output_folder) / "Nicht_erkannt")
        self.fields: dict[str, tk.StringVar] = {
            "input_folder": tk.StringVar(value=self.settings.input_folder),
            "output_folder": tk.StringVar(value=self.settings.output_folder),
            "archive_folder": tk.StringVar(value=self.settings.archive_folder),
            "review_folder": tk.StringVar(value=default_review_folder),
            "archive_retention_days": tk.StringVar(value=str(self.settings.archive_retention_days)),
            "settle_seconds": tk.StringVar(value=str(self.settings.settle_seconds)),
            "invalid_pdf_timeout_seconds": tk.StringVar(value=str(self.settings.invalid_pdf_timeout_seconds)),
            "backlog_threshold": tk.StringVar(value=str(self.settings.backlog_threshold)),
            "backlog_pause_seconds": tk.StringVar(value=str(self.settings.backlog_pause_seconds)),
            "processing_timeout_seconds": tk.StringVar(value=str(self.settings.processing_timeout_seconds)),
        }
        self.status = tk.StringVar(value="Einstellungen speichern und Überwachung starten.")
        self._build()
        self._schedule_worker_message_poll()
        self._apply_window_icon()
        self._start_tray_icon()
        if start_monitoring:
            self.root.after(250, self._start_from_autostart)

    @staticmethod
    def _app_image(size: int) -> object:
        from PIL import Image

        with Image.open(app_asset_path("dokumenten-scanner-sortierung.png")) as source:
            return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)

    def _apply_window_icon(self) -> None:
        from PIL import ImageTk

        self._window_icon = ImageTk.PhotoImage(self._app_image(64), master=self.root)
        self.root.iconphoto(True, self._window_icon)
        if os.name == "nt":
            try:
                icon_path = str(app_asset_path("dokumenten-scanner-sortierung.ico"))
                self.root.iconbitmap(icon_path)
                self.root.update_idletasks()
                self._apply_native_windows_icon(icon_path)
            except (OSError, self.tk.TclError):
                logging.exception("Windows-Fenstersymbol konnte nicht gesetzt werden.")

    def _apply_native_windows_icon(self, icon_path: str) -> None:
        import ctypes

        image_icon = 1
        load_from_file = 0x0010
        wm_seticon = 0x0080
        icon_small = 0
        icon_big = 1
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_void_p

        child = self.root.winfo_id()
        parent = user32.GetParent(child)
        windows = {child, int(parent) if parent else child}
        small = user32.LoadImageW(None, icon_path, image_icon, 16, 16, load_from_file)
        large = user32.LoadImageW(None, icon_path, image_icon, 32, 32, load_from_file)
        self._native_icon_handles = [int(handle) for handle in (small, large) if handle]
        for window in windows:
            if small:
                user32.SendMessageW(window, wm_seticon, icon_small, small)
            if large:
                user32.SendMessageW(window, wm_seticon, icon_big, large)

    def _configure_styles(self) -> None:
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except self.tk.TclError:
            pass
        self.root.option_add("*Font", "{Segoe UI} 9")
        style.configure(
            "Modern.TEntry",
            padding=(10, 7),
            fieldbackground="#FFFFFF",
            foreground="#1B2B3A",
            bordercolor="#C9D5DF",
            lightcolor="#C9D5DF",
            darkcolor="#C9D5DF",
        )
        style.map("Modern.TEntry", bordercolor=[("focus", "#2374AB")], lightcolor=[("focus", "#2374AB")])
        button_styles = {
            "Primary.TButton": ("#176FA6", "#FFFFFF", "#125B89"),
            "Secondary.TButton": ("#E8EEF3", "#243746", "#D8E2EA"),
            "Quiet.TButton": ("#FFFFFF", "#355064", "#EDF2F6"),
            "Header.TButton": ("#284B62", "#EAF3F8", "#365F78"),
            "Danger.TButton": ("#B63A3A", "#FFFFFF", "#952F2F"),
        }
        for name, (background, foreground, active) in button_styles.items():
            style.configure(
                name,
                padding=(13, 8),
                background=background,
                foreground=foreground,
                borderwidth=0,
                focusthickness=1,
                focuscolor=background,
                font=("Segoe UI Semibold", 9),
            )
            style.map(
                name,
                background=[("disabled", "#DDE4E9"), ("pressed", active), ("active", active)],
                foreground=[("disabled", "#91A0AA")],
            )
        style.configure("Header.TButton", padding=(11, 6), font=("Segoe UI Semibold", 8))

    def _card(self, parent: object, title: str, subtitle: str) -> tuple[object, object, object]:
        card = self.tk.Frame(
            parent,
            background="#FFFFFF",
            highlightbackground="#D8E2EA",
            highlightthickness=1,
            bd=0,
        )
        header = self.tk.Frame(card, background="#FFFFFF")
        header.pack(fill="x", padx=18, pady=(14, 10))
        text_frame = self.tk.Frame(header, background="#FFFFFF")
        text_frame.pack(side="left", fill="x", expand=True)
        self.tk.Label(
            text_frame,
            text=title,
            background="#FFFFFF",
            foreground="#172A3A",
            font=("Segoe UI Semibold", 11),
        ).pack(anchor="w")
        self.tk.Label(
            text_frame,
            text=subtitle,
            background="#FFFFFF",
            foreground="#6B7D8B",
            font=("Segoe UI", 8),
            wraplength=380,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))
        body = self.tk.Frame(card, background="#FFFFFF")
        body.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        return card, body, header

    def _button(
        self,
        parent: object,
        text: str,
        command: object,
        tooltip: str,
        style: str = "Secondary.TButton",
        state: str = "normal",
        icon: str | None = None,
    ) -> object:
        options: dict[str, object] = {
            "text": text,
            "command": command,
            "style": style,
            "state": state,
        }
        if icon is not None:
            color = "#FFFFFF" if style in {"Primary.TButton", "Danger.TButton"} else "#355064"
            options["image"] = self._button_icon(icon, color)
            options["compound"] = "left"
        button = self.ttk.Button(parent, **options)
        self._tooltips.append(ToolTip(button, tooltip))
        return button

    def _button_icon(self, name: str, color: str) -> object:
        key = (name, color)
        if key in self._button_images:
            return self._button_images[key]

        from PIL import Image, ImageTk

        source = Image.open(ui_icon_path(name)).convert("RGBA")
        icon = Image.new("RGBA", source.size, color)
        icon.putalpha(source.getchannel("A"))
        photo = ImageTk.PhotoImage(icon, master=self.root)
        self._button_images[key] = photo
        return photo

    def _build(self) -> None:
        from tkinter import filedialog, messagebox

        self._configure_styles()
        root = self.root
        root.configure(background="#F2F5F8")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        shell = self.tk.Frame(root, background="#F2F5F8")
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(1, weight=1)

        header = self.tk.Frame(shell, background="#17354B", height=92)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(1, weight=1)
        from PIL import ImageTk

        self._header_logo = ImageTk.PhotoImage(self._app_image(54), master=self.root)
        logo = self.tk.Label(
            header,
            image=self._header_logo,
            background="#17354B",
            borderwidth=0,
        )
        logo.grid(row=0, column=0, rowspan=2, padx=(22, 14), pady=18)
        self.tk.Label(
            header,
            text="Dokumenten-Scanner-Sortierung",
            background="#17354B",
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 17),
        ).grid(row=0, column=1, sticky="sw", pady=(18, 0))
        self.tk.Label(
            header,
            text="Scans automatisch erkennen, trennen, benennen und sicher ablegen",
            background="#17354B",
            foreground="#BFD0DC",
            font=("Segoe UI", 9),
        ).grid(row=1, column=1, sticky="nw", pady=(2, 18))
        badges = self.tk.Frame(header, background="#17354B")
        badges.grid(row=0, column=2, rowspan=2, padx=(14, 22), pady=20, sticky="e")
        self.header_status_badge = self.tk.Label(
            badges,
            text="●  BEREIT",
            background="#E8EEF2",
            foreground="#48606F",
            font=("Segoe UI Semibold", 8),
            padx=11,
            pady=6,
        )
        self.header_status_badge.pack(side="left", padx=(0, 8))
        self._button(
            badges,
            "Info",
            self.show_version_info,
            "Zeigt die Versionen der Anwendung, der OCR-Komponenten und der verwendeten Bibliotheken.",
            style="Header.TButton",
        ).pack(side="left")

        content = self.tk.Frame(shell, background="#F2F5F8")
        content.grid(row=1, column=0, sticky="nsew", padx=20, pady=12)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(2, weight=1, minsize=190)

        folder_card, folder_body, _folder_header = self._card(
            content,
            "Ordner & Ablage",
            "Serverpfade für Eingang, Ausgabe, Originalarchiv und manuelle Prüfung",
        )
        folder_card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        folder_body.columnconfigure(1, weight=1)
        folders = [
            ("Eingangsordner", "input_folder", "Neue PDFs vom Scanner"),
            ("Zielordner", "output_folder", "Erkannte und benannte Dokumente"),
            ("Archivordner", "archive_folder", "Unveränderte Originalscans"),
            ("Prüfordner", "review_folder", "Kopien nicht erkannter Scans"),
        ]
        for row, (label, field, help_text) in enumerate(folders):
            label_frame = self.tk.Frame(folder_body, background="#FFFFFF")
            label_frame.grid(row=row, column=0, sticky="w", padx=(0, 14), pady=5)
            self.tk.Label(
                label_frame,
                text=label,
                background="#FFFFFF",
                foreground="#233746",
                font=("Segoe UI Semibold", 9),
            ).pack(anchor="w")
            self.tk.Label(
                label_frame,
                text=help_text,
                background="#FFFFFF",
                foreground="#7A8A96",
                font=("Segoe UI", 8),
                wraplength=145,
                justify="left",
            ).pack(anchor="w", pady=(1, 0))
            self.ttk.Entry(folder_body, textvariable=self.fields[field], style="Modern.TEntry").grid(
                row=row, column=1, sticky="ew", pady=5
            )
            browse = self._button(
                folder_body,
                "Auswählen",
                lambda name=field: self._choose_folder(name, filedialog),
                f"{label} im Windows-Dateidialog auswählen. Auch Netzwerk- und Serverpfade sind möglich.",
                "Quiet.TButton",
                icon="folder-open",
            )
            browse.grid(row=row, column=2, padx=(10, 0), pady=5)

        processing_card, processing_body, _processing_header = self._card(
            content,
            "Verarbeitung",
            "Sicherheits- und Aufbewahrungseinstellungen für den laufenden Betrieb",
        )
        processing_card.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        processing_body.columnconfigure(0, weight=1)
        processing_body.columnconfigure(1, weight=1)
        processing_field_rows = (
            (
                ("Archiv-Aufbewahrung", "Originale nach dieser Anzahl Tage löschen", "archive_retention_days"),
                ("Dateistabilität", "Wartezeit nach der letzten Dateiänderung", "settle_seconds"),
            ),
            (
                ("Stapelgrenze", "Ab dieser Anzahl wartender PDFs wird gedrosselt", "backlog_threshold"),
                ("Stapelpause", "Pause zwischen Dokumenten im Stapelmodus (Sekunden)", "backlog_pause_seconds"),
            ),
        )
        for row, fields in enumerate(processing_field_rows):
            for column, (label, help_text, field) in enumerate(fields):
                panel = self.tk.Frame(processing_body, background="#FFFFFF")
                panel.grid(
                    row=row,
                    column=column,
                    sticky="ew",
                    padx=((0, 10) if column == 0 else (10, 0)),
                    pady=((0, 0) if row == 0 else (12, 0)),
                )
                panel.columnconfigure(0, weight=1)
                self.tk.Label(
                    panel, text=label, background="#FFFFFF", foreground="#233746", font=("Segoe UI Semibold", 9)
                ).grid(row=0, column=0, sticky="w")
                self.tk.Label(
                    panel,
                    text=help_text,
                    background="#FFFFFF",
                    foreground="#7A8A96",
                    font=("Segoe UI", 8),
                    wraplength=155,
                    justify="left",
                ).grid(row=1, column=0, sticky="w", pady=(1, 5))
                self.ttk.Entry(panel, textvariable=self.fields[field], style="Modern.TEntry").grid(
                    row=2, column=0, sticky="ew"
                )

        invalid_pdf_panel = self.tk.Frame(processing_body, background="#FFFFFF")
        invalid_pdf_panel.grid(row=2, column=0, sticky="ew", padx=(0, 10), pady=(12, 0))
        invalid_pdf_panel.columnconfigure(0, weight=1)
        self.tk.Label(
            invalid_pdf_panel,
            text="Beschädigte PDF weiterleiten nach (Sekunden)",
            background="#FFFFFF",
            foreground="#233746",
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, sticky="w")
        self.tk.Label(
            invalid_pdf_panel,
            text="Wartezeit ohne Dateiänderung, bevor eine unvollständige PDF zur Prüfung weitergeleitet wird",
            background="#FFFFFF",
            foreground="#7A8A96",
            font=("Segoe UI", 8),
            wraplength=155,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(1, 5))
        self.ttk.Entry(
            invalid_pdf_panel,
            textvariable=self.fields["invalid_pdf_timeout_seconds"],
            style="Modern.TEntry",
        ).grid(row=2, column=0, sticky="ew")

        processing_timeout_panel = self.tk.Frame(processing_body, background="#FFFFFF")
        processing_timeout_panel.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=(12, 0))
        processing_timeout_panel.columnconfigure(0, weight=1)
        self.tk.Label(
            processing_timeout_panel,
            text="OCR-Gesamtlimit (Sekunden)",
            background="#FFFFFF",
            foreground="#233746",
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, sticky="w")
        self.tk.Label(
            processing_timeout_panel,
            text="Maximale OCR-Zeit für einen Scan; verhindert lange Blockaden",
            background="#FFFFFF",
            foreground="#7A8A96",
            font=("Segoe UI", 8),
            wraplength=155,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(1, 5))
        self.ttk.Entry(
            processing_timeout_panel,
            textvariable=self.fields["processing_timeout_seconds"],
            style="Modern.TEntry",
        ).grid(row=2, column=0, sticky="ew")

        notice = self.tk.Frame(processing_body, background="#EAF4FA")
        notice.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        self.tk.Label(
            notice,
            text="i",
            background="#2B7EB3",
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 9),
            width=3,
            pady=7,
        ).pack(side="left")
        self.tk.Label(
            notice,
            text=(
                "Nicht erkannt oder beschädigt: Original ins Ziel, zusätzliche Kopie in den Prüfordner."
            ),
            background="#EAF4FA",
            foreground="#315B73",
            font=("Segoe UI", 8),
            padx=10,
            pady=7,
            wraplength=320,
            justify="left",
        ).pack(side="left", fill="x", expand=True)

        control_card, control_body, _control_header = self._card(
            content,
            "Steuerung",
            "Einstellungen sichern und die Ordnerüberwachung bedienen",
        )
        control_card.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        actions = self.tk.Frame(control_body, background="#FFFFFF")
        actions.pack(fill="x")
        self.save_button = self._button(
            actions,
            "Einstellungen speichern",
            self.save,
            "Prüft alle Angaben und speichert sie dauerhaft, ohne die Überwachung zu starten.",
            "Secondary.TButton",
            icon="device-floppy",
        )
        self.save_button.pack(side="left")
        self.start_button = self._button(
            actions,
            "Überwachung starten",
            self.start,
            "Speichert die Einstellungen und beginnt anschließend mit der automatischen Verarbeitung neuer PDFs.",
            "Primary.TButton",
            icon="player-play",
        )
        self.start_button.pack(side="left", padx=(8, 0))
        self.stop_button = self._button(
            actions,
            "Überwachung beenden",
            self.stop,
            "Stoppt die Ordnerüberwachung. Die Anwendung und bereits erzeugte Dateien bleiben erhalten.",
            "Secondary.TButton",
            "disabled",
            icon="player-stop",
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        quit_button = self._button(
            actions,
            "Anwendung beenden",
            self.quit_application,
            "Beendet die Überwachung und schließt die Anwendung vollständig.",
            "Danger.TButton",
            icon="power",
        )
        quit_button.pack(side="right")
        hide_button = self._button(
            actions,
            "In Infobereich ausblenden",
            self.hide_to_tray,
            "Blendet nur das Fenster aus. Die Anwendung läuft unten rechts im Windows-Infobereich weiter.",
            "Quiet.TButton",
            icon="window-minimize",
        )
        hide_button.pack(side="right", padx=(0, 8))

        status_box = self.tk.Frame(control_body, background="#F1F7F3")
        status_box.pack(fill="x", pady=(12, 0))
        self.tk.Label(
            status_box,
            text="STATUS",
            background="#DDEFE2",
            foreground="#2E6840",
            font=("Segoe UI Semibold", 8),
            padx=9,
            pady=7,
        ).pack(side="left")
        self.status_label = self.tk.Label(
            status_box,
            textvariable=self.status,
            background="#F1F7F3",
            foreground="#365C43",
            font=("Segoe UI", 8),
            padx=10,
            pady=7,
            anchor="w",
        )
        self.status_label.pack(side="left", fill="x", expand=True)

        log_card, log_body, log_header = self._card(
            content,
            "Aktivitätsprotokoll",
            "Letzte Ereignisse, Verarbeitungsergebnisse und Fehlermeldungen",
        )
        log_card.grid(row=2, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        open_log_button = self._button(
            log_header,
            "Protokollordner öffnen",
            self._open_log_folder,
            "Öffnet den Ordner mit der dauerhaften Logdatei und den älteren Protokollen.",
            "Quiet.TButton",
            icon="folder-open",
        )
        open_log_button.pack(side="right")
        log_body.columnconfigure(0, weight=1)
        log_body.rowconfigure(0, weight=1)
        self.activity_log = self.tk.Text(
            log_body,
            height=7,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            background="#F7F9FB",
            foreground="#263A49",
            selectbackground="#B9D8EC",
            relief="flat",
            padx=10,
            pady=8,
            highlightbackground="#E2E8ED",
            highlightthickness=1,
        )
        self.activity_log.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = self.ttk.Scrollbar(log_body, orient="vertical", command=self.activity_log.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.activity_log.configure(yscrollcommand=log_scrollbar.set)
        self._append_activity(f"Protokolldatei: {self.log_path}")

        self._messagebox = messagebox

    def _choose_folder(self, field: str, filedialog: object) -> None:
        directory = filedialog.askdirectory(initialdir=self.fields[field].get() or None)
        if directory:
            self.fields[field].set(directory)

    def _append_activity(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.activity_log.configure(state="normal")
        self.activity_log.insert("end", f"{timestamp}  {message}\n")
        self.activity_log.see("end")
        self.activity_log.configure(state="disabled")

    def _open_log_folder(self) -> None:
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            os.startfile(str(self.log_path.parent))
        except Exception as error:
            self._messagebox.showerror("Protokollordner", f"Ordner konnte nicht geöffnet werden:\n{error}")

    def show_version_info(self) -> None:
        if self._info_dialog is not None and self._info_dialog.winfo_exists():
            self._info_dialog.deiconify()
            self._info_dialog.lift()
            self._info_dialog.focus_force()
            return

        report = collect_version_information(
            self.settings.tesseract_path,
            str(self.tk.TclVersion),
            str(self.tk.TkVersion),
        )
        dialog = self.tk.Toplevel(self.root)
        self._info_dialog = dialog
        dialog.title("Info – Dokumenten-Scanner-Sortierung")
        dialog.configure(background="#F2F5F8")
        dialog.resizable(False, False)
        dialog.transient(self.root)

        def close() -> None:
            self._info_dialog = None
            self._info_logo = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close)
        header = self.tk.Frame(dialog, background="#17354B", height=84)
        header.pack(fill="x")
        header.pack_propagate(False)
        from PIL import ImageTk

        self._info_logo = ImageTk.PhotoImage(self._app_image(42), master=dialog)
        self.tk.Label(
            header,
            image=self._info_logo,
            background="#17354B",
        ).pack(side="left", padx=(20, 14), pady=18)
        title_box = self.tk.Frame(header, background="#17354B")
        title_box.pack(side="left", fill="y", pady=16)
        self.tk.Label(
            title_box,
            text="Info & Versionen",
            background="#17354B",
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 15),
        ).pack(anchor="w")
        self.tk.Label(
            title_box,
            text=f"Dokumenten-Scanner-Sortierung {__version__}",
            background="#17354B",
            foreground="#BFD0DC",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        content = self.tk.Frame(dialog, background="#F2F5F8")
        content.pack(fill="both", expand=True, padx=20, pady=16)

        def section(title: str, entries: tuple[VersionEntry, ...]) -> None:
            card = self.tk.Frame(
                content,
                background="#FFFFFF",
                highlightbackground="#D8E2EA",
                highlightthickness=1,
            )
            card.pack(fill="x", pady=(0, 10))
            self.tk.Label(
                card,
                text=title,
                background="#FFFFFF",
                foreground="#172A3A",
                font=("Segoe UI Semibold", 10),
            ).grid(row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(11, 7))
            card.columnconfigure(0, weight=1)
            for row, entry in enumerate(entries, start=1):
                background = "#F7F9FB" if row % 2 else "#FFFFFF"
                self.tk.Label(
                    card,
                    text=entry.name,
                    background=background,
                    foreground="#355064",
                    anchor="w",
                    padx=10,
                    pady=5,
                ).grid(row=row, column=0, sticky="ew", padx=(14, 0))
                self.tk.Label(
                    card,
                    text=entry.version,
                    background=background,
                    foreground="#172A3A",
                    font=("Segoe UI Semibold", 9),
                    anchor="e",
                    padx=10,
                    pady=5,
                ).grid(row=row, column=1, sticky="ew", padx=(0, 14))
            card.grid_columnconfigure(1, minsize=170)
            self.tk.Frame(card, background="#FFFFFF", height=8).grid(
                row=len(entries) + 1,
                column=0,
                columnspan=2,
            )

        section("Anwendung & Laufzeit", report.application)
        section("OCR-Komponenten", report.ocr)
        section("Verwendete Bibliotheken", report.libraries)

        if report.tesseract_path is None:
            tesseract_source = "Tesseract wurde nicht gefunden."
        elif bundled_folder() is not None:
            tesseract_source = "Tesseract wird direkt mit der Anwendung mitgeliefert."
        else:
            tesseract_source = f"Tesseract: {report.tesseract_path}"
        self.tk.Label(
            content,
            text=tesseract_source,
            background="#E7F1F7",
            foreground="#365C73",
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=620,
            padx=12,
            pady=8,
        ).pack(fill="x")

        footer = self.tk.Frame(dialog, background="#F2F5F8")
        footer.pack(fill="x", padx=20, pady=(0, 18))
        self._button(
            footer,
            "Schließen",
            close,
            "Schließt die Versionsübersicht.",
            style="Primary.TButton",
        ).pack(side="right")

        dialog.update_idletasks()
        width = 660
        height = min(780, dialog.winfo_reqheight(), self.root.winfo_screenheight() - 60)
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 2)
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.grab_set()
        dialog.focus_force()

    @staticmethod
    def _tray_image() -> object:
        return SettingsWindow._app_image(64)

    def _start_tray_icon(self) -> None:
        try:
            import pystray

            menu = pystray.Menu(
                pystray.MenuItem("Fenster öffnen", self._tray_show, default=True),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Überwachung starten", self._tray_start),
                pystray.MenuItem("Überwachung beenden", self._tray_stop),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Anwendung beenden", self._tray_quit),
            )
            self.tray_icon = pystray.Icon(
                "DokumentenScannerSortierung",
                self._tray_image(),
                f"Dokumenten-Scanner-Sortierung {__version__}",
                menu,
            )
            self.tray_icon.run_detached()
            self._update_tray_status()
            logging.info("Symbol im Windows-Infobereich gestartet.")
        except Exception:
            self.tray_icon = None
            logging.exception("Symbol im Windows-Infobereich konnte nicht gestartet werden.")

    def _schedule_from_tray(self, callback: object) -> None:
        try:
            self.root.after(0, callback)
        except RuntimeError:
            pass

    def _tray_show(self, _icon: object, _item: object) -> None:
        self._schedule_from_tray(self.show_window)

    def _tray_start(self, _icon: object, _item: object) -> None:
        self._schedule_from_tray(self.start)

    def _tray_stop(self, _icon: object, _item: object) -> None:
        self._schedule_from_tray(self.stop)

    def _tray_quit(self, _icon: object, _item: object) -> None:
        self._schedule_from_tray(self.quit_application)

    def _update_tray_status(self) -> None:
        if self.tray_icon is None:
            return
        active = bool(self.watcher and self.watcher.running)
        status = "Überwachung aktiv" if active else "Überwachung nicht gestartet"
        self.tray_icon.title = f"Dokumenten-Scanner-Sortierung {__version__} – {status}"

    def _update_monitoring_badge(self) -> None:
        active = bool(self.watcher and self.watcher.running)
        if active:
            self.header_status_badge.configure(
                text="●  ÜBERWACHUNG AKTIV",
                background="#DDF2E4",
                foreground="#26713D",
            )
        else:
            self.header_status_badge.configure(
                text="●  BEREIT",
                background="#E8EEF2",
                foreground="#48606F",
            )

    def show_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide_to_tray(self) -> None:
        if self.tray_icon is None:
            self.quit_application()
            return
        self.root.withdraw()
        logging.info("Fenster in den Windows-Infobereich ausgeblendet.")

    def _start_from_autostart(self) -> None:
        """Start monitoring after logon and stay accessible through the tray icon."""
        self.start()
        if self.watcher and self.watcher.running and self.tray_icon is not None:
            self.hide_to_tray()

    def _current_settings(self) -> Settings:
        try:
            retention = int(self.fields["archive_retention_days"].get())
            settle = int(self.fields["settle_seconds"].get())
            invalid_pdf_timeout = int(self.fields["invalid_pdf_timeout_seconds"].get())
            backlog_threshold = int(self.fields["backlog_threshold"].get())
            backlog_pause = int(self.fields["backlog_pause_seconds"].get())
            processing_timeout = int(self.fields["processing_timeout_seconds"].get())
        except ValueError as error:
            raise ValueError(
                "Aufbewahrung, Wartezeiten, Stapelgrenze und OCR-Gesamtlimit müssen ganze Zahlen sein."
            ) from error

        return Settings(
            input_folder=self.fields["input_folder"].get().strip(),
            output_folder=self.fields["output_folder"].get().strip(),
            archive_folder=self.fields["archive_folder"].get().strip(),
            review_folder=self.fields["review_folder"].get().strip(),
            archive_retention_days=retention,
            settle_seconds=settle,
            invalid_pdf_timeout_seconds=invalid_pdf_timeout,
            poll_interval_seconds=1,
            backlog_threshold=backlog_threshold,
            backlog_pause_seconds=backlog_pause,
            processing_timeout_seconds=processing_timeout,
            # Tesseract is bundled with the application. Preserve a legacy
            # override without exposing it in the normal operating interface.
            tesseract_path=self.settings.tesseract_path,
            ocr_languages=self.settings.ocr_languages,
        )

    def save(self) -> Settings | None:
        if self.watcher and self.watcher.running:
            self._messagebox.showwarning(
                "Überwachung aktiv",
                "Die Einstellungen können während der laufenden Überwachung nicht geändert werden. "
                "Beenden Sie zuerst die Überwachung.",
            )
            return None
        try:
            settings = self._current_settings()
        except ValueError as error:
            self._messagebox.showerror("Ungültige Einstellung", str(error))
            return None
        errors = settings.validate()
        if errors:
            self._messagebox.showerror("Ungültige Einstellung", "\n".join(errors))
            return None

        save_settings(settings, self.settings_path)
        self.settings = settings
        self.status.set(f"Einstellungen gespeichert: {self.settings_path}")
        self._append_activity("Einstellungen gespeichert.")
        return settings

    def start(self) -> None:
        if self.watcher and self.watcher.running:
            return
        if self._monitor_instance is not None:
            release_single_instance(self._monitor_instance)
            self._monitor_instance = None
        settings = self.save()
        if settings is None:
            return
        try:
            acquired, monitor_instance = acquire_single_instance(
                self.settings_path,
                settings.input_folder,
            )
        except OSError as error:
            self._messagebox.showerror(
                "Überwachungssperre nicht verfügbar",
                "Der Eingangsordner kann nicht sicher gegen eine zweite Instanz gesperrt werden.\n\n"
                f"{error}",
            )
            return
        if not acquired:
            self._messagebox.showerror(
                "Eingangsordner bereits überwacht",
                "Der konfigurierte Eingangsordner wird bereits von einer anderen Instanz oder "
                "Serversitzung überwacht.",
            )
            return
        self._monitor_instance = monitor_instance
        try:
            self.watcher = FolderWatcher(settings, self._from_worker, self._result_from_worker)
            self.watcher.start()
        except Exception as error:
            release_single_instance(self._monitor_instance)
            self._monitor_instance = None
            self.watcher = None
            self._messagebox.showerror("Start fehlgeschlagen", str(error))
            return
        self.save_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._update_monitoring_badge()
        self._update_tray_status()

    def stop(self) -> None:
        if self.watcher and self.watcher.running:
            if self.watcher.processing:
                message = "Überwachung wird beendet; der laufende Vorgang wird sicher abgeschlossen."
            else:
                message = "Überwachung wird kontrolliert beendet."
            self.status.set(message)
            self._append_activity(message)
            self.stop_button.configure(state="disabled")
            self.root.update_idletasks()
            try:
                self.watcher.stop()
            finally:
                release_single_instance(self._monitor_instance)
                self._monitor_instance = None
        elif self._monitor_instance is not None:
            release_single_instance(self._monitor_instance)
            self._monitor_instance = None
        self.save_button.configure(state="normal")
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._update_monitoring_badge()
        self._update_tray_status()

    def _from_worker(self, message: str) -> None:
        # Worker callbacks must never call Tk.  Tk may currently be waiting for
        # the worker during a controlled shutdown and is not thread-safe.
        self._worker_messages.put(message)

    def _schedule_worker_message_poll(self) -> None:
        if self._quitting:
            return
        self._worker_poll_after_id = self.root.after(100, self._poll_worker_messages)

    def _poll_worker_messages(self) -> None:
        """Drain worker events exclusively in the Tk main thread."""
        self._worker_poll_after_id = None
        if self._quitting:
            return
        while True:
            try:
                message = self._worker_messages.get_nowait()
            except queue.Empty:
                break
            self.status.set(message)
            self._append_activity(message)
        self._schedule_worker_message_poll()

    def _cancel_worker_message_poll(self) -> None:
        after_id = self._worker_poll_after_id
        self._worker_poll_after_id = None
        if after_id is None:
            return
        try:
            self.root.after_cancel(after_id)
        except self.tk.TclError:
            pass

    def _result_from_worker(self, result: ProcessResult) -> None:
        # The worker sends the user-facing result immediately afterwards via on_status.
        # Detailed processing data is already written once by DocumentProcessor.
        pass

    def quit_application(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self._cancel_worker_message_poll()
        self.stop()
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self._cancel_worker_message_poll()
            if self.watcher is not None:
                self.watcher.stop()
            release_single_instance(self._monitor_instance)
            self._monitor_instance = None
            if self.tray_icon is not None:
                self.tray_icon.stop()
                self.tray_icon = None


def run_headless(settings_path: Path) -> int:
    try:
        settings = load_settings(settings_path)
    except ConfigurationError as error:
        logging.error("Einstellungen konnten nicht geladen werden: %s", error)
        print(str(error), file=sys.stderr)
        return 2
    errors = settings.validate()
    if errors:
        print("Ungültige Einstellungen:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 2

    try:
        acquired, monitor_instance = acquire_single_instance(settings_path, settings.input_folder)
    except OSError as error:
        message = f"Der Eingangsordner kann nicht sicher gesperrt werden: {error}"
        logging.error(message)
        print(message, file=sys.stderr)
        return 3
    if not acquired:
        message = "Der konfigurierte Eingangsordner wird bereits von einer anderen Serversitzung überwacht."
        logging.error(message)
        print(message, file=sys.stderr)
        return 3

    finished = threading.Event()
    watcher = FolderWatcher(
        settings,
        on_status=lambda message: logging.info(message),
        on_result=lambda _result: None,
    )

    def stop(_signal: int, _frame: object) -> None:
        finished.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        watcher.start()
        finished.wait()
    finally:
        watcher.stop()
        release_single_instance(monitor_instance)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dokumenten-Scanner-Sortierung")
    parser.add_argument("--run", action="store_true", help="Überwachung ohne Benutzeroberfläche starten")
    parser.add_argument(
        "--autostart",
        action="store_true",
        help="Nach der Windows-Anmeldung Überwachung starten und in den Infobereich ausblenden",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Mitgelieferte Laufzeitkomponenten prüfen und ohne Datenänderung beenden",
    )
    parser.add_argument("--settings", type=Path, default=default_settings_path(), help="Pfad zur settings.json")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    try:
        acquired, instance = acquire_single_instance(args.settings)
    except OSError as error:
        logging.error("Einzelinstanz-Sperre konnte nicht erstellt werden: %s", error)
        if sys.stderr is not None:
            print(f"Anwendungssperre konnte nicht erstellt werden: {error}", file=sys.stderr)
        return 3
    if not acquired:
        notify_already_running()
        return 0

    try:
        configure_logging(args.settings)
        logging.info(
            "Startmodus; modus=%s; einstellungen=%s",
            "Dienst/Headless" if args.run else "Benutzeroberfläche",
            args.settings,
        )

        try:
            if args.run:
                return run_headless(args.settings)

            window = SettingsWindow(args.settings, start_monitoring=args.autostart)
            window.run()
            return 0
        except ConfigurationError as error:
            logging.error("Einstellungen konnten nicht geladen werden: %s", error)
            notify_configuration_error(error)
            return 2
    finally:
        logging.info("Anwendung beendet; version=%s", __version__)
        release_single_instance(instance)


if __name__ == "__main__":
    raise SystemExit(main())
