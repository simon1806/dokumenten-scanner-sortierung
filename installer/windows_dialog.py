from __future__ import annotations

import subprocess
from pathlib import Path


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


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


def show_completion(
    title: str,
    instruction: str,
    content: str,
    icon_path: Path,
    start_application: bool = True,
) -> bool:
    checked = "$true" if start_application else "$false"
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$form = New-Object System.Windows.Forms.Form; "
        f"$form.Text = {powershell_quote(title)}; "
        "$form.ClientSize = New-Object System.Drawing.Size(720,340); "
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
        "$contentLabel.Size = New-Object System.Drawing.Size(670,120); "
        "$contentLabel.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        f"$contentLabel.Text = {powershell_quote(content)}; $form.Controls.Add($contentLabel); "
        "$launch = New-Object System.Windows.Forms.CheckBox; "
        "$launch.Location = New-Object System.Drawing.Point(24,225); "
        "$launch.Size = New-Object System.Drawing.Size(250,28); "
        "$launch.Font = New-Object System.Drawing.Font('Segoe UI',9); "
        "$launch.Text = 'Anwendung starten'; "
        f"$launch.Checked = {checked}; $form.Controls.Add($launch); "
        "$finish = New-Object System.Windows.Forms.Button; "
        "$finish.Location = New-Object System.Drawing.Point(514,281); "
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
