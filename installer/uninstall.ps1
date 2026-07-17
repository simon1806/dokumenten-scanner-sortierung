param(
    [switch]$Silent
)

$ErrorActionPreference = "Stop"
$ProductName = "Dokumenten-Scanner-Sortierung"
$ApplicationFolder = "DokumentenScannerSortierung"
$ApplicationFilename = "DokumentenScannerSortierung.exe"
$NoticeFilename = "THIRD_PARTY_NOTICES.md"
$IconFilename = "DokumentenScannerSortierung.ico"
$VersionFilename = "version.txt"
$LegacyUninstallerFilename = "DokumentenScannerSortierung-Deinstallieren.exe"
$ShortcutFilename = "Dokumenten-Scanner-Sortierung.lnk"
$RegistryPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\DokumentenScannerSortierung"
$ExpectedFolder = Join-Path $env:LOCALAPPDATA "Programs\$ApplicationFolder"
$InstallFolder = $PSScriptRoot

function Show-Message([string]$Title, [string]$Message, [bool]$ErrorIcon = $false) {
    if ($Silent) {
        return
    }
    Add-Type -AssemblyName System.Windows.Forms
    $icon = if ($ErrorIcon) {
        [System.Windows.Forms.MessageBoxIcon]::Error
    } else {
        [System.Windows.Forms.MessageBoxIcon]::Information
    }
    [void][System.Windows.Forms.MessageBox]::Show(
        $Message,
        $Title,
        [System.Windows.Forms.MessageBoxButtons]::OK,
        $icon
    )
}

function Confirm-Uninstall {
    if ($Silent) {
        return $true
    }
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    $form = New-Object System.Windows.Forms.Form
    $form.Text = "Deinstallation bestätigen"
    $form.ClientSize = New-Object System.Drawing.Size(560, 245)
    $form.StartPosition = "CenterScreen"
    $form.FormBorderStyle = "FixedDialog"
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.ShowInTaskbar = $true
    $iconPath = Join-Path $InstallFolder $IconFilename
    if (Test-Path -LiteralPath $iconPath) {
        $form.Icon = New-Object System.Drawing.Icon($iconPath)
    }

    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = "Top"
    $header.Height = 70
    $header.BackColor = [System.Drawing.Color]::FromArgb(23, 53, 75)
    $form.Controls.Add($header)

    $titleLabel = New-Object System.Windows.Forms.Label
    $titleLabel.AutoSize = $false
    $titleLabel.Location = New-Object System.Drawing.Point(22, 15)
    $titleLabel.Size = New-Object System.Drawing.Size(510, 40)
    $titleLabel.ForeColor = [System.Drawing.Color]::White
    $titleLabel.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 14)
    $titleLabel.Text = "$ProductName deinstallieren?"
    $header.Controls.Add($titleLabel)

    $contentLabel = New-Object System.Windows.Forms.Label
    $contentLabel.AutoSize = $false
    $contentLabel.Location = New-Object System.Drawing.Point(24, 91)
    $contentLabel.Size = New-Object System.Drawing.Size(510, 70)
    $contentLabel.Font = New-Object System.Drawing.Font("Segoe UI", 9)
    $contentLabel.Text = "Programmdateien, Desktop-Verknüpfung und Windows-Eintrag werden entfernt. Einstellungen, Protokolle und sämtliche Dokumentordner bleiben erhalten."
    $form.Controls.Add($contentLabel)

    $action = New-Object System.Windows.Forms.Button
    $action.Location = New-Object System.Drawing.Point(274, 186)
    $action.Size = New-Object System.Drawing.Size(150, 36)
    $action.BackColor = [System.Drawing.Color]::FromArgb(182, 58, 58)
    $action.ForeColor = [System.Drawing.Color]::White
    $action.FlatStyle = "Flat"
    $action.Text = "Deinstallieren"
    $action.Add_Click({ $form.Tag = "confirmed"; $form.Close() })
    $form.Controls.Add($action)

    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Location = New-Object System.Drawing.Point(434, 186)
    $cancel.Size = New-Object System.Drawing.Size(100, 36)
    $cancel.Text = "Abbrechen"
    $cancel.Add_Click({ $form.Close() })
    $form.Controls.Add($cancel)
    $form.AcceptButton = $action
    $form.CancelButton = $cancel
    $form.Add_Shown({ $action.Focus() })
    [void]$form.ShowDialog()
    return $form.Tag -eq "confirmed"
}

try {
    $resolvedExpected = [System.IO.Path]::GetFullPath($ExpectedFolder).TrimEnd("\")
    $resolvedActual = [System.IO.Path]::GetFullPath($InstallFolder).TrimEnd("\")
    if ($resolvedActual -ne $resolvedExpected) {
        throw "Der Installationspfad ist nicht sicher."
    }
    if (-not (Confirm-Uninstall)) {
        exit 0
    }

    foreach ($filename in @(
        $ApplicationFilename,
        $NoticeFilename,
        $IconFilename,
        $VersionFilename,
        $LegacyUninstallerFilename
    )) {
        $installedFile = Join-Path $InstallFolder $filename
        if (Test-Path -LiteralPath $installedFile) {
            Remove-Item -LiteralPath $installedFile -Force -ErrorAction Stop
        }
    }
    $desktop = [Environment]::GetFolderPath("Desktop")
    Remove-Item -LiteralPath (Join-Path $desktop $ShortcutFilename) -Force -ErrorAction SilentlyContinue
    $startup = [Environment]::GetFolderPath("Startup")
    Remove-Item -LiteralPath (Join-Path $startup $ShortcutFilename) -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $RegistryPath -Force -ErrorAction SilentlyContinue
} catch [System.UnauthorizedAccessException] {
    Show-Message "Deinstallation nicht möglich" (
        "Die Anwendung läuft wahrscheinlich noch. Bitte beenden Sie sie vollständig und versuchen Sie es erneut.`n`n" +
        "Technische Details: $($_.Exception.Message)"
    ) $true
    exit 1
} catch {
    Show-Message "Deinstallation fehlgeschlagen" $_.Exception.Message $true
    exit 1
}

Show-Message "Deinstallation abgeschlossen" (
    "Die Anwendung wurde entfernt. Einstellungen, Protokolle und Dokumentordner wurden beibehalten."
)

$scriptPath = $PSCommandPath
Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue
if (-not (Get-ChildItem -LiteralPath $InstallFolder -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    Remove-Item -LiteralPath $InstallFolder -Force -ErrorAction SilentlyContinue
}
exit 0
