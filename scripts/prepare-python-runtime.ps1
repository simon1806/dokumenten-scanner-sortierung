param([string]$Destination = "")

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
if (-not $Destination) { $Destination = Join-Path $ProjectRoot "vendor\Python-3.14.10" }
$Destination = [System.IO.Path]::GetFullPath($Destination)
if (-not $Destination.StartsWith($ProjectRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Python-Zielordner muss innerhalb des Projekts liegen: $Destination"
}
$url = "https://www.python.org/ftp/python/3.14.10/python-3.14.10-amd64.zip"
$sha256 = "CC28026A650970CC1570BB127312F1F9634EB67ABFE157AE6CA1ACBBC627D091"
$downloadRoot = Join-Path $ProjectRoot ".artifacts\downloads"
$archive = Join-Path $downloadRoot "python-3.14.10-amd64.zip"
$python = Join-Path $Destination "python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
    if (-not (Test-Path -LiteralPath $archive) -or
        (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $sha256) {
        Invoke-WebRequest -Uri $url -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $sha256) {
        throw "Python-Archiv hat eine falsche SHA-256-Pruefsumme."
    }
    Expand-Archive -LiteralPath $archive -DestinationPath $Destination
}
$probe = @'
import json, platform, struct, tkinter
assert platform.python_version() == "3.14.10"
assert struct.calcsize("P") == 8
root = tkinter.Tk()
root.withdraw()
try:
    tcl = str(root.tk.call("info", "patchlevel"))
    tk = str(root.tk.call("package", "present", "Tk"))
    assert (tcl, tk) == ("9.0.4", "9.0.4"), (tcl, tk)
    print(json.dumps({"python": platform.python_version(), "tcl": tcl, "tk": tk}))
finally:
    root.destroy()
'@
$result = $probe | & $python -
if ($LASTEXITCODE -ne 0) { throw "Python-/Tcl-/Tk-Laufzeitpruefung fehlgeschlagen." }
$result | Write-Host
Write-Host "Projektlokale Python-Laufzeit vorbereitet; es wurde keine Anwendungsrelease gebaut."
