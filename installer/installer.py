"""Minimaler Windows-Installer für die portable Anwendung.

PyInstaller legt die eigentliche Anwendung als eingebettete Datei im Setup ab.
Ein erneuter Start derselben Setup-EXE ersetzt die Anwendung und dient damit auch
als Update-Mechanismus.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


APPLICATION_FILENAME = "DokumentenScannerSortierung.exe"
APPLICATION_FOLDER = "DokumentenScannerSortierung"


def payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / APPLICATION_FILENAME


def installation_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Programs" / APPLICATION_FOLDER / APPLICATION_FILENAME


def show_message(kind: str, title: str, message: str) -> None:
    if "--silent" in sys.argv:
        print(f"{title}: {message}")
        return
    from tkinter import messagebox

    root = __import__("tkinter").Tk()
    root.withdraw()
    getattr(messagebox, kind)(title, message)
    root.destroy()


def main() -> int:
    target = installation_path()
    try:
        if "--silent" in sys.argv:
            print(f"Installiere {payload_path()} nach {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload_path(), target)
    except PermissionError as error:
        show_message(
            "showerror",
            "Update nicht möglich",
            "Die Anwendung läuft wahrscheinlich noch. Bitte schließen Sie sie und starten Sie das Setup erneut.\n\n"
            f"Technische Details: {error}",
        )
        return 1
    except Exception as error:
        show_message("showerror", "Installation fehlgeschlagen", str(error))
        return 1

    if "--no-launch" not in sys.argv:
        subprocess.Popen([str(target)], close_fds=True)
    show_message(
        "showinfo",
        "Installation abgeschlossen",
        f"Die Anwendung wurde installiert unter:\n{target.parent}\n\n"
        "Spätere Versionen werden mit derselben Setup-EXE installiert.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
