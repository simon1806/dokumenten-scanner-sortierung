param(
    [Parameter(Mandatory = $true)][string]$LeptonicaRoot,
    [Parameter(Mandatory = $true)][string]$RuntimeDirectory,
    [string]$SourceUrl = "https://github.com/tesseract-ocr/tesseract/archive/refs/tags/5.5.4.tar.gz",
    [string]$SourceSha256 = "A8DBF8B8C8EE427FF6AD1D99116502FEAF59440AF1ADB893CAF60A6576826CF4"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = [System.IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$WorkRoot = Join-Path $ProjectRoot ".artifacts\tesseract-5.5.4-update"
$Downloads = Join-Path $WorkRoot "downloads"
$Toolchain = Join-Path $WorkRoot "toolchain"
$ToolBin = Join-Path $Toolchain "mingw64\bin"
$SourceRoot = Join-Path $WorkRoot "tesseract-5.5.4"
$BuildRoot = Join-Path $WorkRoot "build"
$Candidate = Join-Path $WorkRoot "candidate"
$LockFile = Join-Path $PSScriptRoot "tesseract-build-tools.lock.json"

function Assert-ProjectChildPath([string]$Path) {
    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($ProjectRoot.TrimEnd("\") + "\",
            [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "OCR-Buildpfad muss innerhalb des Projekts liegen: $fullPath"
    }
    return $fullPath
}

$LeptonicaRoot = Assert-ProjectChildPath $LeptonicaRoot
$RuntimeDirectory = Assert-ProjectChildPath $RuntimeDirectory
New-Item -ItemType Directory -Force -Path $Downloads, $Toolchain | Out-Null

function Get-VerifiedBuildDownload([string]$Url, [string]$Filename, [string]$Sha256) {
    if ($Filename -ne [System.IO.Path]::GetFileName($Filename) -or
        $Sha256 -notmatch '^[a-fA-F0-9]{64}$') {
        throw "Ungueltiger Eintrag in der OCR-Buildwerkzeugliste."
    }
    $download = Join-Path $Downloads $Filename
    if (-not (Test-Path -LiteralPath $download) -or
        (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $Sha256) {
        Write-Host "Lade OCR-Builddatei: $Filename"
        Invoke-WebRequest -Uri $Url -OutFile $download
    }
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $Sha256) {
        throw "SHA-256-Pruefung der OCR-Builddatei fehlgeschlagen: $Filename"
    }
    return $download
}

$source = Get-VerifiedBuildDownload $SourceUrl "tesseract-5.5.4.tar.gz" $SourceSha256
# Always restore the pinned sources before configuring, even with cached downloads.
& tar -xf $source -C $WorkRoot
if ($LASTEXITCODE -ne 0) { throw "Tesseract-Quellcode konnte nicht entpackt werden." }
foreach ($package in (Get-Content -LiteralPath $LockFile -Raw | ConvertFrom-Json)) {
    $download = Get-VerifiedBuildDownload `
        ("https://repo.msys2.org/mingw/mingw64/" + $package.filename) `
        $package.filename $package.sha256
    & tar -xf $download -C $Toolchain
    if ($LASTEXITCODE -ne 0) { throw "OCR-Buildwerkzeug konnte nicht entpackt werden: $download" }
}

$cmake = Join-Path $ToolBin "cmake.exe"
$previousPath = $env:PATH
$previousTessdataPrefix = $env:TESSDATA_PREFIX
try {
    $env:PATH = "$ToolBin;$RuntimeDirectory;$previousPath"
    & $cmake -S $SourceRoot -B $BuildRoot -G Ninja `
        "-DCMAKE_BUILD_TYPE=Release" `
        "-DCMAKE_C_COMPILER=$ToolBin\gcc.exe" `
        "-DCMAKE_CXX_COMPILER=$ToolBin\g++.exe" `
        "-DCMAKE_MAKE_PROGRAM=$ToolBin\ninja.exe" `
        "-DLeptonica_DIR=$LeptonicaRoot\mingw64\lib\cmake\leptonica" `
        "-DOPENMP_BUILD=ON" "-DBUILD_TRAINING_TOOLS=OFF" "-DBUILD_TESTS=OFF" `
        "-DBUILD_SHARED_LIBS=OFF" "-DENABLE_NATIVE=OFF" `
        "-DDISABLE_CURL=ON" "-DDISABLE_ARCHIVE=ON" "-DDISABLE_TIFF=ON" `
        "-DENABLE_PRECOMPILED_HEADERS=OFF"
    if ($LASTEXITCODE -ne 0) { throw "Tesseract-CMake-Konfiguration fehlgeschlagen." }
    & $cmake --build $BuildRoot --parallel 4
    if ($LASTEXITCODE -ne 0) { throw "Tesseract-Quellbuild fehlgeschlagen." }

    # Validate a complete candidate with its own DLLs, without the compiler on PATH.
    $Candidate = Assert-ProjectChildPath $Candidate
    if (Test-Path -LiteralPath $Candidate) { Remove-Item -LiteralPath $Candidate -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $Candidate | Out-Null
    Copy-Item -Path (Join-Path $RuntimeDirectory "*") -Destination $Candidate -Recurse -Force
    Copy-Item -LiteralPath (Join-Path $BuildRoot "bin\tesseract.exe") -Destination $Candidate -Force
    $runtimeFiles = @("libatomic-1.dll", "libgcc_s_seh-1.dll", "libgomp-1.dll",
        "libquadmath-0.dll", "libstdc++-6.dll", "libwinpthread-1.dll")
    foreach ($filename in $runtimeFiles) {
        Copy-Item -LiteralPath (Join-Path $ToolBin $filename) -Destination $Candidate -Force
    }
    foreach ($obsoleteDll in @("libtesseract-5.dll", "libtesseract-5.5.dll")) {
        Remove-Item -LiteralPath (Join-Path $Candidate $obsoleteDll) -Force -ErrorAction SilentlyContinue
    }
    $licenseRoot = Join-Path $Candidate "licenses"
    New-Item -ItemType Directory -Force -Path (Join-Path $licenseRoot "tesseract-ocr") | Out-Null
    Copy-Item -LiteralPath (Join-Path $SourceRoot "LICENSE") `
        -Destination (Join-Path $licenseRoot "tesseract-ocr\LICENSE") -Force
    foreach ($name in @("libatomic", "libgcc", "libgomp", "libquadmath", "libstdc++", "libwinpthread")) {
        Copy-Item -LiteralPath (Join-Path $Toolchain "mingw64\share\licenses\$name") `
            -Destination $licenseRoot -Recurse -Force
    }
    $env:PATH = [Environment]::SystemDirectory
    $env:TESSDATA_PREFIX = Join-Path $Candidate "tessdata"
    $version = & (Join-Path $Candidate "tesseract.exe") --version 2>&1
    if ($LASTEXITCODE -ne 0 -or ($version | Select-Object -First 1) -ne "tesseract 5.5.4" -or
        ($version -join "`n") -notmatch "leptonica-1\.87\.0") {
        throw "Die eigenstaendige OCR-Kandidatenlaufzeit ist ungueltig: $version"
    }
    $languages = & (Join-Path $Candidate "tesseract.exe") --list-langs 2>&1
    if ($LASTEXITCODE -ne 0) { throw "OCR-Kandidatenlaufzeit kann keine Sprachmodelle lesen." }
    foreach ($language in @("deu", "eng", "osd")) {
        if ($languages -notcontains $language) { throw "OCR-Sprachmodell fehlt: $language" }
    }
    $env:PATH = "$ToolBin;$previousPath"
    $compilerVersion = & (Join-Path $ToolBin "gcc.exe") -dumpfullversion
    if ($LASTEXITCODE -ne 0) { throw "Compiler-Version konnte nicht gelesen werden." }
    $manifest = [ordered]@{
        tesseract = "5.5.4"
        leptonica = "1.87.0"
        source_url = $SourceUrl
        source_sha256 = $SourceSha256
        build_tools_lock_sha256 = (Get-FileHash -LiteralPath $LockFile -Algorithm SHA256).Hash
        runtime_lock_sha256 = (Get-FileHash -LiteralPath (Join-Path $PSScriptRoot "tesseract-runtime.lock.json") -Algorithm SHA256).Hash
        gcc = $compilerVersion
        openmp = $true
        native_cpu_optimization = $false
        curl = $false
        archive = $false
        tesseract_tiff_reader = $false
        tesseract_exe_sha256 = (Get-FileHash -LiteralPath (Join-Path $Candidate "tesseract.exe") -Algorithm SHA256).Hash
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Candidate "OCR-BUILD-MANIFEST.json") -Encoding UTF8
    # The caller installs this checked candidate. No application or setup is built here.
    Write-Output $Candidate
} finally {
    $env:PATH = $previousPath
    $env:TESSDATA_PREFIX = $previousTessdataPrefix
}
