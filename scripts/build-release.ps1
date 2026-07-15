param(
    [string]$Version = "0.1.19",
    [string]$TesseractDir = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BuildRoot = Join-Path $ProjectRoot ".artifacts"
$MainDist = Join-Path $BuildRoot "main"
$Release = Join-Path $ProjectRoot "release"
$MainName = "DokumentenScannerSortierung"
$MainExecutable = Join-Path $MainDist "$MainName.exe"
$UninstallerSource = Join-Path $ProjectRoot "installer\uninstall.ps1"
$IconAssets = Join-Path $ProjectRoot "src\scanner_sorter\assets\icons\tabler"
$AppAssets = Join-Path $ProjectRoot "src\scanner_sorter\assets\app"
$AppIcon = Join-Path $AppAssets "dokumenten-scanner-sortierung.ico"
$ThirdPartyNotices = Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md"
$VersionPayload = Join-Path $BuildRoot "version.txt"

if (-not (Test-Path $Python)) {
    throw "Virtuelle Umgebung nicht gefunden: $Python"
}

$BasePython = & $Python -c "import sys; print(sys.base_prefix)"
$TclRoot = Join-Path $BasePython "tcl"
$DllRoot = Join-Path $BasePython "DLLs"
$TkinterPackage = Join-Path $BasePython "Lib\tkinter"
$TkinterHook = Join-Path $PSScriptRoot "pyinstaller_tkinter_hook.py"
$DefaultTesseractDir = Join-Path $ProjectRoot "vendor\Tesseract-OCR"

if (-not $TesseractDir -and (Test-Path $DefaultTesseractDir)) {
    $TesseractDir = $DefaultTesseractDir
}

$TesseractArgs = @()
if ($TesseractDir) {
    $ResolvedTesseractDir = (Resolve-Path $TesseractDir).Path
    if (-not (Test-Path (Join-Path $ResolvedTesseractDir "tesseract.exe"))) {
        throw "tesseract.exe nicht gefunden in: $ResolvedTesseractDir"
    }
    $TesseractArgs = @("--add-data", "$ResolvedTesseractDir;Tesseract-OCR")
    Write-Host "Tesseract wird mitgeliefert aus: $ResolvedTesseractDir"
} else {
    Write-Host "Tesseract wird nicht mitgeliefert. Optional: -TesseractDir C:\Pfad\zu\Tesseract-OCR"
}

New-Item -ItemType Directory -Force -Path $MainDist, $Release | Out-Null
[System.IO.File]::WriteAllText($VersionPayload, "$Version`n", [System.Text.Encoding]::UTF8)

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $MainName `
    --icon $AppIcon `
    --paths (Join-Path $ProjectRoot "src") `
    --hidden-import fitz `
    --hidden-import zxingcpp `
    --hidden-import pytesseract `
    --hidden-import pystray `
    --hidden-import pystray._win32 `
    --hidden-import tkinter `
    --hidden-import _tkinter `
    --collect-all fitz `
    --collect-all zxingcpp `
    --collect-all pystray `
    --copy-metadata Pillow `
    --copy-metadata PyMuPDF `
    --copy-metadata pypdf `
    --copy-metadata pytesseract `
    --copy-metadata pystray `
    --copy-metadata zxing-cpp `
    --add-binary "$(Join-Path $DllRoot '_tkinter.pyd');." `
    --add-binary "$(Join-Path $DllRoot 'tcl86t.dll');." `
    --add-binary "$(Join-Path $DllRoot 'tk86t.dll');." `
    --add-data "$TkinterPackage;tkinter" `
    --add-data "$(Join-Path $TclRoot 'tcl8.6');_tcl_data" `
    --add-data "$(Join-Path $TclRoot 'tk8.6');_tk_data" `
    --add-data "$IconAssets;scanner_sorter/assets/icons/tabler" `
    --add-data "$AppAssets;scanner_sorter/assets/app" `
    $TesseractArgs `
    --runtime-hook $TkinterHook `
    --distpath $MainDist `
    --workpath (Join-Path $BuildRoot "work-main") `
    --specpath (Join-Path $BuildRoot "spec") `
    (Join-Path $ProjectRoot "src\main.py")

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "DokumentenScannerSortierung-Setup" `
    --icon $AppIcon `
    --paths $ProjectRoot `
    --add-data "$MainExecutable;payload" `
    --add-data "$UninstallerSource;payload" `
    --add-data "$ThirdPartyNotices;payload" `
    --add-data "$AppIcon;payload" `
    --add-data "$VersionPayload;payload" `
    --distpath $Release `
    --workpath (Join-Path $BuildRoot "work-setup") `
    --specpath (Join-Path $BuildRoot "spec") `
    (Join-Path $ProjectRoot "installer\installer.py")

Copy-Item -Force $MainExecutable (Join-Path $Release "$MainName-$Version.exe")
Write-Host "Release-Dateien erstellt: $Release"
