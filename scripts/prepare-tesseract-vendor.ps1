param(
    [string]$InstallerUrl = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe",
    [string]$InstallerSha256 = "F3FC4236425B690C8BE756F35793F77394EE004BE0A6460A440C754D892F68BC",
    [string]$GermanDataUrl = "https://github.com/tesseract-ocr/tessdata/raw/refs/tags/4.1.0/deu.traineddata",
    [string]$GermanDataSha256 = "896B3B4956503AB9DAA10285DB330881B2D74B70D889B79262CC534B9EC699A4",
    [string]$Destination = "",
    [switch]$KeepInstaller
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

if (-not $Destination) {
    $Destination = Join-Path $ProjectRoot "vendor\Tesseract-OCR"
}

$Destination = [System.IO.Path]::GetFullPath($Destination)
$DownloadDir = Join-Path $ProjectRoot ".artifacts\downloads"
$InstallerPath = Join-Path $DownloadDir ([System.IO.Path]::GetFileName($InstallerUrl))
$GermanDataDownload = Join-Path $DownloadDir "deu-4.1.0.traineddata"
$TesseractExe = Join-Path $Destination "tesseract.exe"
$SystemTesseractDir = Join-Path $env:ProgramFiles "Tesseract-OCR"
$SystemTesseractExe = Join-Path $SystemTesseractDir "tesseract.exe"

New-Item -ItemType Directory -Force -Path $DownloadDir, $Destination | Out-Null

if (Test-Path $TesseractExe) {
    $ExistingVersion = (& $TesseractExe --version | Select-Object -First 1)
    if ($ExistingVersion -notmatch "v5\.5\.0") {
        throw "Der Zielordner enthaelt nicht Tesseract 5.5.0: $ExistingVersion. Den bisherigen Ordner zuerst sichern oder verschieben."
    }
} else {
    if (Test-Path $SystemTesseractExe) {
        $SystemVersion = (& $SystemTesseractExe --version | Select-Object -First 1)
        if ($SystemVersion -notmatch "v5\.5\.0") {
            throw "Die vorhandene Systeminstallation ist nicht Tesseract 5.5.0: $SystemVersion"
        }
        Write-Host "Kopiere die vorhandene Tesseract-5.5.0-Systeminstallation:"
        Write-Host $SystemTesseractDir
        Copy-Item -Path (Join-Path $SystemTesseractDir "*") -Destination $Destination -Recurse -Force
    } else {
        Write-Host "Lade Tesseract-Installer:"
        Write-Host $InstallerUrl
        Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath -Headers @{
            "User-Agent" = "Mozilla/5.0 Windows Tesseract Vendor Preparation"
        }

        $ActualSha256 = (Get-FileHash -LiteralPath $InstallerPath -Algorithm SHA256).Hash
        if ($ActualSha256 -ne $InstallerSha256) {
            throw "Die SHA-256-Pruefsumme des Tesseract-Installers stimmt nicht. Erwartet: $InstallerSha256, erhalten: $ActualSha256"
        }

        Write-Host "Installiere Tesseract nach:"
        Write-Host $Destination
        $arguments = @(
            "/S",
            "/D=$Destination"
        )
        $process = Start-Process -FilePath $InstallerPath -ArgumentList $arguments -Wait -PassThru
        if ($process.ExitCode -ne 0) {
            throw "Tesseract-Installer wurde mit Exitcode $($process.ExitCode) beendet."
        }
        if (-not (Test-Path $TesseractExe) -and (Test-Path $SystemTesseractExe)) {
            $InstalledVersion = (& $SystemTesseractExe --version | Select-Object -First 1)
            if ($InstalledVersion -match "v5\.5\.0") {
                Copy-Item -Path (Join-Path $SystemTesseractDir "*") -Destination $Destination -Recurse -Force
            }
        }
    }
}

$GermanData = Join-Path $Destination "tessdata\deu.traineddata"
$EnglishData = Join-Path $Destination "tessdata\eng.traineddata"

if (-not (Test-Path $TesseractExe)) {
    throw "tesseract.exe wurde nicht gefunden: $TesseractExe"
}
if (-not (Test-Path $GermanData) -or (Get-FileHash -LiteralPath $GermanData -Algorithm SHA256).Hash -ne $GermanDataSha256) {
    Write-Host "Lade das deutsche Sprachmodell tessdata 4.1.0:"
    Write-Host $GermanDataUrl
    Invoke-WebRequest -Uri $GermanDataUrl -OutFile $GermanDataDownload -Headers @{
        "User-Agent" = "Mozilla/5.0 Windows Tesseract Vendor Preparation"
    }
    $ActualGermanDataSha256 = (Get-FileHash -LiteralPath $GermanDataDownload -Algorithm SHA256).Hash
    if ($ActualGermanDataSha256 -ne $GermanDataSha256) {
        throw "Die SHA-256-Pruefsumme des deutschen Sprachmodells stimmt nicht. Erwartet: $GermanDataSha256, erhalten: $ActualGermanDataSha256"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $GermanData) | Out-Null
    Copy-Item -LiteralPath $GermanDataDownload -Destination $GermanData -Force
}
if (-not (Test-Path $EnglishData)) {
    throw "Englisches Sprachpaket wurde nicht gefunden: $EnglishData"
}

if (-not $KeepInstaller) {
    Remove-Item -LiteralPath $InstallerPath, $GermanDataDownload -Force -ErrorAction SilentlyContinue
}

Write-Host "Tesseract ist vorbereitet. Der Release-Build nimmt diesen Ordner automatisch mit."
