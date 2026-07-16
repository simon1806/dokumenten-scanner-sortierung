param(
    [string]$Version = "0.1.25",
    [string]$TesseractDir = "",
    [switch]$WithoutBundledTesseract,
    [string]$SignToolPath = "",
    [string]$SigningCertificateThumbprint = "",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [switch]$AllowDirtySource,
    [switch]$ForceRebuild
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ArtifactsRoot = Join-Path $ProjectRoot ".artifacts"
$BuildRoot = Join-Path $ArtifactsRoot "release-build\$Version"
$MainDist = Join-Path $BuildRoot "main"
$SetupDist = Join-Path $BuildRoot "setup"
$WorkMain = Join-Path $BuildRoot "work-main"
$WorkSetup = Join-Path $BuildRoot "work-setup"
$SpecRoot = Join-Path $BuildRoot "spec"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$VersionRelease = Join-Path $ReleaseRoot $Version
$MainName = "DokumentenScannerSortierung"
$MainExecutable = Join-Path $MainDist "$MainName.exe"
$SetupExecutable = Join-Path $SetupDist "$MainName-Setup.exe"
$UninstallerSource = Join-Path $ProjectRoot "installer\uninstall.ps1"
$ProductSource = Join-Path $ProjectRoot "installer\product.py"
$IconAssets = Join-Path $ProjectRoot "src\scanner_sorter\assets\icons\tabler"
$AppAssets = Join-Path $ProjectRoot "src\scanner_sorter\assets\app"
$AppIcon = Join-Path $AppAssets "dokumenten-scanner-sortierung.ico"
$ThirdPartyNotices = Join-Path $ProjectRoot "THIRD_PARTY_NOTICES.md"
$Readme = Join-Path $ProjectRoot "README.md"
$Changelog = Join-Path $ProjectRoot "CHANGELOG.md"
$VersionPayload = Join-Path $BuildRoot "version.txt"
$PayloadManifest = Join-Path $BuildRoot "payload-manifest.json"
$MainVersionResource = Join-Path $BuildRoot "version-main.txt"
$SetupVersionResource = Join-Path $BuildRoot "version-setup.txt"
$ConstraintsFile = Join-Path $ProjectRoot "constraints-build.txt"
$LockFile = Join-Path $ProjectRoot "requirements-build.lock"
$ExpectedTesseractVersion = "5.5.2"
$ExpectedLeptonicaVersion = "1.87.0"

function Assert-ProjectChildPath([string]$Path) {
    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd("\")
    $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    if (-not $candidate.StartsWith($root + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsicherer Build-Pfad außerhalb des Projekts: $candidate"
    }
}

function Reset-BuildDirectory([string]$Path) {
    Assert-ProjectChildPath $Path
    if (Test-Path -LiteralPath $Path) {
        Remove-Item -LiteralPath $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Invoke-PythonCommand([string[]]$Arguments, [string]$Description) {
    & $Python @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description ist mit Exitcode $LASTEXITCODE fehlgeschlagen."
    }
}

function Invoke-PythonCapture([string[]]$Arguments, [string]$Description) {
    $output = & $Python @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$Description ist mit Exitcode $LASTEXITCODE fehlgeschlagen: $($output -join ' ')"
    }
    return ($output -join [Environment]::NewLine).Trim()
}

function Get-PythonStringConstant([string]$Path, [string]$Name) {
    $content = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $pattern = "(?m)^" + [regex]::Escape($Name) + '\s*=\s*"([^"]+)"\s*$'
    $match = [regex]::Match($content, $pattern)
    if (-not $match.Success) {
        throw "Konstante $Name wurde nicht in $Path gefunden."
    }
    return $match.Groups[1].Value
}

function ConvertTo-PythonLiteral([string]$Value) {
    return $Value.Replace("\", "\\").Replace("'", "\'")
}

function New-PyInstallerVersionFile(
    [string]$Path,
    [string]$OriginalFilename,
    [string]$FileDescription,
    [string]$CompanyName,
    [string]$ProductName,
    [int[]]$VersionParts
) {
    $tuple = "({0}, {1}, {2}, {3})" -f $VersionParts[0], $VersionParts[1], $VersionParts[2], $VersionParts[3]
    $company = ConvertTo-PythonLiteral $CompanyName
    $product = ConvertTo-PythonLiteral $ProductName
    $description = ConvertTo-PythonLiteral $FileDescription
    $original = ConvertTo-PythonLiteral $OriginalFilename
    $versionText = ConvertTo-PythonLiteral $Version
    $resource = @"
VSVersionInfo(
  ffi=FixedFileInfo(filevers=$tuple, prodvers=$tuple, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040704B0', [
      StringStruct('CompanyName', '$company'),
      StringStruct('FileDescription', '$description'),
      StringStruct('FileVersion', '$versionText'),
      StringStruct('InternalName', '$MainName'),
      StringStruct('OriginalFilename', '$original'),
      StringStruct('ProductName', '$product'),
      StringStruct('ProductVersion', '$versionText')
    ])]),
    VarFileInfo([VarStruct('Translation', [1031, 1200])])
  ]
)
"@
    [System.IO.File]::WriteAllText($Path, $resource, [System.Text.UTF8Encoding]::new($false))
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
}

function Assert-Artifact([string]$Path, [string]$ExpectedVersion) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Build-Artefakt fehlt: $Path"
    }
    $item = Get-Item -LiteralPath $Path
    if ($item.Length -lt 1MB) {
        throw "Build-Artefakt ist unerwartet klein: $Path ($($item.Length) Bytes)"
    }
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        if ($stream.ReadByte() -ne 0x4D -or $stream.ReadByte() -ne 0x5A) {
            throw "Build-Artefakt ist keine Windows-EXE: $Path"
        }
    } finally {
        $stream.Dispose()
    }
    $productVersion = $item.VersionInfo.ProductVersion
    if (-not $productVersion -or -not $productVersion.StartsWith($ExpectedVersion)) {
        throw "PE-Produktversion stimmt nicht: $Path meldet '$productVersion', erwartet '$ExpectedVersion'."
    }
}

function Sign-Artifact([string]$Path) {
    if (-not $SigningCertificateThumbprint) {
        return
    }
    & $SignToolPath sign /sha1 $SigningCertificateThumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Signierung ist mit Exitcode $LASTEXITCODE fehlgeschlagen: $Path"
    }
}

function Invoke-QualityGates {
    $previousPythonPath = $env:PYTHONPATH
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $env:PYTHONPATH = Join-Path $ProjectRoot "src"
        $env:PYTHONDONTWRITEBYTECODE = "1"
        Push-Location $ProjectRoot
        try {
            Invoke-PythonCommand @(
                "-m", "ruff", "check", "--no-cache", "src", "installer", "scripts", "tests"
            ) "Ruff-Pruefung"
            Invoke-PythonCommand @(
                "-m", "unittest", "discover", "-s", "tests", "-v"
            ) "Unit-Tests"
        } finally {
            Pop-Location
        }
    } finally {
        $env:PYTHONPATH = $previousPythonPath
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
    }
}

function Invoke-ArtifactSelfTest([string]$Path, [int]$TimeoutSeconds = 120) {
    $name = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $stdoutPath = Join-Path $BuildRoot "$name-self-test.stdout.log"
    $stderrPath = Join-Path $BuildRoot "$name-self-test.stderr.log"
    $selfTestTemp = Join-Path $BuildRoot "self-test-temp-$name-$([guid]::NewGuid().ToString('N'))"
    Assert-ProjectChildPath $selfTestTemp
    New-Item -ItemType Directory -Path $selfTestTemp | Out-Null
    $previousTemp = $env:TEMP
    $previousTmp = $env:TMP
    # Start-Process in Windows PowerShell 5.1 can fail before launch when the
    # inherited environment contains differently-cased duplicates such as
    # Path/PATH. ProcessStartInfo inherits that environment without rebuilding
    # it as a case-insensitive PowerShell dictionary.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $Path
    $startInfo.Arguments = "--self-test"
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    $stdoutTask = $null
    $stderrTask = $null
    try {
        # Keep one-file extraction in the controlled build tree. This also makes
        # the self-test independent of service-account TEMP permissions.
        $env:TEMP = $selfTestTemp
        $env:TMP = $selfTestTemp
        if (-not $process.Start()) {
            throw "Selbsttest-Prozess konnte nicht gestartet werden: $Path"
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        $timedOut = -not $process.WaitForExit($TimeoutSeconds * 1000)
        if ($timedOut) {
            try {
                $process.Kill()
            } catch {
                Write-Warning "Selbsttest-Prozess konnte nach Timeout nicht beendet werden: $Path; $_"
            }
            [void]$process.WaitForExit(10000)
        }
        $process.WaitForExit()
        $stdout = $stdoutTask.GetAwaiter().GetResult()
        $stderr = $stderrTask.GetAwaiter().GetResult()
        [System.IO.File]::WriteAllText($stdoutPath, $stdout, [System.Text.UTF8Encoding]::new($false))
        [System.IO.File]::WriteAllText($stderrPath, $stderr, [System.Text.UTF8Encoding]::new($false))
        if ($timedOut) {
            throw "Selbsttest hat das Zeitlimit von $TimeoutSeconds Sekunden ueberschritten: $Path"
        }
        if ($process.ExitCode -ne 0) {
            throw (
                "Selbsttest ist mit Exitcode $($process.ExitCode) fehlgeschlagen: $Path" +
                "`nSTDOUT: $($stdout.Trim())`nSTDERR: $($stderr.Trim())"
            )
        }
    } finally {
        $process.Dispose()
        $env:TEMP = $previousTemp
        $env:TMP = $previousTmp
        if (Test-Path -LiteralPath $selfTestTemp) {
            try {
                Remove-Item -LiteralPath $selfTestTemp -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Warning "Temporärer Selbsttest-Ordner konnte nicht entfernt werden: $selfTestTemp; $_"
            }
        }
    }
}

function Get-ReleaseDirectoryManifest([string]$Path) {
    $directories = @(Get-ChildItem -LiteralPath $Path -Directory -Force)
    if ($directories.Count -gt 0) {
        throw "Release-Ordner enthaelt unerwartete Unterordner: $Path"
    }
    $manifest = [ordered]@{}
    foreach ($file in @(Get-ChildItem -LiteralPath $Path -File -Force | Sort-Object Name)) {
        $manifest[$file.Name] = [ordered]@{
            length = $file.Length
            sha256 = Get-Sha256 $file.FullName
        }
    }
    return $manifest
}

function Assert-ReleaseDirectoryManifest(
    [string]$Path,
    [System.Collections.IDictionary]$Expected
) {
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "Release-Wiederherstellungsziel fehlt: $Path"
    }
    $actual = Get-ReleaseDirectoryManifest $Path
    if ($actual.Count -ne $Expected.Count) {
        throw "Wiederhergestelltes Release ist unvollstaendig: $($actual.Count) statt $($Expected.Count) Dateien."
    }
    foreach ($entry in $Expected.GetEnumerator()) {
        if (-not $actual.Contains($entry.Key) -or
            $actual[$entry.Key].length -ne $entry.Value.length -or
            $actual[$entry.Key].sha256 -ne $entry.Value.sha256) {
            throw "Wiederhergestelltes Release weicht ab: $($entry.Key)"
        }
    }
}

function Publish-ReleaseDirectory([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) {
        if (-not $ForceRebuild) {
            throw "Release existiert bereits: $Destination. Zum bewussten Ersetzen -ForceRebuild verwenden."
        }
    }

    New-Item -ItemType Directory -Force -Path $ReleaseRoot | Out-Null
    $publicationId = [guid]::NewGuid().ToString("N")
    $staging = Join-Path $ReleaseRoot ".publishing-$Version-$publicationId"
    $backup = Join-Path $ReleaseRoot ".replaced-$Version-$publicationId"
    $oldMoved = $false
    $newPublished = $false
    $oldManifest = $null
    try {
        New-Item -ItemType Directory -Path $staging | Out-Null
        Get-ChildItem -LiteralPath $Source -File | Copy-Item -Destination $staging -ErrorAction Stop

        $sourceFiles = @(Get-ChildItem -LiteralPath $Source -File | Sort-Object Name)
        $stagedFiles = @(Get-ChildItem -LiteralPath $staging -File | Sort-Object Name)
        if ($sourceFiles.Count -ne $stagedFiles.Count) {
            throw "Release-Staging ist unvollstaendig: $($stagedFiles.Count) statt $($sourceFiles.Count) Dateien."
        }
        foreach ($sourceFile in $sourceFiles) {
            $stagedFile = Join-Path $staging $sourceFile.Name
            if (-not (Test-Path -LiteralPath $stagedFile -PathType Leaf) -or
                (Get-Sha256 $sourceFile.FullName) -ne (Get-Sha256 $stagedFile)) {
                throw "Release-Staging weicht von der vorbereiteten Datei ab: $($sourceFile.Name)"
            }
        }

        if (Test-Path -LiteralPath $Destination) {
            $oldManifest = Get-ReleaseDirectoryManifest $Destination
            Move-Item -LiteralPath $Destination -Destination $backup -ErrorAction Stop
            $oldMoved = $true
        }
        Move-Item -LiteralPath $staging -Destination $Destination -ErrorAction Stop
        $newPublished = $true
    } catch {
        $publicationError = $_
        if (-not $newPublished -and $oldMoved -and -not (Test-Path -LiteralPath $Destination)) {
            try {
                Move-Item -LiteralPath $backup -Destination $Destination -ErrorAction Stop
                Assert-ReleaseDirectoryManifest $Destination $oldManifest
            } catch {
                throw (
                    "Release-Veröffentlichung fehlgeschlagen: $($publicationError.Exception.Message). " +
                    "Auch die terminierende Wiederherstellung des vorherigen Releases ist fehlgeschlagen. " +
                    "Vorheriges Release prüfen: Ziel=$Destination; Backup=$backup; " +
                    "Restore-Fehler=$($_.Exception.Message)"
                )
            }
        } elseif (-not $newPublished -and $oldMoved) {
            throw (
                "Release-Veröffentlichung fehlgeschlagen und das Ziel wurde zwischenzeitlich neu belegt. " +
                "Das vorherige Release wurde nicht überschrieben und bleibt zur manuellen Wiederherstellung erhalten: " +
                "Backup=$backup; Ziel=$Destination; Ursache=$($publicationError.Exception.Message)"
            )
        }
        throw $publicationError
    } finally {
        if (Test-Path -LiteralPath $staging) {
            try {
                Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction Stop
            } catch {
                Write-Warning "Release-Staging konnte nicht entfernt werden und bleibt zur Prüfung erhalten: $staging; $_"
            }
        }
    }
    if ($newPublished -and $oldMoved -and (Test-Path -LiteralPath $backup)) {
        try {
            Remove-Item -LiteralPath $backup -Recurse -Force -ErrorAction Stop
        } catch {
            Write-Warning "Ersetztes Alt-Release konnte nicht entfernt werden und bleibt erhalten: $backup; $_"
        }
    }
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Virtuelle Umgebung nicht gefunden: $Python"
}
foreach ($requiredFile in @(
    $ProductSource,
    $UninstallerSource,
    $AppIcon,
    $ThirdPartyNotices,
    $Readme,
    $Changelog,
    $ConstraintsFile,
    $LockFile
)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Erforderliche Build-Datei fehlt: $requiredFile"
    }
}
if ($Version -notmatch '^\d+(?:\.\d+){1,3}$') {
    throw "Ungültige Version: $Version"
}
if ([bool]$SignToolPath -ne [bool]$SigningCertificateThumbprint) {
    throw "Für die optionale Signierung müssen SignToolPath und SigningCertificateThumbprint gemeinsam angegeben werden."
}
if ($SignToolPath -and -not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) {
    throw "SignTool wurde nicht gefunden: $SignToolPath"
}
if ((Test-Path -LiteralPath $VersionRelease) -and -not $ForceRebuild) {
    throw "Release existiert bereits: $VersionRelease. Zum bewussten Ersetzen -ForceRebuild verwenden."
}

$pyproject = [System.IO.File]::ReadAllText((Join-Path $ProjectRoot "pyproject.toml"), [System.Text.Encoding]::UTF8)
$pyprojectMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"\s*$')
$initSource = [System.IO.File]::ReadAllText(
    (Join-Path $ProjectRoot "src\scanner_sorter\__init__.py"),
    [System.Text.Encoding]::UTF8
)
$initMatch = [regex]::Match($initSource, '(?m)^__version__\s*=\s*"([^"]+)"\s*$')
if (-not $pyprojectMatch.Success -or -not $initMatch.Success) {
    throw "Anwendungsversion konnte nicht aus den Quellen gelesen werden."
}
if ($pyprojectMatch.Groups[1].Value -ne $Version -or $initMatch.Groups[1].Value -ne $Version) {
    throw "Versionskonflikt: Build=$Version, pyproject=$($pyprojectMatch.Groups[1].Value), Anwendung=$($initMatch.Groups[1].Value)"
}

$commitOutput = & git -C $ProjectRoot rev-parse HEAD 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Git-Commit konnte nicht ermittelt werden: $($commitOutput -join ' ')"
}
$sourceCommit = ($commitOutput -join "").Trim()
$dirtyOutput = & git -C $ProjectRoot status --porcelain=v1 --untracked-files=all 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Git-Status konnte nicht ermittelt werden: $($dirtyOutput -join ' ')"
}
$dirtyLines = @($dirtyOutput | Where-Object { $_ -and $_.ToString().Trim() })
$SourceDirty = [bool]($dirtyLines.Count -gt 0)
if ($SourceDirty -and -not $AllowDirtySource) {
    throw "Arbeitsverzeichnis enthaelt versionierte oder unversionierte Aenderungen. Fuer einen bewussten Entwicklungs-Build -AllowDirtySource verwenden.`n$($dirtyLines -join [Environment]::NewLine)"
}

$constraintLines = @(
    [System.IO.File]::ReadAllLines($ConstraintsFile, [System.Text.Encoding]::UTF8) |
        ForEach-Object { $_.Trim() } |
        Where-Object { $_ -and -not $_.StartsWith("#") }
)
foreach ($constraint in $constraintLines) {
    if ($constraint -notmatch '^([^=]+)==([^=]+)$') {
        throw "Ungültige fixierte Build-Abhängigkeit: $constraint"
    }
    $packageName = $Matches[1].Trim()
    $expectedPackageVersion = $Matches[2].Trim()
    # Windows PowerShell 5.1 does not reliably preserve newlines in native
    # command arguments. Keep this probe deliberately on one line.
    $actualPackageVersion = Invoke-PythonCapture @(
        "-c",
        "import importlib.metadata, sys; print(importlib.metadata.version(sys.argv[1]))",
        $packageName
    ) "Prüfung der fixierten Build-Abhängigkeit $packageName"
    if ($actualPackageVersion -ne $expectedPackageVersion) {
        throw (
            "Build-Abhängigkeit stimmt nicht: $packageName=$actualPackageVersion, " +
            "erwartet $expectedPackageVersion"
        )
    }
}
$lockText = [System.IO.File]::ReadAllText($LockFile, [System.Text.Encoding]::UTF8)
foreach ($constraint in $constraintLines) {
    if ($lockText -notmatch ('(?im)^' + [regex]::Escape($constraint) + '\s*\\\s*$')) {
        throw "Fixierte Abhängigkeit fehlt in der Hash-Lockdatei: $constraint"
    }
}
$lockHashCount = [regex]::Matches($lockText, '(?im)^\s*--hash=sha256:[0-9a-f]{64}\s*$').Count
if ($lockHashCount -ne $constraintLines.Count) {
    throw "Hash-Lockdatei enthält $lockHashCount Hashes für $($constraintLines.Count) fixierte Abhängigkeiten."
}
Invoke-PythonCommand @("-m", "pip", "check") "pip check"
Invoke-QualityGates

$BasePython = Invoke-PythonCapture @("-c", "import sys; print(sys.base_prefix)") "Ermittlung der Python-Laufzeit"
$PythonVersion = Invoke-PythonCapture @("-c", "import platform; print(platform.python_version())") "Ermittlung der Python-Version"
$PyInstallerVersion = Invoke-PythonCapture @(
    "-c",
    "import importlib.metadata; print(importlib.metadata.version('PyInstaller'))"
) "Ermittlung der PyInstaller-Version"
$TclRoot = Join-Path $BasePython "tcl"
$DllRoot = Join-Path $BasePython "DLLs"
$TkinterPackage = Join-Path $BasePython "Lib\tkinter"
$TkinterHook = Join-Path $PSScriptRoot "pyinstaller_tkinter_hook.py"
$DefaultTesseractDir = Join-Path $ProjectRoot "vendor\Tesseract-OCR"

$TesseractArgs = @()
$DetectedTesseractVersion = "nicht mitgeliefert"
$DetectedLeptonicaVersion = "nicht mitgeliefert"
$NativeRuntimeVersionOutput = @()
if ($WithoutBundledTesseract) {
    if ($TesseractDir) {
        throw "TesseractDir und WithoutBundledTesseract dürfen nicht gemeinsam verwendet werden."
    }
    Write-Warning "Es wird ausdrücklich ein Build ohne eingebettetes Tesseract erzeugt."
} else {
    if (-not $TesseractDir) {
        $TesseractDir = $DefaultTesseractDir
    }
    if (-not (Test-Path -LiteralPath $TesseractDir -PathType Container)) {
        throw "Tesseract-Vendorordner fehlt: $TesseractDir. Vorbereitung ausführen oder -WithoutBundledTesseract bewusst setzen."
    }
    $ResolvedTesseractDir = (Resolve-Path -LiteralPath $TesseractDir).Path
    $TesseractExe = Join-Path $ResolvedTesseractDir "tesseract.exe"
    if (-not (Test-Path -LiteralPath $TesseractExe -PathType Leaf)) {
        throw "tesseract.exe nicht gefunden in: $ResolvedTesseractDir"
    }
    $versionOutput = & $TesseractExe --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Tesseract-Versionsprüfung ist mit Exitcode $LASTEXITCODE fehlgeschlagen."
    }
    $versionText = $versionOutput -join "`n"
    $NativeRuntimeVersionOutput = @($versionOutput | ForEach-Object { $_.ToString() })
    $tesseractMatch = [regex]::Match($versionText, '(?m)^tesseract\s+v?([^\s]+)')
    $leptonicaMatch = [regex]::Match($versionText, '(?m)^\s*leptonica[-\s]+([^\s]+)')
    if (-not $tesseractMatch.Success -or -not $leptonicaMatch.Success) {
        throw "Tesseract-/Leptonica-Version konnte nicht gelesen werden: $versionText"
    }
    $DetectedTesseractVersion = $tesseractMatch.Groups[1].Value
    $DetectedLeptonicaVersion = $leptonicaMatch.Groups[1].Value
    if ($DetectedTesseractVersion -ne $ExpectedTesseractVersion -or
        $DetectedLeptonicaVersion -ne $ExpectedLeptonicaVersion) {
        throw "Falsche OCR-Laufzeit: Tesseract $DetectedTesseractVersion / Leptonica $DetectedLeptonicaVersion; erwartet $ExpectedTesseractVersion / $ExpectedLeptonicaVersion."
    }
    $languages = & $TesseractExe --list-langs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Tesseract-Sprachprüfung ist mit Exitcode $LASTEXITCODE fehlgeschlagen."
    }
    foreach ($language in @("deu", "eng", "osd")) {
        if ($languages -notcontains $language -or
            -not (Test-Path -LiteralPath (Join-Path $ResolvedTesseractDir "tessdata\$language.traineddata"))) {
            throw "Tesseract-Sprachmodell fehlt oder wird nicht erkannt: $language"
        }
    }
    $TesseractArgs = @("--add-data", "$ResolvedTesseractDir;Tesseract-OCR")
    Write-Host "Geprüfte OCR-Laufzeit: Tesseract $DetectedTesseractVersion / Leptonica $DetectedLeptonicaVersion"
}

Reset-BuildDirectory $BuildRoot
New-Item -ItemType Directory -Force -Path $MainDist, $SetupDist, $SpecRoot | Out-Null
[System.IO.File]::WriteAllText($VersionPayload, "$Version`n", [System.Text.UTF8Encoding]::new($false))

$displayName = Get-PythonStringConstant $ProductSource "DISPLAY_NAME"
$publisher = Get-PythonStringConstant $ProductSource "PUBLISHER"
$versionParts = @($Version.Split(".") | ForEach-Object { [int]$_ })
while ($versionParts.Count -lt 4) {
    $versionParts += 0
}
New-PyInstallerVersionFile $MainVersionResource "$MainName.exe" $displayName $publisher $displayName $versionParts
New-PyInstallerVersionFile $SetupVersionResource "$MainName-Setup.exe" "$displayName Setup" $publisher $displayName $versionParts

$mainArguments = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", $MainName,
    "--icon", $AppIcon,
    "--version-file", $MainVersionResource,
    "--paths", (Join-Path $ProjectRoot "src"),
    "--hidden-import", "fitz",
    "--hidden-import", "zxingcpp",
    "--hidden-import", "pytesseract",
    "--hidden-import", "pystray",
    "--hidden-import", "pystray._win32",
    "--hidden-import", "tkinter",
    "--hidden-import", "_tkinter",
    "--collect-all", "fitz",
    "--collect-all", "zxingcpp",
    "--collect-all", "pystray",
    "--copy-metadata", "Pillow",
    "--copy-metadata", "PyMuPDF",
    "--copy-metadata", "pypdf",
    "--copy-metadata", "pytesseract",
    "--copy-metadata", "pystray",
    "--copy-metadata", "zxing-cpp",
    "--add-binary", "$(Join-Path $DllRoot '_tkinter.pyd');.",
    "--add-binary", "$(Join-Path $DllRoot 'tcl86t.dll');.",
    "--add-binary", "$(Join-Path $DllRoot 'tk86t.dll');.",
    "--add-data", "$TkinterPackage;tkinter",
    "--add-data", "$(Join-Path $TclRoot 'tcl8.6');_tcl_data",
    "--add-data", "$(Join-Path $TclRoot 'tk8.6');_tk_data",
    "--add-data", "$IconAssets;scanner_sorter/assets/icons/tabler",
    "--add-data", "$AppAssets;scanner_sorter/assets/app"
)
$mainArguments += $TesseractArgs
$mainArguments += @(
    "--runtime-hook", $TkinterHook,
    "--distpath", $MainDist,
    "--workpath", $WorkMain,
    "--specpath", $SpecRoot,
    (Join-Path $ProjectRoot "src\main.py")
)
Invoke-PythonCommand $mainArguments "Anwendungs-Build"
Assert-Artifact $MainExecutable $Version
Sign-Artifact $MainExecutable
Invoke-ArtifactSelfTest $MainExecutable

$payloadSources = [ordered]@{
    "$MainName.exe" = $MainExecutable
    "THIRD_PARTY_NOTICES.md" = $ThirdPartyNotices
    "dokumenten-scanner-sortierung.ico" = $AppIcon
    "version.txt" = $VersionPayload
    "uninstall.ps1" = $UninstallerSource
}
$payloadEntries = [ordered]@{}
foreach ($entry in $payloadSources.GetEnumerator()) {
    $item = Get-Item -LiteralPath $entry.Value
    $payloadEntries[$entry.Key] = [ordered]@{
        size = $item.Length
        sha256 = Get-Sha256 $item.FullName
    }
}
$payloadManifestObject = [ordered]@{
    schema = 1
    version = $Version
    files = $payloadEntries
}
[System.IO.File]::WriteAllText(
    $PayloadManifest,
    ($payloadManifestObject | ConvertTo-Json -Depth 5),
    [System.Text.UTF8Encoding]::new($false)
)

$setupArguments = @(
    "-m", "PyInstaller",
    "--noconfirm", "--clean", "--onefile", "--windowed",
    "--name", "$MainName-Setup",
    "--icon", $AppIcon,
    "--version-file", $SetupVersionResource,
    "--paths", $ProjectRoot,
    "--add-data", "$MainExecutable;payload",
    "--add-data", "$UninstallerSource;payload",
    "--add-data", "$ThirdPartyNotices;payload",
    "--add-data", "$AppIcon;payload",
    "--add-data", "$VersionPayload;payload",
    "--add-data", "$PayloadManifest;payload",
    "--distpath", $SetupDist,
    "--workpath", $WorkSetup,
    "--specpath", $SpecRoot,
    (Join-Path $ProjectRoot "installer\installer.py")
)
Invoke-PythonCommand $setupArguments "Setup-Build"
Assert-Artifact $SetupExecutable $Version
Sign-Artifact $SetupExecutable
Invoke-ArtifactSelfTest $SetupExecutable

$readyDirectory = Join-Path $BuildRoot "ready"
New-Item -ItemType Directory -Force -Path $readyDirectory | Out-Null
$portableName = "$MainName-$Version.exe"
$setupName = "$MainName-Setup.exe"
Copy-Item -LiteralPath $MainExecutable -Destination (Join-Path $readyDirectory $portableName)
Copy-Item -LiteralPath $SetupExecutable -Destination (Join-Path $readyDirectory $setupName)
Copy-Item -LiteralPath $ThirdPartyNotices -Destination (Join-Path $readyDirectory "THIRD_PARTY_NOTICES.md")
Copy-Item -LiteralPath $Readme -Destination (Join-Path $readyDirectory "README.md")
Copy-Item -LiteralPath $Changelog -Destination (Join-Path $readyDirectory "CHANGELOG.md")

$releaseFiles = @(
    $portableName,
    $setupName,
    "THIRD_PARTY_NOTICES.md",
    "README.md",
    "CHANGELOG.md"
)
$fileManifest = foreach ($name in $releaseFiles) {
    $path = Join-Path $readyDirectory $name
    $item = Get-Item -LiteralPath $path
    [ordered]@{ name = $name; size = $item.Length; sha256 = Get-Sha256 $path }
}
$releaseManifestObject = [ordered]@{
    schema = 1
    product = $displayName
    version = $Version
    source_commit = $sourceCommit
    source_dirty = $SourceDirty
    build_timestamp_utc = [DateTime]::UtcNow.ToString("o")
    python = $PythonVersion
    pyinstaller = $PyInstallerVersion
    dependency_lock_sha256 = Get-Sha256 $LockFile
    bundled_tesseract = -not $WithoutBundledTesseract
    tesseract = $DetectedTesseractVersion
    leptonica = $DetectedLeptonicaVersion
    native_runtime_version_output = @($NativeRuntimeVersionOutput)
    signed = [bool]$SigningCertificateThumbprint
    files = @($fileManifest)
}
$releaseManifestPath = Join-Path $readyDirectory "RELEASE-MANIFEST.json"
[System.IO.File]::WriteAllText(
    $releaseManifestPath,
    ($releaseManifestObject | ConvertTo-Json -Depth 6),
    [System.Text.UTF8Encoding]::new($false)
)
$hashNames = @($releaseFiles + "RELEASE-MANIFEST.json") | Sort-Object
$hashLines = foreach ($name in $hashNames) {
    "$(Get-Sha256 (Join-Path $readyDirectory $name))  $name"
}
[System.IO.File]::WriteAllText(
    (Join-Path $readyDirectory "SHA256SUMS.txt"),
    (($hashLines -join "`n") + "`n"),
    [System.Text.UTF8Encoding]::new($false)
)

Publish-ReleaseDirectory $readyDirectory $VersionRelease

Write-Host "Release $Version erstellt: $VersionRelease"
Write-Host "SHA-256-Prüfsummen: $(Join-Path $VersionRelease 'SHA256SUMS.txt')"
