param(
    [string]$Version = "0.1.0"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$BuildRoot = Join-Path $ProjectRoot ".artifacts"
$MainDist = Join-Path $BuildRoot "main"
$Release = Join-Path $ProjectRoot "release"
$MainName = "DokumentenScannerSortierung"
$MainExecutable = Join-Path $MainDist "$MainName.exe"

if (-not (Test-Path $Python)) {
    throw "Virtuelle Umgebung nicht gefunden: $Python"
}

$BasePython = & $Python -c "import sys; print(sys.base_prefix)"
$TclRoot = Join-Path $BasePython "tcl"
$DllRoot = Join-Path $BasePython "DLLs"
$TkinterHook = Join-Path $PSScriptRoot "pyinstaller_tkinter_hook.py"

New-Item -ItemType Directory -Force -Path $MainDist, $Release | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name $MainName `
    --paths (Join-Path $ProjectRoot "src") `
    --hidden-import fitz `
    --hidden-import zxingcpp `
    --hidden-import pytesseract `
    --hidden-import tkinter `
    --hidden-import _tkinter `
    --collect-all fitz `
    --collect-all zxingcpp `
    --add-binary "$(Join-Path $DllRoot '_tkinter.pyd');." `
    --add-binary "$(Join-Path $DllRoot 'tcl86t.dll');." `
    --add-binary "$(Join-Path $DllRoot 'tk86t.dll');." `
    --add-data "$(Join-Path $TclRoot 'tcl8.6');tcl\tcl8.6" `
    --add-data "$(Join-Path $TclRoot 'tk8.6');tcl\tk8.6" `
    --runtime-hook $TkinterHook `
    --distpath $MainDist `
    --workpath (Join-Path $BuildRoot "work-main") `
    --specpath (Join-Path $BuildRoot "spec") `
    (Join-Path $ProjectRoot "src\scanner_sorter\app.py")

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name "DokumentenScannerSortierung-Setup" `
    --hidden-import tkinter `
    --hidden-import _tkinter `
    --add-binary "$(Join-Path $DllRoot '_tkinter.pyd');." `
    --add-binary "$(Join-Path $DllRoot 'tcl86t.dll');." `
    --add-binary "$(Join-Path $DllRoot 'tk86t.dll');." `
    --add-data "$(Join-Path $TclRoot 'tcl8.6');tcl\tcl8.6" `
    --add-data "$(Join-Path $TclRoot 'tk8.6');tcl\tk8.6" `
    --runtime-hook $TkinterHook `
    --add-data "$MainExecutable;payload" `
    --distpath $Release `
    --workpath (Join-Path $BuildRoot "work-setup") `
    --specpath (Join-Path $BuildRoot "spec") `
    (Join-Path $ProjectRoot "installer\installer.py")

Copy-Item -Force $MainExecutable (Join-Path $Release "$MainName-$Version.exe")
Write-Host "Release-Dateien erstellt: $Release"
