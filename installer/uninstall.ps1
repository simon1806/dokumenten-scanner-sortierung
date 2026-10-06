param(
    [switch]$Silent
)

$ErrorActionPreference = "Stop"
$ProductName = "Dokumenten-Scanner-Sortierung"
$ApplicationFolder = "DokumentenScannerSortierung"
$ApplicationFilename = "DokumentenScannerSortierung.exe"
$OpenLauncherFilename = "DokumentenScannerSortierung-Oeffnen.exe"
$NoticeFilename = "THIRD_PARTY_NOTICES.md"
$IconFilename = "DokumentenScannerSortierung.ico"
$VersionFilename = "version.txt"
$RuntimeManifestFilename = "runtime-files.json"
$LegacyUninstallerFilename = "DokumentenScannerSortierung-Deinstallieren.exe"
$ShortcutFilename = "Dokumenten-Scanner-Sortierung.lnk"
$ServerAutostartTaskName = "GlasHagen Dokumenten-Scanner-Sortierung"
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
    if ((Get-Item -LiteralPath $InstallFolder -Force).Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
        throw "Der Installationsordner ist ein Reparse-Point."
    }
    if (-not (Confirm-Uninstall)) {
        exit 0
    }

    # Remove only manifest-owned, unchanged runtime files. Never recursively
    # delete the runtime directory: unknown files and junctions stay intact.
    $runtimeManifestPath = Join-Path $InstallFolder $RuntimeManifestFilename
    if (Test-Path -LiteralPath $runtimeManifestPath) {
        $runtimeManifestItem = Get-Item -LiteralPath $runtimeManifestPath -Force
        if ($runtimeManifestItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
            throw "Die Runtime-Dateiliste ist ein Reparse-Point."
        }
        $runtimeManifest = Get-Content -LiteralPath $runtimeManifestPath -Raw | ConvertFrom-Json
        $runtimeDirectories = @{}
        $runtimeFilesToRemove = @()
        foreach ($entry in $runtimeManifest.PSObject.Properties) {
            $parts = $entry.Name.Split('/')
            if ($parts.Count -lt 2 -or $parts[0] -cne '_internal') {
                throw "Nicht erlaubter Runtime-Pfad: $($entry.Name)"
            }
            foreach ($part in $parts) {
                if (-not $part -or $part -in @('.', '..') -or $part -match '[\\:<>"|?*\x00-\x1f]' -or $part -match '[ .]$') {
                    throw "Ungültiger Runtime-Pfad: $($entry.Name)"
                }
            }
            $runtimePath = [System.IO.Path]::GetFullPath((Join-Path $InstallFolder $entry.Name))
            if (-not $runtimePath.StartsWith($resolvedActual + '\_internal\', [System.StringComparison]::OrdinalIgnoreCase)) {
                throw "Runtime-Pfad liegt außerhalb des Programmordners."
            }
            $currentPath = $InstallFolder
            foreach ($part in $parts) {
                $currentPath = Join-Path $currentPath $part
                if (Test-Path -LiteralPath $currentPath) {
                    $currentItem = Get-Item -LiteralPath $currentPath -Force
                    if ($currentItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                        throw "Runtime-Pfad enthält einen Reparse-Point: $currentPath"
                    }
                }
            }
            if (Test-Path -LiteralPath $runtimePath -PathType Leaf) {
                $runtimeItem = Get-Item -LiteralPath $runtimePath -Force
                $runtimeStream = [System.IO.File]::OpenRead($runtimePath)
                $runtimeHasher = [System.Security.Cryptography.SHA256]::Create()
                try {
                    $runtimeHash = [System.BitConverter]::ToString($runtimeHasher.ComputeHash($runtimeStream)).Replace('-', '')
                } finally {
                    $runtimeHasher.Dispose()
                    $runtimeStream.Dispose()
                }
                if ($runtimeItem.Length -eq $entry.Value.size -and $runtimeHash -eq $entry.Value.sha256) {
                    $runtimeFilesToRemove += $runtimePath
                }
            }
            $runtimeParent = Split-Path -Parent $runtimePath
            while ($runtimeParent.StartsWith($resolvedActual + '\_internal', [System.StringComparison]::OrdinalIgnoreCase)) {
                $runtimeDirectories[$runtimeParent] = $true
                $runtimeParent = Split-Path -Parent $runtimeParent
            }
        }
        foreach ($runtimePath in $runtimeFilesToRemove) {
            Remove-Item -LiteralPath $runtimePath -Force -ErrorAction Stop
        }
        foreach ($runtimeDirectory in ($runtimeDirectories.Keys | Sort-Object Length -Descending)) {
            if ((Test-Path -LiteralPath $runtimeDirectory -PathType Container) -and
                -not (Get-ChildItem -LiteralPath $runtimeDirectory -Force | Select-Object -First 1)) {
                Remove-Item -LiteralPath $runtimeDirectory -Force -ErrorAction Stop
            }
        }
        Remove-Item -LiteralPath $runtimeManifestPath -Force -ErrorAction Stop
    }

    foreach ($filename in @(
        $ApplicationFilename,
        $OpenLauncherFilename,
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
    try {
        $serverTask = Get-ScheduledTask -TaskName $ServerAutostartTaskName -ErrorAction SilentlyContinue
        if ($serverTask) {
            Unregister-ScheduledTask -TaskName $ServerAutostartTaskName -Confirm:$false -ErrorAction Stop
        }
    } catch {
        # Die Aufgabe ist optional. Ohne Administratorrechte darf die reguläre
        # Benutzer-Deinstallation trotzdem die eigenen Programmdateien entfernen.
        $serverAutostartWarning = "Die optionale Serverstartaufgabe konnte nicht entfernt werden. Starten Sie die Deinstallation bei Bedarf als Administrator."
    }
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
    "Die Anwendung wurde entfernt. Einstellungen, Protokolle und Dokumentordner wurden beibehalten." +
    $(if ($serverAutostartWarning) { "`n`n$serverAutostartWarning" } else { "" })
)

$scriptPath = $PSCommandPath
Remove-Item -LiteralPath $scriptPath -Force -ErrorAction SilentlyContinue
if (-not (Get-ChildItem -LiteralPath $InstallFolder -Force -ErrorAction SilentlyContinue | Select-Object -First 1)) {
    Remove-Item -LiteralPath $InstallFolder -Force -ErrorAction SilentlyContinue
}
exit 0
