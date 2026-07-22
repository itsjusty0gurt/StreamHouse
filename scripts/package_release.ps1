$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$releaseDirectory = Join-Path $projectRoot "release"
$versionSource = Get-Content -Raw -LiteralPath (Join-Path $projectRoot "config\version.py")
$versionMatch = [regex]::Match($versionSource, 'VERSION\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) {
    throw "Could not read VERSION from config\version.py."
}
$version = $versionMatch.Groups[1].Value

$packages = @(
    @{
        Name = "SallyBot"
        Directory = Join-Path $projectRoot "dist\SallyBot"
        Executable = "SallyBot.exe"
    },
    @{
        Name = "SallyAICompanion"
        Directory = Join-Path $projectRoot "dist\SallyAICompanion"
        Executable = "SallyAICompanion.exe"
    }
)

New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
foreach ($package in $packages) {
    $executable = Join-Path $package.Directory $package.Executable
    if (-not (Test-Path -LiteralPath $executable)) {
        throw "Missing build: $executable. Run scripts\build_windows.ps1 first."
    }
    $archive = Join-Path $releaseDirectory (
        "$($package.Name)-$version-windows-x64.zip"
    )
    $checksum = "$archive.sha256"
    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive
    }
    Compress-Archive -Path (Join-Path $package.Directory "*") -DestinationPath $archive
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $checksum -Encoding ascii -Value (
        "$hash  $(Split-Path -Leaf $archive)"
    )
    Write-Host "$($package.Name) archive: $archive"
    Write-Host "SHA-256: $hash"
}
