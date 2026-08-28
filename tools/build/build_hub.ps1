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
        --name "StreamhouseHub" `
        --icon "shared\assets\streamhouse-icons\windows\streamhouse-hub.ico" `
        --version-file "tools\packaging\windows-hub-version-info.txt" `
        --exclude-module "products.ai.engine" `
        --exclude-module "products.ai.streamhouse_ai" `
        --add-binary ".venv\Lib\site-packages\PySide6\plugins\platforms\qoffscreen.dll;PySide6\plugins\platforms" `
        --add-data "shared\assets\streamhouse-icons\streamhouse-hub.png;assets\streamhouse-icons" `
        --add-data "extensions\twitch\app;extensions\twitch\app" `
        "products\hub\hub_main.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Streamhouse Hub PyInstaller failed with exit code $LASTEXITCODE."
    }

    $qtRoot = Join-Path $projectRoot "dist\StreamhouseHub\_internal\PySide6"
    $internalRoot = Join-Path $projectRoot "dist\StreamhouseHub\_internal"
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
    # PyInstaller may discover Poppler's versioned ICU implementation from the
    # build host PATH while resolving Qt6Core's Windows ICU dependency. That
    # DLL does not export the compatibility procedures Qt requests and makes
    # the packaged app fail while importing PySide6.QtCore. Windows supplies
    # the correct System32 compatibility DLL; do not shadow it in the package.
    $foreignIcu = Join-Path $internalRoot "icuuc.dll"
    if (Test-Path -LiteralPath $foreignIcu) {
        Remove-Item -LiteralPath $foreignIcu -Force
    }
    Get-ChildItem -LiteralPath $internalRoot -Filter "icudt*.dll" -File | `
        Remove-Item -Force
    Write-Host "Streamhouse Hub created at dist\StreamhouseHub\StreamhouseHub.exe"
}
finally {
    Pop-Location
}
