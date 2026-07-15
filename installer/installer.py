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
SHORTCUT_FILENAME = "Dokumenten-Scanner-Sortierung.lnk"
NOTICE_FILENAME = "THIRD_PARTY_NOTICES.md"
ICON_FILENAME = "DokumentenScannerSortierung.ico"
PAYLOAD_ICON_FILENAME = "dokumenten-scanner-sortierung.ico"
VERSION_FILENAME = "version.txt"


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


def application_version() -> str:
    try:
        return version_payload_path().read_text(encoding="utf-8-sig").strip() or "Unbekannt"
    except OSError:
        return "Unbekannt"


def installation_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Programs" / APPLICATION_FOLDER / APPLICATION_FILENAME


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def create_desktop_shortcut(target: Path, icon_path: Path | None = None) -> None:
    icon_path = icon_path or target
    quoted_target = _powershell_quote(str(target))
    quoted_working_directory = _powershell_quote(str(target.parent))
    quoted_shortcut_name = _powershell_quote(SHORTCUT_FILENAME)
    quoted_icon = _powershell_quote(str(icon_path))
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
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        f"$form.Text = {_powershell_quote(title)}; "
        "$form.ClientSize = New-Object System.Drawing.Size(560,245); "
        "$form.StartPosition = 'CenterScreen'; "
        "$form.FormBorderStyle = 'FixedDialog'; "
        "$form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ShowInTaskbar = $true; "
        f"$iconPath = {_powershell_quote(str(icon_payload_path()))}; "
        "if (Test-Path -LiteralPath $iconPath) { $form.Icon = New-Object System.Drawing.Icon($iconPath) }; "
        "$header = New-Object System.Windows.Forms.Panel; $header.Dock = 'Top'; $header.Height = 70; "
        "$header.BackColor = [System.Drawing.Color]::FromArgb(23,53,75); $form.Controls.Add($header); "
        "$titleLabel = New-Object System.Windows.Forms.Label; $titleLabel.AutoSize = $false; "
        "$titleLabel.Location = New-Object System.Drawing.Point(22,15); "
        "$titleLabel.Size = New-Object System.Drawing.Size(510,40); "
        "$titleLabel.ForeColor = [System.Drawing.Color]::White; "
        "$titleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',14); "
        f"$titleLabel.Text = {_powershell_quote(instruction)}; $header.Controls.Add($titleLabel); "
        "$contentLabel = New-Object System.Windows.Forms.Label; $contentLabel.AutoSize = $false; "
        "$contentLabel.Location = New-Object System.Drawing.Point(24,91); "
        "$contentLabel.Size = New-Object System.Drawing.Size(510,70); "
        "$contentLabel.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        f"$contentLabel.Text = {_powershell_quote(content)}; $form.Controls.Add($contentLabel); "
        "$action = New-Object System.Windows.Forms.Button; "
        "$action.Location = New-Object System.Drawing.Point(274,186); "
        "$action.Size = New-Object System.Drawing.Size(150,36); "
        "$action.BackColor = [System.Drawing.Color]::FromArgb(23,111,166); "
        "$action.ForeColor = [System.Drawing.Color]::White; $action.FlatStyle = 'Flat'; "
        f"$action.Text = {_powershell_quote(action_text)}; "
        "$action.Add_Click({ $form.Tag = 'confirmed'; $form.Close() }); $form.Controls.Add($action); "
        "$cancel = New-Object System.Windows.Forms.Button; "
        "$cancel.Location = New-Object System.Drawing.Point(434,186); "
        "$cancel.Size = New-Object System.Drawing.Size(100,36); $cancel.Text = 'Abbrechen'; "
        "$cancel.Add_Click({ $form.Close() }); $form.Controls.Add($cancel); "
        "$form.AcceptButton = $action; $form.CancelButton = $cancel; "
        "$form.Add_Shown({ $action.Focus() }); [void]$form.ShowDialog(); "
        "if ($form.Tag -eq 'confirmed') { exit 0 } else { exit 1 }"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def main() -> int:
    target = installation_path()
    is_update = target.exists()
    version = application_version()
    if not confirm_installation(is_update, version):
        return 0
    try:
        if "--silent" in sys.argv:
            print(f"Installiere {payload_path()} nach {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(payload_path(), target)
        shutil.copy2(notice_payload_path(), target.parent / NOTICE_FILENAME)
        installed_icon = target.parent / ICON_FILENAME
        shutil.copy2(icon_payload_path(), installed_icon)
        create_desktop_shortcut(target, installed_icon)
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
