$ErrorActionPreference = "Stop"

$pluginSource = Join-Path $PSScriptRoot "StreamhouseHub"
$repositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repositoryRoot ".venv\Scripts\python.exe"
$outputRoot = Join-Path $repositoryRoot "build\touch-portal"
$distribution = Join-Path $outputRoot "package"
$pluginFolder = Join-Path $distribution "StreamhouseHub"

if (-not (Test-Path -LiteralPath $python)) {
    throw "The repository virtual environment is not available."
}

New-Item -ItemType Directory -Path $pluginFolder -Force | Out-Null
& $python -m PyInstaller --noconfirm --clean --onefile `
    --name streamhouse_touch_portal `
    --distpath $pluginFolder `
    --workpath (Join-Path $outputRoot "work") `
    --specpath $outputRoot `
    (Join-Path $pluginSource "plugin.py")
if ($LASTEXITCODE -ne 0) {
    throw "Touch Portal plugin executable build failed."
}

Copy-Item -LiteralPath (Join-Path $pluginSource "entry.tp") -Destination $pluginFolder
$zipPath = Join-Path $outputRoot "StreamhouseHub.zip"
$packagePath = Join-Path $outputRoot "StreamhouseHub.tpp"
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath }
if (Test-Path -LiteralPath $packagePath) { Remove-Item -LiteralPath $packagePath }
Compress-Archive -Path $pluginFolder -DestinationPath $zipPath
Move-Item -LiteralPath $zipPath -Destination $packagePath
Write-Output "Touch Portal plugin: $packagePath"
