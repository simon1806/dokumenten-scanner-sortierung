param(
    [string]$InstallerUrl = "https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe",
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

New-Item -ItemType Directory -Force -Path $DownloadDir, $Destination | Out-Null

Write-Host "Lade Tesseract-Installer:"
Write-Host $InstallerUrl
Invoke-WebRequest -Uri $InstallerUrl -OutFile $InstallerPath -Headers @{
    "User-Agent" = "Mozilla/5.0 Windows Tesseract Vendor Preparation"
}

Write-Host "Installiere Tesseract nach:"
Write-Host $Destination
$arguments = @(
    "/VERYSILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CURRENTUSER",
    "/DIR=$Destination"
)
$process = Start-Process -FilePath $InstallerPath -ArgumentList $arguments -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Tesseract-Installer wurde mit Exitcode $($process.ExitCode) beendet."
}

$TesseractExe = Join-Path $Destination "tesseract.exe"
$GermanData = Join-Path $Destination "tessdata\deu.traineddata"
$EnglishData = Join-Path $Destination "tessdata\eng.traineddata"

if (-not (Test-Path $TesseractExe)) {
    throw "tesseract.exe wurde nicht gefunden: $TesseractExe"
}
if (-not (Test-Path $GermanData)) {
    throw "Deutsches Sprachpaket wurde nicht gefunden: $GermanData"
}
if (-not (Test-Path $EnglishData)) {
    throw "Englisches Sprachpaket wurde nicht gefunden: $EnglishData"
}

if (-not $KeepInstaller) {
    Remove-Item -LiteralPath $InstallerPath -Force
}

Write-Host "Tesseract ist vorbereitet. Der Release-Build nimmt diesen Ordner automatisch mit."
