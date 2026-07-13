$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$distribution = Join-Path $projectRoot "dist\SallyAI"
$releaseDirectory = Join-Path $projectRoot "release"
$versionSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "config\version.py")
$versionMatch = [regex]::Match($versionSource, 'VERSION\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Could not read VERSION from config\version.py."
}
$version = $versionMatch.Groups[1].Value
$archive = Join-Path $releaseDirectory "SallyAI-$version-windows-x64.zip"
$checksum = "$archive.sha256"

if (-not (Test-Path -LiteralPath (Join-Path $distribution "SallyAI.exe"))) {
    throw "Build dist\SallyAI first with scripts\build_windows.ps1."
}

New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive
}
Compress-Archive -Path (Join-Path $distribution "*") -DestinationPath $archive
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
Set-Content -LiteralPath $checksum -Encoding ascii -Value "$hash  $(Split-Path -Leaf $archive)"
Write-Host "Release archive: $archive"
Write-Host "SHA-256: $hash"
