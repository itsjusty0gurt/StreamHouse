$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$releaseDirectory = Join-Path $projectRoot "release"
$versionSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "shared\streamhouse_runtime\version.py")
$versionMatch = [regex]::Match($versionSource, 'VERSION\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Could not read VERSION from shared\streamhouse_runtime\version.py."
}
$version = $versionMatch.Groups[1].Value
$bundle = Join-Path $projectRoot "dist\StreamhouseHub"
$executable = Join-Path $bundle "StreamhouseHub.exe"
if (-not (Test-Path -LiteralPath $executable)) {
    throw "Missing build: $executable. Run tools\build\build_hub.ps1 first."
}

New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
$archive = Join-Path $releaseDirectory "StreamhouseHub-$version-windows-x64.zip"
$checksum = "$archive.sha256"
if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive
}
Compress-Archive -Path (Join-Path $bundle "*") -DestinationPath $archive
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
Set-Content -LiteralPath $checksum -Encoding ascii -Value "$hash  $(Split-Path -Leaf $archive)"
Write-Host "Streamhouse Hub archive: $archive"
Write-Host "SHA-256: $hash"
