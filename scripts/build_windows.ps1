$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Create .venv and install requirements-build.txt before packaging."
}

& $python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: .venv\Scripts\python.exe -m pip install -r requirements-build.txt"
}

Push-Location $projectRoot
try {
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name "SallyAI" `
        --icon "assets\sally-icon.ico" `
        --version-file "packaging\windows-version-info.txt" `
        --add-data "assets;assets" `
        "main.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    $qtRoot = Join-Path $projectRoot "dist\SallyAI\_internal\PySide6"
    $qtResources = Join-Path $qtRoot "resources"
    $qtQml = Join-Path $qtRoot "qml"
    $qtLocales = Join-Path $qtRoot "translations\qtwebengine_locales"

    if (Test-Path -LiteralPath $qtResources) {
        Get-ChildItem -LiteralPath $qtResources -File | Where-Object {
            $_.Name -like "*.debug.*"
        } | Remove-Item -Force
    }
    if (Test-Path -LiteralPath $qtQml) {
        Remove-Item -LiteralPath $qtQml -Recurse -Force
    }
    if (Test-Path -LiteralPath $qtLocales) {
        Get-ChildItem -LiteralPath $qtLocales -File | Where-Object {
            $_.Name -ne "en-US.pak"
        } | Remove-Item -Force
    }

    Write-Host "Windows build created at dist\SallyAI\SallyAI.exe"
}
finally {
    Pop-Location
}
