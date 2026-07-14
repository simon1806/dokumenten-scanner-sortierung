from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
from pathlib import Path

from .config import Settings, default_settings_path, load_settings, save_settings
from .models import ProcessResult
from .watcher import FolderWatcher


def configure_logging(settings_path: Path) -> None:
    log_path = settings_path.parent / "logs" / "dokumentensortierer.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
    )


class SettingsWindow:
    def __init__(self, settings_path: Path):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.settings_path = settings_path
        self.settings = load_settings(settings_path)
        self.watcher: FolderWatcher | None = None

        self.root = tk.Tk()
        self.root.title("Dokumenten-Scanner-Sortierung")
        self.root.minsize(760, 470)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.fields: dict[str, tk.StringVar] = {
            "input_folder": tk.StringVar(value=self.settings.input_folder),
            "output_folder": tk.StringVar(value=self.settings.output_folder),
            "archive_folder": tk.StringVar(value=self.settings.archive_folder),
            "archive_retention_days": tk.StringVar(value=str(self.settings.archive_retention_days)),
            "settle_seconds": tk.StringVar(value=str(self.settings.settle_seconds)),
            "tesseract_path": tk.StringVar(value=self.settings.tesseract_path),
        }
        self.status = tk.StringVar(value="Einstellungen speichern und Überwachung starten.")
        self._build()

    def _build(self) -> None:
        from tkinter import filedialog, messagebox

        root = self.root
        root.columnconfigure(0, weight=1)
        frame = self.ttk.Frame(root, padding=16)
        frame.grid(sticky="nsew")
        frame.columnconfigure(1, weight=1)

        self.ttk.Label(frame, text="Ordner", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        labels = [
            ("Eingangsordner", "input_folder"),
            ("Zielordner", "output_folder"),
            ("Archivordner", "archive_folder"),
        ]
        for row, (label, field) in enumerate(labels, start=1):
            self.ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            self.ttk.Entry(frame, textvariable=self.fields[field]).grid(row=row, column=1, sticky="ew", pady=5)
            self.ttk.Button(
                frame,
                text="Auswählen…",
                command=lambda name=field: self._choose_folder(name, filedialog),
            ).grid(row=row, column=2, padx=(8, 0), pady=5)

        self.ttk.Separator(frame).grid(row=4, column=0, columnspan=3, sticky="ew", pady=12)
        self.ttk.Label(frame, text="Verarbeitung", font=("Segoe UI", 12, "bold")).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        self._entry_row(frame, 6, "Archiv-Aufbewahrung (Tage)", "archive_retention_days")
        self._entry_row(frame, 7, "Wartezeit nach Scan (Sekunden)", "settle_seconds")
        self._entry_row(frame, 8, "Tesseract-Pfad (optional)", "tesseract_path")

        self.ttk.Label(
            frame,
            text="Bei nicht erkannten Scans wird die Originaldatei unverändert in den Zielordner weitergeleitet.",
            wraplength=680,
        ).grid(row=9, column=0, columnspan=3, sticky="w", pady=(14, 6))

        controls = self.ttk.Frame(frame)
        controls.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        self.ttk.Button(controls, text="Einstellungen speichern", command=self.save).pack(side="left")
        self.start_button = self.ttk.Button(controls, text="Überwachung starten", command=self.start)
        self.start_button.pack(side="left", padx=8)
        self.stop_button = self.ttk.Button(controls, text="Überwachung beenden", command=self.stop, state="disabled")
        self.stop_button.pack(side="left")

        self.ttk.Label(frame, textvariable=self.status, foreground="#155724", wraplength=680).grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(12, 0)
        )

        self._messagebox = messagebox

    def _entry_row(self, frame: object, row: int, label: str, field: str) -> None:
        self.ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
        self.ttk.Entry(frame, textvariable=self.fields[field]).grid(row=row, column=1, sticky="ew", pady=5)

    def _choose_folder(self, field: str, filedialog: object) -> None:
        directory = filedialog.askdirectory(initialdir=self.fields[field].get() or None)
        if directory:
            self.fields[field].set(directory)

    def _current_settings(self) -> Settings:
        try:
            retention = int(self.fields["archive_retention_days"].get())
            settle = int(self.fields["settle_seconds"].get())
        except ValueError as error:
            raise ValueError("Archiv-Aufbewahrung und Wartezeit müssen ganze Zahlen sein.") from error

        return Settings(
            input_folder=self.fields["input_folder"].get().strip(),
            output_folder=self.fields["output_folder"].get().strip(),
            archive_folder=self.fields["archive_folder"].get().strip(),
            archive_retention_days=retention,
            settle_seconds=settle,
            poll_interval_seconds=self.settings.poll_interval_seconds,
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

    def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")

    def _from_worker(self, message: str) -> None:
        self.root.after(0, self.status.set, message)

    def _result_from_worker(self, result: ProcessResult) -> None:
        logging.info("%s: %s", result.source_name, result.message)

    def close(self) -> None:
        self.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


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
