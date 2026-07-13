$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$executable = Join-Path $projectRoot "dist\SallyAI\SallyAI.exe"

if (-not (Test-Path -LiteralPath $executable)) {
    throw "Build dist\SallyAI first with scripts\build_windows.ps1."
}

$smokeData = Join-Path ([System.IO.Path]::GetTempPath()) (
    "SallyAI-smoke-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Force -Path $smokeData | Out-Null

$oldDataDir = $env:SALLY_DATA_DIR
$oldSmokeTest = $env:SALLY_SMOKE_TEST
$oldPlatform = $env:QT_QPA_PLATFORM

try {
    $env:SALLY_DATA_DIR = $smokeData
    $env:SALLY_SMOKE_TEST = "1"
    $env:QT_QPA_PLATFORM = "offscreen"

    $process = Start-Process -FilePath $executable -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "Packaged smoke test exited with code $($process.ExitCode)."
    }
    Write-Host "Packaged smoke test passed."
}
finally {
    $env:SALLY_DATA_DIR = $oldDataDir
    $env:SALLY_SMOKE_TEST = $oldSmokeTest
    $env:QT_QPA_PLATFORM = $oldPlatform
    if (Test-Path -LiteralPath $smokeData) {
        Remove-Item -LiteralPath $smokeData -Recurse -Force
    }
}
