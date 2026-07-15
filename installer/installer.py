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
from datetime import datetime
from pathlib import Path

if __package__:
    from .product import (
        APPLICATION_FILENAME,
        APPLICATION_FOLDER,
        DISPLAY_NAME,
        ICON_FILENAME,
        LEGACY_UNINSTALLER_FILENAME,
        NOTICE_FILENAME,
        PAYLOAD_ICON_FILENAME,
        PAYLOAD_UNINSTALLER_FILENAME,
        PUBLISHER,
        SHORTCUT_FILENAME,
        SUPPORT_EMAIL,
        UNINSTALL_REGISTRY_PATH,
        UNINSTALLER_FILENAME,
        VERSION_FILENAME,
    )
    from .windows_dialog import powershell_quote, show_confirmation
else:
    from product import (  # type: ignore[no-redef]
        APPLICATION_FILENAME,
        APPLICATION_FOLDER,
        DISPLAY_NAME,
        ICON_FILENAME,
        LEGACY_UNINSTALLER_FILENAME,
        NOTICE_FILENAME,
        PAYLOAD_ICON_FILENAME,
        PAYLOAD_UNINSTALLER_FILENAME,
        PUBLISHER,
        SHORTCUT_FILENAME,
        SUPPORT_EMAIL,
        UNINSTALL_REGISTRY_PATH,
        UNINSTALLER_FILENAME,
        VERSION_FILENAME,
    )
    from windows_dialog import powershell_quote, show_confirmation  # type: ignore[no-redef]


def payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / APPLICATION_FILENAME


def notice_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / NOTICE_FILENAME


def icon_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / PAYLOAD_ICON_FILENAME


def version_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / VERSION_FILENAME


def uninstaller_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / PAYLOAD_UNINSTALLER_FILENAME


def application_version() -> str:
    try:
        return version_payload_path().read_text(encoding="utf-8-sig").strip() or "Unbekannt"
    except OSError:
        return "Unbekannt"


def installation_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Programs" / APPLICATION_FOLDER / APPLICATION_FILENAME


def create_desktop_shortcut(target: Path, icon_path: Path | None = None) -> None:
    icon_path = icon_path or target
    quoted_target = powershell_quote(str(target))
    quoted_working_directory = powershell_quote(str(target.parent))
    quoted_shortcut_name = powershell_quote(SHORTCUT_FILENAME)
    quoted_icon = powershell_quote(str(icon_path))
    script = (
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        f"$shortcutPath = Join-Path $desktop {quoted_shortcut_name}; "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($shortcutPath); "
        f"$shortcut.TargetPath = {quoted_target}; "
        f"$shortcut.WorkingDirectory = {quoted_working_directory}; "
        f"$shortcut.IconLocation = {quoted_icon}; "
        "$shortcut.Description = 'Dokumenten-Scanner-Sortierung'; "
        "$shortcut.Save()"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=creation_flags,
    )


def notify_shell_icon_change(path: Path) -> None:
    if os.name != "nt":
        return
    import ctypes

    shell32 = ctypes.windll.shell32
    shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p]
    shell32.SHChangeNotify.restype = None
    shell32.SHChangeNotify(0x00002000, 0x0005, str(path), None)
    shell32.SHChangeNotify(0x08000000, 0x0000, None, None)


def show_message(kind: str, title: str, message: str) -> None:
    if "--silent" in sys.argv:
        if sys.stdout is not None:
            print(f"{title}: {message}")
        return
    import ctypes

    icon = 0x10 if kind == "showerror" else 0x40
    ctypes.windll.user32.MessageBoxW(None, message, title, icon)


def prompt_text(is_update: bool, version: str) -> tuple[str, str, str, str]:
    if is_update:
        return (
            "Update bestätigen",
            f"Update auf Version {version} ausführen?",
            "Die vorhandene Anwendung wird ersetzt. Einstellungen, Protokolle und archivierte Dokumente "
            "bleiben erhalten. Bitte schließen Sie die laufende Anwendung vor dem Update vollständig.",
            "Update ausführen",
        )
    return (
        "Installation bestätigen",
        f"Dokumenten-Scanner-Sortierung {version} installieren?",
        "Die Anwendung wird für den aktuell angemeldeten Windows-Benutzer installiert. "
        "Zusätzlich wird eine Verknüpfung auf dem Desktop erstellt.",
        "Installation ausführen",
    )


def confirm_installation(is_update: bool, version: str) -> bool:
    if "--silent" in sys.argv:
        return True
    title, instruction, content, action_text = prompt_text(is_update, version)
    return show_confirmation(title, instruction, content, action_text, icon_payload_path())


def installed_app_values(target: Path, uninstaller: Path, version: str, estimated_size_kb: int) -> dict[str, str | int]:
    uninstall_command = (
        f'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{uninstaller}"'
    )
    return {
        "DisplayName": DISPLAY_NAME,
        "DisplayVersion": version,
        "Publisher": PUBLISHER,
        "Contact": SUPPORT_EMAIL,
        "HelpLink": f"mailto:{SUPPORT_EMAIL}",
        "InstallLocation": str(target.parent),
        "DisplayIcon": f'"{target.parent / ICON_FILENAME}"',
        "UninstallString": uninstall_command,
        "QuietUninstallString": f"{uninstall_command} -Silent",
        "InstallDate": datetime.now().strftime("%Y%m%d"),
        "EstimatedSize": estimated_size_kb,
        "NoModify": 1,
        "NoRepair": 1,
        "Language": 1031,
    }


def register_installed_application(target: Path, uninstaller: Path, version: str, files: tuple[Path, ...]) -> None:
    import winreg

    estimated_size_kb = max(1, (sum(path.stat().st_size for path in files) + 1023) // 1024)
    values = installed_app_values(target, uninstaller, version, estimated_size_kb)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_PATH) as key:
        for name, value in values.items():
            value_type = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, value_type, value)


def main() -> int:
    target = installation_path()
    is_update = target.exists()
    version = application_version()
    if not confirm_installation(is_update, version):
        return 0
    try:
        if "--silent" in sys.argv:
            if sys.stdout is not None:
                print(f"Installiere {payload_path()} nach {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload_path(), target)
        installed_notice = target.parent / NOTICE_FILENAME
        shutil.copy2(notice_payload_path(), installed_notice)
        installed_icon = target.parent / ICON_FILENAME
        shutil.copy2(icon_payload_path(), installed_icon)
        installed_uninstaller = target.parent / UNINSTALLER_FILENAME
        shutil.copy2(uninstaller_payload_path(), installed_uninstaller)
        (target.parent / LEGACY_UNINSTALLER_FILENAME).unlink(missing_ok=True)
        create_desktop_shortcut(target, installed_icon)
        register_installed_application(
            target,
            installed_uninstaller,
            version,
            (target, installed_notice, installed_icon, installed_uninstaller),
        )
        notify_shell_icon_change(installed_icon)
        notify_shell_icon_change(Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop" / SHORTCUT_FILENAME)
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
        "Update abgeschlossen" if is_update else "Installation abgeschlossen",
        f"Die Anwendung wurde {'aktualisiert' if is_update else 'installiert'} unter:\n{target.parent}\n\n"
        "Eine Verknüpfung wurde auf dem Desktop erstellt.\n\n"
        "Spätere Versionen werden mit derselben Setup-EXE installiert.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
