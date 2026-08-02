$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
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
        --name "StreamhouseAI" `
        --icon "shared\assets\streamhouse-icons\windows\streamhouse-ai.ico" `
        --version-file "tools\packaging\windows-ai-version-info.txt" `
        --exclude-module "products.hub" `
        --add-binary ".venv\Lib\site-packages\PySide6\plugins\platforms\qoffscreen.dll;PySide6\plugins\platforms" `
        --add-data "shared\assets\streamhouse-icons\streamhouse-ai.png;assets\streamhouse-icons" `
        "products\ai\ai_main.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Streamhouse AI PyInstaller failed with exit code $LASTEXITCODE."
    }

    $qtRoot = Join-Path $projectRoot "dist\StreamhouseAI\_internal\PySide6"
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
    Write-Host "Streamhouse AI created at dist\StreamhouseAI\StreamhouseAI.exe"
}
finally {
    Pop-Location
}
