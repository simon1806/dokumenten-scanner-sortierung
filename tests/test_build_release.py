from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BuildReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (PROJECT_ROOT / "scripts" / "build-release.ps1").read_text(encoding="utf-8-sig")

    def test_build_is_versioned_fail_closed_and_checks_native_commands(self) -> None:
        self.assertIn('[string]$Version = "0.2.3"', self.script)
        self.assertIn("function Invoke-PythonCommand", self.script)
        self.assertEqual(3, self.script.count('Invoke-PythonCommand $'))
        self.assertIn('Join-Path $ReleaseRoot $Version', self.script)
        self.assertNotIn("Reset-BuildDirectory $ReleaseRoot", self.script)
        self.assertNotIn("Reset-BuildDirectory $VersionRelease", self.script)

    def test_quality_gates_run_before_packaging(self) -> None:
        quality_gate = self.script.index("Invoke-QualityGates\n")
        reset_build = self.script.index("Reset-BuildDirectory $BuildRoot")
        pyinstaller_build = self.script.index('Invoke-PythonCommand $mainArguments')
        self.assertLess(quality_gate, reset_build)
        self.assertLess(quality_gate, pyinstaller_build)
        self.assertIn('"-m", "ruff", "check", "--no-cache"', self.script)
        self.assertIn('"-m", "unittest", "discover", "-s", "tests", "-v"', self.script)

    def test_dirty_source_is_blocked_including_untracked_files(self) -> None:
        self.assertIn("[switch]$AllowDirtySource", self.script)
        self.assertIn("status --porcelain=v1 --untracked-files=all", self.script)
        self.assertIn("if ($SourceDirty -and -not $AllowDirtySource)", self.script)
        self.assertIn("source_dirty = $SourceDirty", self.script)

    def test_existing_version_requires_force_and_publication_is_staged(self) -> None:
        self.assertIn("[switch]$ForceRebuild", self.script)
        self.assertIn("if ((Test-Path -LiteralPath $VersionRelease) -and -not $ForceRebuild)", self.script)
        self.assertIn("function Publish-ReleaseDirectory", self.script)
        self.assertIn('Join-Path $ReleaseRoot ".publishing-$Version-$publicationId"', self.script)
        self.assertIn("Move-Item -LiteralPath $staging -Destination $Destination", self.script)
        self.assertIn("Publish-ReleaseDirectory $readyDirectory $VersionRelease", self.script)

    def test_failed_force_publication_restores_and_verifies_previous_release(self) -> None:
        self.assertIn("function Get-ReleaseDirectoryManifest", self.script)
        self.assertIn("function Assert-ReleaseDirectoryManifest", self.script)
        self.assertIn(
            "Move-Item -LiteralPath $backup -Destination $Destination -ErrorAction Stop",
            self.script,
        )
        self.assertIn("Assert-ReleaseDirectoryManifest $Destination $oldManifest", self.script)
        self.assertIn("Backup=$backup; Ziel=$Destination", self.script)
        self.assertNotIn("PublishRootCompatibility", self.script)

    def test_built_executables_must_pass_bounded_self_tests(self) -> None:
        self.assertIn("function Invoke-ArtifactSelfTest", self.script)
        self.assertIn("New-Object System.Diagnostics.ProcessStartInfo", self.script)
        self.assertIn('$startInfo.Arguments = "--self-test"', self.script)
        self.assertIn("$startInfo.CreateNoWindow = $true", self.script)
        self.assertIn("$startInfo.RedirectStandardOutput = $true", self.script)
        self.assertIn("$startInfo.RedirectStandardError = $true", self.script)
        self.assertIn('$env:TEMP = $selfTestTemp', self.script)
        self.assertIn('$env:TMP = $selfTestTemp', self.script)
        self.assertIn('$env:TEMP = $previousTemp', self.script)
        self.assertIn('$env:TMP = $previousTmp', self.script)
        self.assertIn("WaitForExit($TimeoutSeconds * 1000)", self.script)
        self.assertIn("if ($process.ExitCode -ne 0)", self.script)
        self.assertEqual(3, self.script.count("Invoke-ArtifactSelfTest $"))

    def test_release_requires_and_validates_expected_ocr_runtime(self) -> None:
        self.assertIn('[switch]$WithoutBundledTesseract', self.script)
        self.assertIn('$ExpectedTesseractVersion = "5.5.2"', self.script)
        self.assertIn('$ExpectedLeptonicaVersion = "1.87.0"', self.script)
        self.assertIn('foreach ($language in @("deu", "eng", "osd"))', self.script)

    def test_release_contains_integrity_and_provenance_artifacts(self) -> None:
        for marker in (
            "payload-manifest.json",
            "RELEASE-MANIFEST.json",
            "SHA256SUMS.txt",
            "THIRD_PARTY_NOTICES.md",
            "--version-file",
            "source_commit",
        ):
            self.assertIn(marker, self.script)

    def test_release_requires_and_hashes_readme_and_changelog(self) -> None:
        for marker in (
            '$Readme = Join-Path $ProjectRoot "README.md"',
            '$Changelog = Join-Path $ProjectRoot "CHANGELOG.md"',
            'Copy-Item -LiteralPath $Readme',
            'Copy-Item -LiteralPath $Changelog',
            '"README.md",',
            '"CHANGELOG.md"',
        ):
            self.assertIn(marker, self.script)
        required_files = self.script[
            self.script.index("foreach ($requiredFile in @(") : self.script.index(
                ")) {", self.script.index("foreach ($requiredFile in @(")
            )
        ]
        self.assertIn("$Readme", required_files)
        self.assertIn("$Changelog", required_files)
        self.assertIn('$hashNames = @($releaseFiles + "RELEASE-MANIFEST.json")', self.script)

    def test_build_dependencies_are_exactly_pinned(self) -> None:
        constraints = (PROJECT_ROOT / "constraints-build.txt").read_text(encoding="utf-8")
        lock = (PROJECT_ROOT / "requirements-build.lock").read_text(encoding="utf-8")
        packages: list[str] = []
        for line in constraints.splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                self.assertIn("==", stripped)
                packages.append(stripped)
                self.assertIn(f"{stripped} \\", lock)
        self.assertIn("PyInstaller==6.21.0", constraints)
        self.assertIn("ruff==0.15.21", constraints)
        self.assertEqual(len(packages), lock.count("--hash=sha256:"))
        self.assertIn("$LockFile", self.script)
        self.assertIn("dependency_lock_sha256 = Get-Sha256 $LockFile", self.script)
        self.assertIn("native_runtime_version_output = @($NativeRuntimeVersionOutput)", self.script)

    def test_dependency_probe_is_compatible_with_windows_powershell_51(self) -> None:
        self.assertIn("foreach ($constraint in $constraintLines)", self.script)
        self.assertIn("$actualPackageVersion = Invoke-PythonCapture", self.script)
        self.assertIn("importlib.metadata.version(sys.argv[1])", self.script)
        self.assertNotIn("$constraintCheck = @'", self.script)

    def test_windows_ci_uses_the_build_scripts_expected_virtual_environment(self) -> None:
        workflow = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertEqual(workflow.count("python -m venv .venv"), 2)
        self.assertEqual(workflow.count('python-version: "3.12"'), 2)
        self.assertIn(r".\.venv\Scripts\python.exe -m unittest", workflow)
        self.assertIn(r".\.venv\Scripts\ruff.exe check", workflow)
        self.assertIn(r".\scripts\build-release.ps1 -Version 0.2.3", workflow)
        self.assertEqual(workflow.count("--require-hashes"), 2)
        self.assertEqual(
            workflow.count("actions/checkout@93cb6efe18208431cddfb8368fd83d5badbf9bfd"),
            2,
        )
        self.assertEqual(
            workflow.count("actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"),
            2,
        )
        self.assertEqual(
            workflow.count("actions/upload-artifact@330a01c490aca151604b8cf639adc76d48f6c5d4"),
            1,
        )
        self.assertNotRegex(workflow, r"uses:\s+[^\s]+@v\d+")


if __name__ == "__main__":
    unittest.main()
