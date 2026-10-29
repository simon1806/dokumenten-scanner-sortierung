param([string]$Destination = "", [switch]$KeepDownloads)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
function Assert-ProjectChildPath([string]$Path) {
    $resolved = [System.IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($ProjectRoot + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Der Vendor-Zielordner muss innerhalb des Projekts liegen: $resolved"
    }
    return $resolved
}
if (-not $Destination) { $Destination = Join-Path $ProjectRoot "vendor\Tesseract-OCR" }
$Destination = Assert-ProjectChildPath $Destination
$DownloadDir = Join-Path $ProjectRoot ".artifacts\downloads"
$NativeRoot = Assert-ProjectChildPath (Join-Path $ProjectRoot ".artifacts\tesseract-native-runtime")
$Runtime = Join-Path $NativeRoot "runtime"
$LockFile = Join-Path $PSScriptRoot "tesseract-runtime.lock.json"
New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null
if (Test-Path -LiteralPath $NativeRoot) { Remove-Item -LiteralPath $NativeRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

function Get-VerifiedDownload([string]$Url, [string]$Sha256) {
    $download = Join-Path $DownloadDir ([System.IO.Path]::GetFileName($Url))
    if (-not (Test-Path -LiteralPath $download) -or
        (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $Sha256) {
        Write-Host "Lade OCR-Laufzeitdatei: $Url"
        Invoke-WebRequest -Uri $Url -OutFile $download
    }
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $Sha256) {
        throw "SHA-256-Pruefung fehlgeschlagen: $download"
    }
    return $download
}
$packages = Get-Content -LiteralPath $LockFile -Raw | ConvertFrom-Json
foreach ($package in $packages) {
    if ($package.filename -notmatch '^mingw-w64-x86_64-[a-zA-Z0-9_.+~\-]+\.pkg\.tar\.zst$' -or
        $package.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw "Ungueltiger OCR-Paketeintrag." }
    $download = Get-VerifiedDownload ("https://repo.msys2.org/mingw/mingw64/" + $package.filename) $package.sha256
    & tar -xf $download -C $NativeRoot
    if ($LASTEXITCODE -ne 0) { throw "OCR-Laufzeitpaket konnte nicht entpackt werden: $download" }
}
# Build a clean runtime instead of retaining obsolete DLLs from the former installer.
Copy-Item -Path (Join-Path $NativeRoot "mingw64\bin\*.dll") -Destination $Runtime
Copy-Item -LiteralPath (Join-Path $NativeRoot "mingw64\share\licenses") -Destination $Runtime -Recurse
$tessdata = Join-Path $Runtime "tessdata"
New-Item -ItemType Directory -Path $tessdata | Out-Null
$models = @(
    @{ Language = "deu"; Repository = "tessdata"; Sha256 = "896B3B4956503AB9DAA10285DB330881B2D74B70D889B79262CC534B9EC699A4" },
    @{ Language = "eng"; Repository = "tessdata_fast"; Sha256 = "7D4322BD2A7749724879683FC3912CB542F19906C83BCC1A52132556427170B2" },
    @{ Language = "osd"; Repository = "tessdata_fast"; Sha256 = "9CF5D576FCC47564F11265841E5CA839001E7E6F38FF7F7AACF46D15A96B00FF" }
)
foreach ($model in $models) {
    $filename = $model.Language + ".traineddata"
    $existing = Join-Path $Destination "tessdata\$filename"
    if ((Test-Path -LiteralPath $existing) -and
        (Get-FileHash -LiteralPath $existing -Algorithm SHA256).Hash -eq $model.Sha256) {
        Copy-Item -LiteralPath $existing -Destination $tessdata
    } else {
        $url = "https://raw.githubusercontent.com/tesseract-ocr/" + $model.Repository + "/4.1.0/" + $filename
        $download = Get-VerifiedDownload $url $model.Sha256
        Copy-Item -LiteralPath $download -Destination (Join-Path $tessdata $filename)
    }
}
& (Join-Path $PSScriptRoot "build-tesseract-source.ps1") -LeptonicaRoot $NativeRoot -RuntimeDirectory $Runtime
$candidate = Assert-ProjectChildPath (Join-Path $ProjectRoot ".artifacts\tesseract-5.5.4-update\candidate")
$backup = Assert-ProjectChildPath (Join-Path $ProjectRoot (".artifacts\tesseract-backups\pre-update-" + [guid]::NewGuid().ToString("N")))
$failed = Assert-ProjectChildPath ($backup + "-failed")
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $backup), (Split-Path -Parent $Destination) | Out-Null
$hadPreviousRuntime = Test-Path -LiteralPath $Destination
if ($hadPreviousRuntime) { Move-Item -LiteralPath $Destination -Destination $backup }
try {
    Move-Item -LiteralPath $candidate -Destination $Destination
    $versionOutput = & (Join-Path $Destination "tesseract.exe") --version 2>&1
    if ($LASTEXITCODE -ne 0 -or ($versionOutput | Select-Object -First 1) -notmatch "^tesseract 5\.5\.4$" -or
        ($versionOutput -join [Environment]::NewLine) -notmatch "leptonica-1\.87\.0") { throw "Installierte OCR-Laufzeit ist ungueltig." }
    $languages = & (Join-Path $Destination "tesseract.exe") --list-langs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "OCR-Sprachpruefung fehlgeschlagen." }
    foreach ($language in @("deu", "eng", "osd")) {
        if ($languages -notcontains $language) { throw "OCR-Sprachmodell fehlt: $language" }
    }
} catch {
    if (Test-Path -LiteralPath $Destination) { Move-Item -LiteralPath $Destination -Destination $failed }
    if ($hadPreviousRuntime) { Move-Item -LiteralPath $backup -Destination $Destination }
    throw
}
if (-not $KeepDownloads) { Remove-Item -LiteralPath $NativeRoot -Recurse -Force }
Write-Host ($versionOutput -join [Environment]::NewLine)
Write-Host "Tesseract 5.5.4 mit aktuellen Bildbibliotheken ist vorbereitet. Es wurde keine Anwendungsrelease gebaut."
