$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$executables = @(
    (Join-Path $projectRoot "dist\StreamhouseHub\StreamhouseHub.exe"),
    (Join-Path $projectRoot "dist\StreamhouseAI\StreamhouseAI.exe")
)

foreach ($executable in $executables) {
    if (-not (Test-Path -LiteralPath $executable)) {
        throw "Missing packaged executable: $executable"
    }
}

$smokeData = Join-Path ([System.IO.Path]::GetTempPath()) (
    "Streamhouse-smoke-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Force -Path $smokeData | Out-Null

$oldDataDir = $env:STREAMHOUSE_DATA_DIR
$oldSmokeTest = $env:STREAMHOUSE_SMOKE_TEST
$oldPlatform = $env:QT_QPA_PLATFORM

try {
    $env:STREAMHOUSE_DATA_DIR = $smokeData
    $env:STREAMHOUSE_SMOKE_TEST = "1"
    $env:QT_QPA_PLATFORM = "offscreen"

    foreach ($executable in $executables) {
        $process = Start-Process -FilePath $executable -PassThru -Wait
        if ($process.ExitCode -ne 0) {
            throw "Packaged smoke test failed for $executable with code $($process.ExitCode)."
        }
    }
    Write-Host "Packaged smoke tests passed."
}
finally {
    $env:STREAMHOUSE_DATA_DIR = $oldDataDir
    $env:STREAMHOUSE_SMOKE_TEST = $oldSmokeTest
    $env:QT_QPA_PLATFORM = $oldPlatform
    if (Test-Path -LiteralPath $smokeData) {
        Remove-Item -LiteralPath $smokeData -Recurse -Force
    }
}
