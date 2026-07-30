from __future__ import annotations

import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@dataclass(slots=True)
class ProgressDialog:
    """A lightweight, modeless PowerShell progress window owned by setup."""

    process: subprocess.Popen[object]

    def close(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.process.terminate()
            self.process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                self.process.kill()
            except OSError:
                pass


def _dialog_response_path() -> Path:
    return Path(tempfile.gettempdir()) / f"dokumentensortierer-setup-{uuid4().hex}.response"


def _wait_for_dialog_response(
    process: subprocess.Popen[object],
    response_path: Path,
    *,
    timeout: float | None = None,
) -> str:
    started = time.monotonic()
    while True:
        try:
            response = response_path.read_text(encoding="utf-8").strip()
        except OSError:
            response = ""
        if response:
            return response
        if process.poll() is not None:
            return ""
        if timeout is not None and time.monotonic() - started >= timeout:
            return ""
        time.sleep(0.05)


def _progress_script(
    title: str,
    instruction: str,
    content: str,
    icon_path: Path,
    *,
    ready_path: Path | None = None,
    ready_value: str = "ready",
) -> str:
    shown_action = ""
    if ready_path is not None:
        shown_action = (
            f"[System.IO.File]::WriteAllText({powershell_quote(str(ready_path))}, "
            f"{powershell_quote(ready_value)}, [System.Text.Encoding]::UTF8); "
        )
    return (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        f"$form.Text = {powershell_quote(title)}; "
        "$form.ClientSize = New-Object System.Drawing.Size(720,245); "
        "$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; "
        "$form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ControlBox = $false; "
        "$form.ShowInTaskbar = $true; $form.TopMost = $true; "
        f"$iconPath = {powershell_quote(str(icon_path))}; "
        "if (Test-Path -LiteralPath $iconPath) { $form.Icon = New-Object System.Drawing.Icon($iconPath) }; "
        "$header = New-Object System.Windows.Forms.Panel; $header.Dock = 'Top'; $header.Height = 70; "
        "$header.BackColor = [System.Drawing.Color]::FromArgb(23,53,75); $form.Controls.Add($header); "
        "$titleLabel = New-Object System.Windows.Forms.Label; $titleLabel.AutoSize = $false; "
        "$titleLabel.Location = New-Object System.Drawing.Point(22,15); $titleLabel.Size = New-Object System.Drawing.Size(670,40); "
        "$titleLabel.ForeColor = [System.Drawing.Color]::White; $titleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',14); "
        f"$titleLabel.Text = {powershell_quote(instruction)}; $header.Controls.Add($titleLabel); "
        "$contentLabel = New-Object System.Windows.Forms.Label; $contentLabel.AutoSize = $false; "
        "$contentLabel.Location = New-Object System.Drawing.Point(24,91); $contentLabel.Size = New-Object System.Drawing.Size(670,54); "
        "$contentLabel.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        f"$contentLabel.Text = {powershell_quote(content)}; $form.Controls.Add($contentLabel); "
        "$progress = New-Object System.Windows.Forms.ProgressBar; "
        "$progress.Location = New-Object System.Drawing.Point(24,166); $progress.Size = New-Object System.Drawing.Size(670,24); "
        "$progress.Style = 'Marquee'; $progress.MarqueeAnimationSpeed = 28; $form.Controls.Add($progress); "
        "$waitLabel = New-Object System.Windows.Forms.Label; $waitLabel.AutoSize = $false; "
        "$waitLabel.Location = New-Object System.Drawing.Point(24,202); $waitLabel.Size = New-Object System.Drawing.Size(670,22); "
        "$waitLabel.ForeColor = [System.Drawing.Color]::FromArgb(72,96,111); $waitLabel.Font = New-Object System.Drawing.Font('Segoe UI',8); "
        "$waitLabel.Text = 'Bitte schließen Sie dieses Fenster nicht und starten Sie kein weiteres Setup.'; $form.Controls.Add($waitLabel); "
        f"$form.Add_Shown({{ {shown_action}$form.Activate() }}); "
        "[System.Windows.Forms.Application]::Run($form)"
    )


def show_installation_progress(
    title: str,
    instruction: str,
    content: str,
    icon_path: Path,
) -> ProgressDialog:
    """Show a non-interactive progress window while setup replaces its payload."""
    script = _progress_script(title, instruction, content, icon_path)
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return ProgressDialog(process)


def show_confirmation(
    title: str,
    instruction: str,
    content: str,
    action_text: str,
    icon_path: Path,
    danger: bool = False,
) -> bool:
    action_color = "182,58,58" if danger else "23,111,166"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        f"$form.Text = {powershell_quote(title)}; "
        "$form.ClientSize = New-Object System.Drawing.Size(720,350); "
        "$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; "
        "$form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ShowInTaskbar = $true; "
        f"$iconPath = {powershell_quote(str(icon_path))}; "
        "if (Test-Path -LiteralPath $iconPath) { $form.Icon = New-Object System.Drawing.Icon($iconPath) }; "
        "$header = New-Object System.Windows.Forms.Panel; $header.Dock = 'Top'; $header.Height = 70; "
        "$header.BackColor = [System.Drawing.Color]::FromArgb(23,53,75); $form.Controls.Add($header); "
        "$titleLabel = New-Object System.Windows.Forms.Label; $titleLabel.AutoSize = $false; "
        "$titleLabel.Location = New-Object System.Drawing.Point(22,15); "
        "$titleLabel.Size = New-Object System.Drawing.Size(670,40); "
        "$titleLabel.ForeColor = [System.Drawing.Color]::White; "
        "$titleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',14); "
        f"$titleLabel.Text = {powershell_quote(instruction)}; $header.Controls.Add($titleLabel); "
        "$contentLabel = New-Object System.Windows.Forms.Label; $contentLabel.AutoSize = $false; "
        "$contentLabel.Location = New-Object System.Drawing.Point(24,91); "
        "$contentLabel.Size = New-Object System.Drawing.Size(670,170); "
        "$contentLabel.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        f"$contentLabel.Text = {powershell_quote(content)}; $form.Controls.Add($contentLabel); "
        "$action = New-Object System.Windows.Forms.Button; "
        "$action.Location = New-Object System.Drawing.Point(414,291); "
        "$action.Size = New-Object System.Drawing.Size(150,36); "
        f"$action.BackColor = [System.Drawing.Color]::FromArgb({action_color}); "
        "$action.ForeColor = [System.Drawing.Color]::White; $action.FlatStyle = 'Flat'; "
        f"$action.Text = {powershell_quote(action_text)}; "
        "$action.Add_Click({ $form.Tag = 'confirmed'; $form.Close() }); $form.Controls.Add($action); "
        "$cancel = New-Object System.Windows.Forms.Button; "
        "$cancel.Location = New-Object System.Drawing.Point(574,291); "
        "$cancel.Size = New-Object System.Drawing.Size(120,36); $cancel.Text = 'Abbrechen'; "
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


def show_confirmation_with_server_autostart(
    title: str,
    instruction: str,
    content: str,
    action_text: str,
    icon_path: Path,
) -> tuple[bool, bool]:
    """Show the setup choice for a boot-triggered SYSTEM task.

    The option is deliberately opt-in: a normal workstation installation keeps
    its user startup shortcut, while a server can run before a user signs in.
    """

    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        f"$form.Text = {powershell_quote(title)}; "
        "$form.ClientSize = New-Object System.Drawing.Size(720,405); "
        "$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; "
        "$form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ShowInTaskbar = $true; "
        f"$iconPath = {powershell_quote(str(icon_path))}; "
        "if (Test-Path -LiteralPath $iconPath) { $form.Icon = New-Object System.Drawing.Icon($iconPath) }; "
        "$header = New-Object System.Windows.Forms.Panel; $header.Dock = 'Top'; $header.Height = 70; "
        "$header.BackColor = [System.Drawing.Color]::FromArgb(23,53,75); $form.Controls.Add($header); "
        "$titleLabel = New-Object System.Windows.Forms.Label; $titleLabel.AutoSize = $false; "
        "$titleLabel.Location = New-Object System.Drawing.Point(22,15); "
        "$titleLabel.Size = New-Object System.Drawing.Size(670,40); "
        "$titleLabel.ForeColor = [System.Drawing.Color]::White; "
        "$titleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',14); "
        f"$titleLabel.Text = {powershell_quote(instruction)}; $header.Controls.Add($titleLabel); "
        "$contentLabel = New-Object System.Windows.Forms.Label; $contentLabel.AutoSize = $false; "
        "$contentLabel.Location = New-Object System.Drawing.Point(24,91); "
        "$contentLabel.Size = New-Object System.Drawing.Size(670,155); "
        "$contentLabel.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        f"$contentLabel.Text = {powershell_quote(content)}; $form.Controls.Add($contentLabel); "
        "$server = New-Object System.Windows.Forms.CheckBox; "
        "$server.Location = New-Object System.Drawing.Point(24,258); "
        "$server.Size = New-Object System.Drawing.Size(650,28); "
        "$server.Font = New-Object System.Drawing.Font('Segoe UI Semibold',9); "
        "$server.Text = 'Serverautostart beim Systemstart einrichten'; $form.Controls.Add($server); "
        "$hint = New-Object System.Windows.Forms.Label; $hint.AutoSize = $false; "
        "$hint.Location = New-Object System.Drawing.Point(45,286); $hint.Size = New-Object System.Drawing.Size(630,38); "
        "$hint.Font = New-Object System.Drawing.Font('Segoe UI',8); "
        "$hint.Text = 'Erstellt eine SYSTEM-Aufgabe mit zentralen Einstellungen. Das Setup benötigt dafür Administratorrechte; Netzwerkordner müssen als UNC-Pfade eingerichtet sein.'; $form.Controls.Add($hint); "
        "$action = New-Object System.Windows.Forms.Button; "
        "$action.Location = New-Object System.Drawing.Point(414,351); $action.Size = New-Object System.Drawing.Size(150,36); "
        "$action.BackColor = [System.Drawing.Color]::FromArgb(23,111,166); "
        "$action.ForeColor = [System.Drawing.Color]::White; $action.FlatStyle = 'Flat'; "
        f"$action.Text = {powershell_quote(action_text)}; "
        "$action.Add_Click({ if ($server.Checked) { $form.Tag = 'server' } else { $form.Tag = 'normal' }; $form.Close() }); $form.Controls.Add($action); "
        "$cancel = New-Object System.Windows.Forms.Button; "
        "$cancel.Location = New-Object System.Drawing.Point(574,351); $cancel.Size = New-Object System.Drawing.Size(120,36); "
        "$cancel.Text = 'Abbrechen'; $cancel.Add_Click({ $form.Close() }); $form.Controls.Add($cancel); "
        "$form.AcceptButton = $action; $form.CancelButton = $cancel; "
        "$form.Add_Shown({ $action.Focus() }); [void]$form.ShowDialog(); "
        "if ($form.Tag -eq 'server') { exit 10 } elseif ($form.Tag -eq 'normal') { exit 0 } else { exit 1 }"
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
    return result.returncode in {0, 10}, result.returncode == 10


def show_confirmation_with_server_autostart_and_progress(
    title: str,
    instruction: str,
    content: str,
    action_text: str,
    icon_path: Path,
    progress_text: tuple[str, str, str],
) -> tuple[bool, bool, ProgressDialog | None]:
    """Hand the visible confirmation directly over to a progress window."""
    selection_path = _dialog_response_path()
    ready_path = _dialog_response_path()
    progress_title, progress_instruction, progress_content = progress_text
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        f"$form.Text = {powershell_quote(title)}; "
        "$form.ClientSize = New-Object System.Drawing.Size(720,405); "
        "$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; "
        "$form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ShowInTaskbar = $true; "
        f"$iconPath = {powershell_quote(str(icon_path))}; "
        "if (Test-Path -LiteralPath $iconPath) { $form.Icon = New-Object System.Drawing.Icon($iconPath) }; "
        "$header = New-Object System.Windows.Forms.Panel; $header.Dock = 'Top'; $header.Height = 70; "
        "$header.BackColor = [System.Drawing.Color]::FromArgb(23,53,75); $form.Controls.Add($header); "
        "$titleLabel = New-Object System.Windows.Forms.Label; $titleLabel.AutoSize = $false; "
        "$titleLabel.Location = New-Object System.Drawing.Point(22,15); "
        "$titleLabel.Size = New-Object System.Drawing.Size(670,40); "
        "$titleLabel.ForeColor = [System.Drawing.Color]::White; "
        "$titleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',14); "
        f"$titleLabel.Text = {powershell_quote(instruction)}; $header.Controls.Add($titleLabel); "
        "$contentLabel = New-Object System.Windows.Forms.Label; $contentLabel.AutoSize = $false; "
        "$contentLabel.Location = New-Object System.Drawing.Point(24,91); "
        "$contentLabel.Size = New-Object System.Drawing.Size(670,155); "
        "$contentLabel.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        f"$contentLabel.Text = {powershell_quote(content)}; $form.Controls.Add($contentLabel); "
        "$server = New-Object System.Windows.Forms.CheckBox; "
        "$server.Location = New-Object System.Drawing.Point(24,258); "
        "$server.Size = New-Object System.Drawing.Size(650,28); "
        "$server.Font = New-Object System.Drawing.Font('Segoe UI Semibold',9); "
        "$server.Text = 'Serverautostart beim Systemstart einrichten'; $form.Controls.Add($server); "
        "$hint = New-Object System.Windows.Forms.Label; $hint.AutoSize = $false; "
        "$hint.Location = New-Object System.Drawing.Point(45,286); $hint.Size = New-Object System.Drawing.Size(630,38); "
        "$hint.Font = New-Object System.Drawing.Font('Segoe UI',8); "
        "$hint.Text = 'Erstellt eine SYSTEM-Aufgabe mit zentralen Einstellungen. Das Setup benötigt dafür Administratorrechte; Netzwerkordner müssen als UNC-Pfade eingerichtet sein.'; $form.Controls.Add($hint); "
        "$action = New-Object System.Windows.Forms.Button; "
        "$action.Location = New-Object System.Drawing.Point(414,351); $action.Size = New-Object System.Drawing.Size(150,36); "
        "$action.BackColor = [System.Drawing.Color]::FromArgb(23,111,166); "
        "$action.ForeColor = [System.Drawing.Color]::White; $action.FlatStyle = 'Flat'; "
        f"$action.Text = {powershell_quote(action_text)}; "
        "$action.Add_Click({ "
        "$selection = if ($server.Checked) { 'server' } else { 'normal' }; "
        f"[System.IO.File]::WriteAllText({powershell_quote(str(selection_path))}, $selection, [System.Text.Encoding]::UTF8); "
        "$form.Close() }); $form.Controls.Add($action); "
        "$cancel = New-Object System.Windows.Forms.Button; "
        "$cancel.Location = New-Object System.Drawing.Point(574,351); $cancel.Size = New-Object System.Drawing.Size(120,36); "
        "$cancel.Text = 'Abbrechen'; $cancel.Add_Click({ "
        f"[System.IO.File]::WriteAllText({powershell_quote(str(selection_path))}, 'cancel', [System.Text.Encoding]::UTF8); "
        "$form.Close() }); $form.Controls.Add($cancel); "
        "$form.AcceptButton = $action; $form.CancelButton = $cancel; "
        "$form.Add_Shown({ $action.Focus() }); [void]$form.ShowDialog(); "
        f"$selection = if (Test-Path -LiteralPath {powershell_quote(str(selection_path))}) "
        f"{{ [System.IO.File]::ReadAllText({powershell_quote(str(selection_path))}, [System.Text.Encoding]::UTF8).Trim() }} "
        "else { 'cancel' }; "
        "if ($selection -eq 'cancel') { exit 1 }; "
        + _progress_script(
            progress_title,
            progress_instruction,
            progress_content,
            icon_path,
            ready_path=ready_path,
        )
    )
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        _wait_for_dialog_response(process, ready_path)
        try:
            selection = selection_path.read_text(encoding="utf-8").lstrip("\ufeff").strip()
        except OSError:
            selection = "cancel"
    finally:
        selection_path.unlink(missing_ok=True)
        ready_path.unlink(missing_ok=True)
    if selection not in {"normal", "server"}:
        if process.poll() is None:
            process.terminate()
        return False, False, None
    return True, selection == "server", ProgressDialog(process)


def show_completion(
    title: str,
    instruction: str,
    content: str,
    icon_path: Path,
    start_application: bool = True,
    previous_dialog: ProgressDialog | None = None,
) -> bool:
    checked = "$true" if start_application else "$false"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        f"$form.Text = {powershell_quote(title)}; "
        "$form.ClientSize = New-Object System.Drawing.Size(760,430); "
        "$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; "
        "$form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.ShowInTaskbar = $true; "
        f"$iconPath = {powershell_quote(str(icon_path))}; "
        "if (Test-Path -LiteralPath $iconPath) { $form.Icon = New-Object System.Drawing.Icon($iconPath) }; "
        "$header = New-Object System.Windows.Forms.Panel; $header.Dock = 'Top'; $header.Height = 70; "
        "$header.BackColor = [System.Drawing.Color]::FromArgb(23,53,75); $form.Controls.Add($header); "
        "$titleLabel = New-Object System.Windows.Forms.Label; $titleLabel.AutoSize = $false; "
        "$titleLabel.Location = New-Object System.Drawing.Point(22,15); "
        "$titleLabel.Size = New-Object System.Drawing.Size(710,40); "
        "$titleLabel.ForeColor = [System.Drawing.Color]::White; "
        "$titleLabel.Font = New-Object System.Drawing.Font('Segoe UI Semibold',14); "
        f"$titleLabel.Text = {powershell_quote(instruction)}; $header.Controls.Add($titleLabel); "
        "$contentLabel = New-Object System.Windows.Forms.Label; $contentLabel.AutoSize = $false; "
        "$contentLabel.Location = New-Object System.Drawing.Point(24,91); "
        "$contentLabel.Size = New-Object System.Drawing.Size(710,220); "
        "$contentLabel.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        f"$contentLabel.Text = {powershell_quote(content)}; $form.Controls.Add($contentLabel); "
        "$launch = New-Object System.Windows.Forms.CheckBox; "
        "$launch.Location = New-Object System.Drawing.Point(24,328); "
        "$launch.Size = New-Object System.Drawing.Size(250,28); "
        "$launch.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        "$launch.Text = 'Anwendung starten'; "
        f"$launch.Checked = {checked}; $form.Controls.Add($launch); "
        "$finish = New-Object System.Windows.Forms.Button; "
        "$finish.Location = New-Object System.Drawing.Point(550,375); "
        "$finish.Size = New-Object System.Drawing.Size(180,36); "
        "$finish.BackColor = [System.Drawing.Color]::FromArgb(23,111,166); "
        "$finish.ForeColor = [System.Drawing.Color]::White; $finish.FlatStyle = 'Flat'; "
        "$finish.Text = 'Installation beenden'; "
        "$finish.Add_Click({ if ($launch.Checked) { $form.Tag = 'launch' } "
        "else { $form.Tag = 'close' }; $form.Close() }); $form.Controls.Add($finish); "
        "$form.AcceptButton = $finish; "
        "$form.Add_Shown({ $finish.Focus() }); [void]$form.ShowDialog(); "
        "if ($form.Tag -eq 'launch') { exit 0 } else { exit 2 }"
    )
    if previous_dialog is not None:
        ready_path = _dialog_response_path()
        script = script.replace(
            "$form.Add_Shown({ $finish.Focus() });",
            "$form.Add_Shown({ "
            f"[System.IO.File]::WriteAllText({powershell_quote(str(ready_path))}, 'ready', "
            "[System.Text.Encoding]::UTF8); $finish.Focus() });",
        )
        process = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            _wait_for_dialog_response(process, ready_path, timeout=15)
            previous_dialog.close()
            return process.wait() == 0
        finally:
            ready_path.unlink(missing_ok=True)
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
