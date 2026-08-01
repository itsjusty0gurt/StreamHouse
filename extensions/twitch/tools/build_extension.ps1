param(
    [Parameter(Mandatory = $true)]
    [string]$RelayUrl,
    [string]$Output = ".\dist\StreamhouseSoundboardExtension.zip"
)

$ErrorActionPreference = "Stop"
$extensionRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$repositoryRoot = Split-Path -Parent (Split-Path -Parent $extensionRoot)
$source = Join-Path $extensionRoot "app"
$staging = Join-Path ([System.IO.Path]::GetTempPath()) ("streamhouse-extension-" + [guid]::NewGuid())
$outputPath = [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $Output))

if (-not $RelayUrl.StartsWith("https://")) {
    throw "RelayUrl must use HTTPS."
}

try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    foreach ($name in @("viewer.html", "viewer.css", "viewer.js", "config.js", "config.html")) {
        Copy-Item -LiteralPath (Join-Path $source $name) -Destination $staging
    }
    Copy-Item -LiteralPath (Join-Path $source "viewer.html") -Destination (Join-Path $staging "panel.html")
    Copy-Item -LiteralPath (Join-Path $source "viewer.html") -Destination (Join-Path $staging "mobile.html")
    $configPath = Join-Path $staging "config.js"
    $escapedUrl = $RelayUrl.TrimEnd("/").Replace("\", "\\").Replace('"', '\"')
    Set-Content -LiteralPath $configPath -Encoding UTF8 -Value "window.STREAMHOUSE_RELAY_BASE = `"$escapedUrl`";"
    $outputDirectory = Split-Path -Parent $outputPath
    New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
    Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $outputPath -Force
    Write-Output "Built Twitch Extension bundle: $outputPath"
}
finally {
    if (Test-Path -LiteralPath $staging) {
        Remove-Item -LiteralPath $staging -Recurse -Force
    }
}
