from __future__ import annotations

import argparse
import hashlib
import logging
import os
import signal
import sys
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __version__
from .config import Settings, default_settings_path, load_settings, save_settings
from .models import ProcessResult
from .watcher import FolderWatcher


ERROR_ALREADY_EXISTS = 183


def acquire_single_instance(settings_path: Path) -> tuple[bool, tuple[object, int] | None]:
    """Acquire one Windows mutex per settings file, independent of the EXE filename."""
    if os.name != "nt":
        return True, None

    import ctypes

    settings_key = str(settings_path.resolve()).casefold().encode("utf-8")
    digest = hashlib.sha256(settings_key).hexdigest()[:20]
    mutex_name = f"Local\\DokumentenScannerSortierung-{digest}"
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

        user32 = ctypes.windll.user32
        windows: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def find_window(window: int, _parameter: int) -> bool:
            length = user32.GetWindowTextLengthW(window)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(window, title, length + 1)
                if title.value.startswith("Dokumenten-Scanner-Sortierung"):
                    windows.append(window)
                    return False
            return True

        user32.EnumWindows(callback_type(find_window), 0)
        if windows:
            user32.ShowWindow(windows[0], 9)
            user32.SetForegroundWindow(windows[0])
            return
        user32.MessageBoxW(None, message, "Anwendung läuft bereits", 0x40)
    else:
        print(message, file=sys.stderr)


def log_file_path(settings_path: Path) -> Path:
    return settings_path.parent / "logs" / "dokumentensortierer.log"


def configure_logging(settings_path: Path) -> Path:
    log_path = log_file_path(settings_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
    ]
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.info("Anwendung gestartet; Version %s", __version__)
    return log_path


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
    def __init__(self, settings_path: Path):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.settings_path = settings_path
        self.log_path = log_file_path(settings_path)
        self.settings = load_settings(settings_path)
        self.watcher: FolderWatcher | None = None
        self.tray_icon: object | None = None
        self._quitting = False
        self._tooltips: list[ToolTip] = []

        self.root = tk.Tk()
        self.root.title(f"Dokumenten-Scanner-Sortierung – Version {__version__}")
        self.root.geometry("980x820")
        self.root.minsize(900, 740)
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
            "tesseract_path": tk.StringVar(value=self.settings.tesseract_path),
        }
        self.status = tk.StringVar(value="Einstellungen speichern und Überwachung starten.")
        self._build()
        self._start_tray_icon()

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
    ) -> object:
        button = self.ttk.Button(parent, text=text, command=command, style=style, state=state)
        self._tooltips.append(ToolTip(button, tooltip))
        return button

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
        logo = self.tk.Label(
            header,
            text="DS",
            background="#2B7EB3",
            foreground="#FFFFFF",
            font=("Segoe UI Semibold", 16),
            width=3,
            height=2,
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
        self.tk.Label(
            badges,
            text=f"Version {__version__}",
            background="#284B62",
            foreground="#EAF3F8",
            font=("Segoe UI Semibold", 8),
            padx=11,
            pady=6,
        ).pack(side="left")

        content = self.tk.Frame(shell, background="#F2F5F8")
        content.grid(row=1, column=0, sticky="nsew", padx=20, pady=16)
        content.columnconfigure(0, weight=3)
        content.columnconfigure(1, weight=2)
        content.rowconfigure(2, weight=1)

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
        processing_fields = [
            ("Archiv-Aufbewahrung", "Originale nach dieser Anzahl Tage löschen", "archive_retention_days"),
            ("Dateistabilität", "Wartezeit nach der letzten Dateiänderung", "settle_seconds"),
        ]
        for column, (label, help_text, field) in enumerate(processing_fields):
            panel = self.tk.Frame(processing_body, background="#FFFFFF")
            panel.grid(row=0, column=column, sticky="ew", padx=((0, 10) if column == 0 else (10, 0)))
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

        tesseract_panel = self.tk.Frame(processing_body, background="#FFFFFF")
        tesseract_panel.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        tesseract_panel.columnconfigure(0, weight=1)
        self.tk.Label(
            tesseract_panel,
            text="Tesseract-Pfad",
            background="#FFFFFF",
            foreground="#233746",
            font=("Segoe UI Semibold", 9),
        ).grid(row=0, column=0, sticky="w")
        self.tk.Label(
            tesseract_panel,
            text="Optional – leer bedeutet: mitgelieferte OCR verwenden",
            background="#FFFFFF",
            foreground="#7A8A96",
            font=("Segoe UI", 8),
            wraplength=330,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(1, 5))
        self.ttk.Entry(tesseract_panel, textvariable=self.fields["tesseract_path"], style="Modern.TEntry").grid(
            row=2, column=0, sticky="ew"
        )
        notice = self.tk.Frame(processing_body, background="#EAF4FA")
        notice.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
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
                "Nicht erkannt: Original ins Ziel, zusätzliche Kopie in den Prüfordner."
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
        save_button = self._button(
            actions,
            "Einstellungen speichern",
            self.save,
            "Prüft alle Angaben und speichert sie dauerhaft, ohne die Überwachung zu starten.",
            "Secondary.TButton",
        )
        save_button.pack(side="left")
        self.start_button = self._button(
            actions,
            "Überwachung starten",
            self.start,
            "Speichert die Einstellungen und beginnt anschließend mit der automatischen Verarbeitung neuer PDFs.",
            "Primary.TButton",
        )
        self.start_button.pack(side="left", padx=(8, 0))
        self.stop_button = self._button(
            actions,
            "Überwachung beenden",
            self.stop,
            "Stoppt die Ordnerüberwachung. Die Anwendung und bereits erzeugte Dateien bleiben erhalten.",
            "Secondary.TButton",
            "disabled",
        )
        self.stop_button.pack(side="left", padx=(8, 0))
        quit_button = self._button(
            actions,
            "Anwendung beenden",
            self.quit_application,
            "Beendet die Überwachung und schließt die Anwendung vollständig.",
            "Danger.TButton",
        )
        quit_button.pack(side="right")
        hide_button = self._button(
            actions,
            "In Infobereich ausblenden",
            self.hide_to_tray,
            "Blendet nur das Fenster aus. Die Anwendung läuft unten rechts im Windows-Infobereich weiter.",
            "Quiet.TButton",
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

    @staticmethod
    def _tray_image() -> object:
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (64, 64), (25, 103, 164, 255))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((12, 6, 50, 58), radius=5, fill="white")
        draw.polygon(((39, 6), (50, 17), (39, 17)), fill=(190, 220, 240, 255))
        draw.line((19, 27, 43, 27), fill=(25, 103, 164, 255), width=4)
        draw.line((19, 36, 43, 36), fill=(25, 103, 164, 255), width=4)
        draw.line((19, 45, 35, 45), fill=(25, 103, 164, 255), width=4)
        return image

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

    def _current_settings(self) -> Settings:
        try:
            retention = int(self.fields["archive_retention_days"].get())
            settle = int(self.fields["settle_seconds"].get())
        except ValueError as error:
            raise ValueError("Archiv-Aufbewahrung und Stabilitätszeit müssen ganze Zahlen sein.") from error

        return Settings(
            input_folder=self.fields["input_folder"].get().strip(),
            output_folder=self.fields["output_folder"].get().strip(),
            archive_folder=self.fields["archive_folder"].get().strip(),
            review_folder=self.fields["review_folder"].get().strip(),
            archive_retention_days=retention,
            settle_seconds=settle,
            poll_interval_seconds=1,
            tesseract_path=self.fields["tesseract_path"].get().strip(),
            ocr_languages=self.settings.ocr_languages,
        )

    def save(self) -> Settings | None:
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
        settings = self.save()
        if settings is None:
            return
        try:
            self.watcher = FolderWatcher(settings, self._from_worker, self._result_from_worker)
            self.watcher.start()
        except Exception as error:
            self._messagebox.showerror("Start fehlgeschlagen", str(error))
            return
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self._update_monitoring_badge()
        self._update_tray_status()

    def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self._update_monitoring_badge()
        self._update_tray_status()

    def _from_worker(self, message: str) -> None:
        def update() -> None:
            self.status.set(message)
            self._append_activity(message)

        self.root.after(0, update)

    def _result_from_worker(self, result: ProcessResult) -> None:
        logging.info("%s: %s", result.source_name, result.message)

    def quit_application(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        self.stop()
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.root.destroy()

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            if self.tray_icon is not None:
                self.tray_icon.stop()
                self.tray_icon = None


def run_headless(settings_path: Path) -> int:
    settings = load_settings(settings_path)
    errors = settings.validate()
    if errors:
        print("Ungültige Einstellungen:\n- " + "\n- ".join(errors), file=sys.stderr)
        return 2

    finished = threading.Event()
    watcher = FolderWatcher(
        settings,
        on_status=lambda message: logging.info(message),
        on_result=lambda result: logging.info("%s: %s", result.source_name, result.message),
    )

    def stop(_signal: int, _frame: object) -> None:
        finished.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    watcher.start()
    try:
        finished.wait()
    finally:
        watcher.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dokumenten-Scanner-Sortierung")
    parser.add_argument("--run", action="store_true", help="Überwachung ohne Benutzeroberfläche starten")
    parser.add_argument("--settings", type=Path, default=default_settings_path(), help="Pfad zur settings.json")
    args = parser.parse_args(argv)
    acquired, instance = acquire_single_instance(args.settings)
    if not acquired:
        notify_already_running()
        return 0

    try:
        configure_logging(args.settings)

        if args.run:
            return run_headless(args.settings)

        window = SettingsWindow(args.settings)
        window.run()
        return 0
    finally:
        release_single_instance(instance)


if __name__ == "__main__":
    raise SystemExit(main())
