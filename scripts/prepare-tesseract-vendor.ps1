param(
    [string]$BaseInstallerUrl = "https://github.com/tesseract-ocr/tesseract/releases/download/5.5.0/tesseract-ocr-w64-setup-5.5.0.20241111.exe",
    [string]$BaseInstallerSha256 = "F3FC4236425B690C8BE756F35793F77394EE004BE0A6460A440C754D892F68BC",
    [string]$TesseractPackageUrl = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-tesseract-ocr-5.5.2-1-any.pkg.tar.zst",
    [string]$TesseractPackageSha256 = "6667BE5FCD6A9489D65B84C954DAF21B3994155ADA92AD703EDCEC72B374D2EA",
    [string]$LeptonicaPackageUrl = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-leptonica-1.87.0-1-any.pkg.tar.zst",
    [string]$LeptonicaPackageSha256 = "702D6EE60255B083AA37A3CBBE1A53EF253D9119204D0478D025EFEA2D0C91F9",
    [string]$GccRuntimePackageUrl = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-gcc-libs-16.1.0-5-any.pkg.tar.zst",
    [string]$GccRuntimePackageSha256 = "AA560F5438C35B71C3E7B24FD5BECBCA028F70C5B4D1F1697A86FF80FEC947DA",
    [string]$WinPthreadsPackageUrl = "https://mirror.msys2.org/mingw/mingw64/mingw-w64-x86_64-libwinpthread-14.0.0.r179.g24aaa6147-1-any.pkg.tar.zst",
    [string]$WinPthreadsPackageSha256 = "8F12DC1BE987165FAAB6363A159921553B4A2AC64E443CD0E7C501C343C2A92A",
    [string]$GermanDataUrl = "https://github.com/tesseract-ocr/tessdata/raw/refs/tags/4.1.0/deu.traineddata",
    [string]$GermanDataSha256 = "896B3B4956503AB9DAA10285DB330881B2D74B70D889B79262CC534B9EC699A4",
    [string]$Destination = "",
    [switch]$KeepDownloads
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

if (-not $Destination) {
    $Destination = Join-Path $ProjectRoot "vendor\Tesseract-OCR"
}

$Destination = [System.IO.Path]::GetFullPath($Destination)
$ResolvedProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd("\")
if (-not $Destination.StartsWith(
        $ResolvedProjectRoot + "\",
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
    throw "Der Vendor-Zielordner muss innerhalb des Projekts liegen: $Destination"
}
$DownloadDir = Join-Path $ProjectRoot ".artifacts\downloads"
$ExtractionRoot = Join-Path $ProjectRoot ".artifacts\tesseract-packages"
$TesseractExe = Join-Path $Destination "tesseract.exe"
$GermanData = Join-Path $Destination "tessdata\deu.traineddata"
$EnglishData = Join-Path $Destination "tessdata\eng.traineddata"
$OsdData = Join-Path $Destination "tessdata\osd.traineddata"

function Get-VerifiedDownload(
    [string]$Url,
    [string]$Sha256
) {
    $download = Join-Path $DownloadDir ([System.IO.Path]::GetFileName($Url))
    if (-not (Test-Path -LiteralPath $download) -or
        (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $Sha256) {
        Write-Host "Lade herunter: $Url"
        Invoke-WebRequest -Uri $Url -OutFile $download -Headers @{
            "User-Agent" = "Dokumenten-Scanner-Sortierung Build"
        }
    }
    $actualSha256 = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash
    if ($actualSha256 -ne $Sha256) {
        throw "Die SHA-256-Pruefsumme stimmt nicht. Erwartet: $Sha256, erhalten: $actualSha256"
    }
    return $download
}

function Expand-Msys2Package(
    [string]$PackagePath,
    [string]$Name
) {
    $target = Join-Path $ExtractionRoot $Name
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    & tar -xf $PackagePath -C $target
    if ($LASTEXITCODE -ne 0) {
        throw "MSYS2-Paket konnte nicht entpackt werden: $PackagePath"
    }
    return $target
}

function Copy-PackageFile(
    [string]$PackageRoot,
    [string]$RelativePath,
    [string]$TargetDirectory
) {
    $source = Join-Path $PackageRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Datei fehlt im MSYS2-Paket: $RelativePath"
    }
    New-Item -ItemType Directory -Force -Path $TargetDirectory | Out-Null
    Copy-Item -LiteralPath $source -Destination $TargetDirectory -Force
}

function Find-WindowsBase {
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles "Tesseract-OCR"),
        (Join-Path ${env:ProgramFiles(x86)} "Tesseract-OCR"),
        (Join-Path $env:LOCALAPPDATA "Programs\Tesseract-OCR"),
        (Join-Path $env:LOCALAPPDATA "Tesseract-OCR")
    )) {
        $candidateExe = Join-Path $candidate "tesseract.exe"
        if (Test-Path -LiteralPath $candidateExe) {
            $candidateVersion = (& $candidateExe --version 2>&1 | Select-Object -First 1)
            if ($candidateVersion -match "tesseract v5\.5\.0") {
                return $candidate
            }
        }
    }
    return $null
}

New-Item -ItemType Directory -Force -Path $DownloadDir, $ExtractionRoot, $Destination | Out-Null

if (-not (Test-Path -LiteralPath $TesseractExe)) {
    $installedBase = Find-WindowsBase
    if (-not $installedBase) {
        $baseInstaller = Get-VerifiedDownload $BaseInstallerUrl $BaseInstallerSha256
        Write-Host "Bereite Windows-Abhaengigkeiten vor: $Destination"
        $process = Start-Process -FilePath $baseInstaller -ArgumentList @("/S", "/D=$Destination") `
            -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0) {
            throw "Tesseract-Basisinstaller wurde mit Exitcode $($process.ExitCode) beendet."
        }
        $installedBase = Find-WindowsBase
    }
    if (-not (Test-Path -LiteralPath $TesseractExe)) {
        if (-not $installedBase) {
            throw "Die installierten Tesseract-Basisdateien wurden nicht gefunden."
        }
        Write-Host "Kopiere Windows-Abhaengigkeiten aus: $installedBase"
        Copy-Item -Path (Join-Path $installedBase "*") -Destination $Destination -Recurse -Force
    }
}

$existingVersion = (& $TesseractExe --version 2>&1 | Select-Object -First 1)
if ($existingVersion -notmatch "tesseract (v5\.5\.0|5\.5\.2)") {
    throw "Der Zielordner enthaelt keine unterstuetzte Tesseract-Basis: $existingVersion"
}

$tesseractPackage = Get-VerifiedDownload $TesseractPackageUrl $TesseractPackageSha256
$leptonicaPackage = Get-VerifiedDownload $LeptonicaPackageUrl $LeptonicaPackageSha256
$gccPackage = Get-VerifiedDownload $GccRuntimePackageUrl $GccRuntimePackageSha256
$winPthreadsPackage = Get-VerifiedDownload $WinPthreadsPackageUrl $WinPthreadsPackageSha256
$tesseractRoot = Expand-Msys2Package $tesseractPackage "tesseract-5.5.2"
$leptonicaRoot = Expand-Msys2Package $leptonicaPackage "leptonica-1.87.0"
$gccRoot = Expand-Msys2Package $gccPackage "gcc-runtime"
$winPthreadsRoot = Expand-Msys2Package $winPthreadsPackage "winpthreads-runtime"

Copy-PackageFile $tesseractRoot "mingw64\bin\tesseract.exe" $Destination
Copy-PackageFile $tesseractRoot "mingw64\bin\libtesseract-5.5.dll" $Destination
Copy-PackageFile $leptonicaRoot "mingw64\bin\libleptonica-6.dll" $Destination
foreach ($runtimeFile in @(
    "libatomic-1.dll",
    "libgcc_s_seh-1.dll",
    "libgomp-1.dll",
    "libquadmath-0.dll",
    "libstdc++-6.dll"
)) {
    Copy-PackageFile $gccRoot "mingw64\bin\$runtimeFile" $Destination
}
Copy-PackageFile $winPthreadsRoot "mingw64\bin\libwinpthread-1.dll" $Destination

$licenses = Join-Path $Destination "licenses"
Copy-PackageFile $tesseractRoot "mingw64\share\licenses\tesseract-ocr\LICENSE" `
    (Join-Path $licenses "tesseract-ocr")
Copy-PackageFile $leptonicaRoot "mingw64\share\licenses\leptonica\LICENSE" `
    (Join-Path $licenses "leptonica")
foreach ($licenseFile in @("COPYING.LIB", "COPYING.RUNTIME", "COPYING3", "README")) {
    Copy-PackageFile $gccRoot "mingw64\share\licenses\gcc-libs\$licenseFile" `
        (Join-Path $licenses "gcc-runtime")
}
Copy-PackageFile $winPthreadsRoot "mingw64\share\licenses\libwinpthread\COPYING" `
    (Join-Path $licenses "libwinpthread")

Remove-Item -LiteralPath (Join-Path $Destination "libtesseract-5.dll") -Force -ErrorAction SilentlyContinue
Get-ChildItem -LiteralPath $Destination -File -Filter "*.exe" |
    Where-Object Name -ne "tesseract.exe" |
    Remove-Item -Force

if (-not (Test-Path -LiteralPath $GermanData) -or
    (Get-FileHash -LiteralPath $GermanData -Algorithm SHA256).Hash -ne $GermanDataSha256) {
    $germanDataDownload = Get-VerifiedDownload $GermanDataUrl $GermanDataSha256
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $GermanData) | Out-Null
    Copy-Item -LiteralPath $germanDataDownload -Destination $GermanData -Force
}
foreach ($languageFile in @($GermanData, $EnglishData, $OsdData)) {
    if (-not (Test-Path -LiteralPath $languageFile)) {
        throw "Sprachmodell wurde nicht gefunden: $languageFile"
    }
}

$versionOutput = (& $TesseractExe --version 2>&1)
if ($LASTEXITCODE -ne 0 -or
    ($versionOutput | Select-Object -First 1) -notmatch "^tesseract 5\.5\.2$" -or
    ($versionOutput -join "`n") -notmatch "leptonica-1\.87\.0") {
    throw "Tesseract 5.5.2 mit Leptonica 1.87.0 konnte nicht gestartet werden: $($versionOutput -join ' ')"
}
$languages = (& $TesseractExe --list-langs 2>&1)
foreach ($language in @("deu", "eng", "osd")) {
    if ($languages -notcontains $language) {
        throw "Tesseract findet das Sprachmodell '$language' nicht."
    }
}

if (-not $KeepDownloads) {
    Remove-Item -LiteralPath $ExtractionRoot -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $tesseractPackage, $leptonicaPackage, $gccPackage, $winPthreadsPackage `
        -Force -ErrorAction SilentlyContinue
}

Write-Host ($versionOutput -join [Environment]::NewLine)
Write-Host "Tesseract 5.5.2 mit Leptonica 1.87.0 ist vorbereitet. Der Release-Build nimmt diesen Ordner automatisch mit."
