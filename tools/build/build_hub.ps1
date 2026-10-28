$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$outputHelpers = Join-Path $PSScriptRoot "windows_package_output.ps1"
$distRoot = Join-Path $projectRoot "dist"
$finalBundle = Join-Path $distRoot "StreamhouseHub"
$stagingParent = Join-Path $projectRoot "build\package-staging"
$stagingRoot = Join-Path $stagingParent ("hub-" + [guid]::NewGuid().ToString("N"))
$stagedBundle = Join-Path $stagingRoot "StreamhouseHub"

. $outputHelpers

if (-not (Test-Path -LiteralPath $python)) {
    throw "Create .venv and install requirements-build.txt before packaging."
}

& $python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: .venv\Scripts\python.exe -m pip install -r requirements-build.txt"
}

Push-Location $projectRoot
try {
    # Fail before the expensive PyInstaller analysis when the packaged Hub (or
    # one of its bundled child processes) is still using the normal output.
    Assert-StreamhousePackageOutputIdle `
        -OutputPath $finalBundle `
        -ProductName "Streamhouse Hub"

    # Hub uses Qt Widgets and Qt WebEngine, not the Python Qt Quick/QML APIs.
    # Excluding them prevents PyInstaller from scanning incomplete, unused
    # QML plugin metadata shipped in the PySide6 wheel.
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $stagingRoot `
        --windowed `
        --name "StreamhouseHub" `
        --icon "shared\assets\streamhouse-icons\windows\streamhouse-hub.ico" `
        --version-file "tools\packaging\windows-hub-version-info.txt" `
        --exclude-module "products.ai.engine" `
        --exclude-module "products.ai.streamhouse_ai" `
        --exclude-module "PySide6.QtQuick" `
        --exclude-module "PySide6.QtQml" `
        --add-binary ".venv\Lib\site-packages\PySide6\plugins\platforms\qoffscreen.dll;PySide6\plugins\platforms" `
        --add-data "shared\assets\streamhouse-icons\streamhouse-hub.png;assets\streamhouse-icons" `
        --add-data "extensions\twitch\app;extensions\twitch\app" `
        "products\hub\hub_main.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Streamhouse Hub PyInstaller failed with exit code $LASTEXITCODE."
    }

    $qtRoot = Join-Path $stagedBundle "_internal\PySide6"
    $internalRoot = Join-Path $stagedBundle "_internal"
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

    # The Twitch Extension directory is included for its browser assets only.
    # Keep development Python sources/caches and listing material out of every
    # Hub artifact, including the ZIP release path as well as the installer.
    $extensionApp = Join-Path $internalRoot "extensions\twitch\app"
    if (Test-Path -LiteralPath $extensionApp) {
        Get-ChildItem -LiteralPath $extensionApp -Filter "*.py" -File | `
            Remove-Item -Force
        foreach ($relative in ("__pycache__", "listing", "README.md")) {
            $developmentItem = Join-Path $extensionApp $relative
            if (Test-Path -LiteralPath $developmentItem) {
                Remove-Item -LiteralPath $developmentItem -Recurse -Force
            }
        }
    }
    $stagedExecutable = Join-Path $stagedBundle "StreamhouseHub.exe"
    if (-not (Test-Path -LiteralPath $stagedExecutable -PathType Leaf)) {
        throw "PyInstaller did not produce the expected executable: $stagedExecutable"
    }

    # Build and prune a complete package before touching the prior output.
    # Publishing uses same-volume directory moves, bounded retries for short
    # Windows sharing violations, and rollback if the new move fails.
    Assert-StreamhousePackageOutputIdle `
        -OutputPath $finalBundle `
        -ProductName "Streamhouse Hub"
    Publish-StreamhousePackageOutput `
        -StagedBundle $stagedBundle `
        -FinalBundle $finalBundle `
        -AllowedStagingRoot $stagingParent `
        -AllowedFinalRoot $distRoot

    Write-Host "Streamhouse Hub created at dist\StreamhouseHub\StreamhouseHub.exe"
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
