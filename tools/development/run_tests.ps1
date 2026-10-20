[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$runRoot = Join-Path $repoRoot ".test-tmp\run-$PID"
$pythonTemp = Join-Path $runRoot "python"
$pytestTemp = Join-Path $runRoot "pytest"
$pytestCache = Join-Path $repoRoot ".test-tmp\pytest-cache"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$python = if (Test-Path -LiteralPath $venvPython) {
    $venvPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

New-Item -ItemType Directory -Force -Path $pythonTemp, $pytestTemp, $pytestCache | Out-Null

$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$exitCode = 1

try {
    $env:TEMP = $pythonTemp
    $env:TMP = $pythonTemp
    Push-Location $repoRoot
    try {
        & $python -m pytest "--basetemp=$pytestTemp" -o "cache_dir=$pytestCache" @PytestArgs
        $exitCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
} finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    if (Test-Path -LiteralPath $runRoot) {
        Remove-Item -LiteralPath $runRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}

exit $exitCode
