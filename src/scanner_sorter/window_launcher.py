"""Fast Windows launcher for restoring an already running application window.

This module deliberately uses only the Python standard library. Its frozen
entry point is installed next to the main application, so opening the desktop
shortcut does not need to unpack the bundled OCR runtime first.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


WINDOW_TITLE_PREFIX = "Dokumenten-Scanner-Sortierung"
MAIN_APPLICATION_FILENAME = "DokumentenScannerSortierung.exe"
SW_RESTORE = 9


def find_existing_window(title_prefix: str = WINDOW_TITLE_PREFIX) -> int | None:
    """Return the first application window, including a window hidden in the tray."""
    if os.name != "nt":
        return None

    import ctypes

    user32 = ctypes.windll.user32
    windows: list[int] = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def find_window(window: int, _parameter: int) -> bool:
        length = user32.GetWindowTextLengthW(window)
        if length:
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(window, title, length + 1)
            if title.value.startswith(title_prefix):
                windows.append(window)
                return False
        return True

    user32.EnumWindows(callback_type(find_window), 0)
    return windows[0] if windows else None


def activate_existing_window() -> bool:
    """Restore the running application window and return whether it was found."""
    window = find_existing_window()
    if window is None:
        return False

    import ctypes

    user32 = ctypes.windll.user32
    user32.ShowWindow(window, SW_RESTORE)
    user32.BringWindowToTop(window)
    user32.SetForegroundWindow(window)
    return True


def main_application_command() -> tuple[str, ...]:
    """Return a command for the adjacent installed application.

    Source execution remains useful for local development; the installed
    launcher always starts the EXE located in the same program directory.
    """
    if getattr(sys, "frozen", False):
        return (str(Path(sys.executable).with_name(MAIN_APPLICATION_FILENAME)),)
    return (sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"))


def show_start_error(target: str) -> None:
    message = (
        "Die Hauptanwendung wurde nicht gefunden.\n\n"
        f"Erwartete Datei: {target}\n\n"
        "Bitte führen Sie das Setup erneut aus."
    )
    if os.name == "nt":
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Start nicht möglich", 0x10)
    else:
        print(message, file=sys.stderr)


def start_main_application(command: tuple[str, ...]) -> bool:
    target = Path(command[-1])
    if not target.is_file():
        show_start_error(str(target))
        return False
    try:
        subprocess.Popen(command, cwd=str(target.parent))
    except OSError as error:
        show_start_error(f"{target}\nTechnische Details: {error}")
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dokumenten-Scanner-Sortierung öffnen")
    parser.add_argument("--self-test", action="store_true", help="Starter ohne Benutzeraktion prüfen")
    args = parser.parse_args(argv)
    if args.self_test:
        return 0
    if activate_existing_window():
        return 0
    return 0 if start_main_application(main_application_command()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
