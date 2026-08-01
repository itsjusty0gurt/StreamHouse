$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "build_hub.ps1")
& (Join-Path $PSScriptRoot "build_ai.ps1")

Write-Host "Both Streamhouse Windows applications were built independently."
