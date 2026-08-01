$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "package_hub.ps1")
& (Join-Path $PSScriptRoot "package_ai.ps1")

Write-Host "Both independent Streamhouse release archives were created."
