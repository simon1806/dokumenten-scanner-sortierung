from __future__ import annotations

import argparse
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

        self.root = tk.Tk()
        self.root.title("Dokumenten-Scanner-Sortierung")
        self.root.minsize(820, 670)
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

    def _build(self) -> None:
        from tkinter import filedialog, messagebox

        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        frame = self.ttk.Frame(root, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(15, weight=1)

        self.ttk.Label(frame, text="Ordner", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        labels = [
            ("Eingangsordner", "input_folder"),
            ("Zielordner", "output_folder"),
            ("Archivordner", "archive_folder"),
            ("Prüfordner (nicht erkannt)", "review_folder"),
        ]
        for row, (label, field) in enumerate(labels, start=1):
            self.ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            self.ttk.Entry(frame, textvariable=self.fields[field]).grid(row=row, column=1, sticky="ew", pady=5)
            self.ttk.Button(
                frame,
                text="Auswählen…",
                command=lambda name=field: self._choose_folder(name, filedialog),
            ).grid(row=row, column=2, padx=(8, 0), pady=5)

        self.ttk.Separator(frame).grid(row=5, column=0, columnspan=3, sticky="ew", pady=12)
        self.ttk.Label(frame, text="Verarbeitung", font=("Segoe UI", 12, "bold")).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        self._entry_row(frame, 7, "Archiv-Aufbewahrung (Tage)", "archive_retention_days")
        self._entry_row(frame, 8, "Dateistabilität nach Scan (Sekunden)", "settle_seconds")
        self._entry_row(frame, 9, "Tesseract-Pfad (optional, falls nicht mitgeliefert)", "tesseract_path")

        self.ttk.Label(
            frame,
            text=(
                "Nicht erkannte Scans werden unverändert in den Zielordner weitergeleitet und zusätzlich "
                "in den Prüfordner kopiert. Die Stabilitätszeit verhindert eine Verarbeitung während des Scannens."
            ),
            wraplength=760,
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(14, 6))

        controls = self.ttk.Frame(frame)
        controls.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        self.ttk.Button(controls, text="Einstellungen speichern", command=self.save).pack(side="left")
        self.start_button = self.ttk.Button(controls, text="Überwachung starten", command=self.start)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = self.ttk.Button(controls, text="Überwachung beenden", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")
        self.ttk.Button(controls, text="Ausblenden", command=self.hide_to_tray).pack(side="left", padx=8)
        self.ttk.Button(controls, text="Anwendung beenden", command=self.quit_application).pack(side="left")

        self.ttk.Label(frame, textvariable=self.status, foreground="#155724", wraplength=760).grid(
            row=12, column=0, columnspan=3, sticky="w", pady=(12, 4)
        )

        self.ttk.Separator(frame).grid(row=13, column=0, columnspan=3, sticky="ew", pady=10)
        log_header = self.ttk.Frame(frame)
        log_header.grid(row=14, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.ttk.Label(log_header, text="Aktivitätsprotokoll", font=("Segoe UI", 12, "bold")).pack(side="left")
        self.ttk.Button(log_header, text="Protokollordner öffnen", command=self._open_log_folder).pack(side="right")

        log_frame = self.ttk.Frame(frame)
        log_frame.grid(row=15, column=0, columnspan=3, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.activity_log = self.tk.Text(log_frame, height=8, wrap="word", state="disabled", font=("Consolas", 9))
        self.activity_log.grid(row=0, column=0, sticky="nsew")
        log_scrollbar = self.ttk.Scrollbar(log_frame, orient="vertical", command=self.activity_log.yview)
        log_scrollbar.grid(row=0, column=1, sticky="ns")
        self.activity_log.configure(yscrollcommand=log_scrollbar.set)
        self._append_activity(f"Protokolldatei: {self.log_path}")

        self._messagebox = messagebox

    def _entry_row(self, frame: object, row: int, label: str, field: str) -> None:
        self.ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        self.ttk.Entry(frame, textvariable=self.fields[field]).grid(row=row, column=1, sticky="ew", pady=5)

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
                "Dokumenten-Scanner-Sortierung",
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
        self.tray_icon.title = f"Dokumenten-Scanner-Sortierung – {status}"

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
        self._update_tray_status()

    def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
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
    configure_logging(args.settings)

    if args.run:
        return run_headless(args.settings)

    window = SettingsWindow(args.settings)
    window.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
