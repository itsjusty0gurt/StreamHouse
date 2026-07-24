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
        --name "SallyBot" `
        --icon "assets\sally-icon.ico" `
        --version-file "packaging\windows-version-info.txt" `
        --exclude-module "ai" `
        --exclude-module "sally_companion.server" `
        --add-binary ".venv\Lib\site-packages\PySide6\plugins\platforms\qoffscreen.dll;PySide6\plugins\platforms" `
        --add-data "assets;assets" `
        --add-data "twitch_extension;twitch_extension" `
        "main.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE."
    }

    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --name "SallyAICompanion" `
        --icon "assets\sally-icon.ico" `
        --version-file "packaging\windows-companion-version-info.txt" `
        --add-binary ".venv\Lib\site-packages\PySide6\plugins\platforms\qoffscreen.dll;PySide6\plugins\platforms" `
        --add-data "assets;assets" `
        "companion_main.py"
    if ($LASTEXITCODE -ne 0) {
        throw "AI Companion PyInstaller failed with exit code $LASTEXITCODE."
    }

    foreach ($bundle in @("SallyBot", "SallyAICompanion")) {
        $qtRoot = Join-Path $projectRoot "dist\$bundle\_internal\PySide6"
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
    }

    Write-Host "Windows builds created at dist\SallyBot\SallyBot.exe and dist\SallyAICompanion\SallyAICompanion.exe"
}
finally {
    Pop-Location
}
