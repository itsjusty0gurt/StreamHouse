$ErrorActionPreference = "Stop"

function Test-StreamhousePathWithin {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Root
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $fullRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    return $fullPath.StartsWith(
        $fullRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )
}

function Get-StreamhousePackageProcesses {
    param(
        [Parameter(Mandatory = $true)][string]$OutputPath
    )

    $fullOutput = [System.IO.Path]::GetFullPath($OutputPath).TrimEnd('\', '/')
    return @(
        Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $processPath = $null
            try {
                $processPath = $_.Path
            }
            catch {
                # Some protected system processes do not expose Path. They
                # cannot be safely identified as package owners here.
            }
            if (-not $processPath) {
                return $false
            }
            $fullProcessPath = [System.IO.Path]::GetFullPath($processPath)
            return $fullProcessPath.StartsWith(
                $fullOutput + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        } | Select-Object Id, ProcessName, Path
    )
}

function Assert-StreamhousePackageOutputIdle {
    param(
        [Parameter(Mandatory = $true)][string]$OutputPath,
        [Parameter(Mandatory = $true)][string]$ProductName
    )

    $owners = @(Get-StreamhousePackageProcesses -OutputPath $OutputPath)
    if ($owners.Count -eq 0) {
        return
    }

    $details = ($owners | ForEach-Object {
        "$($_.ProcessName) (PID $($_.Id), $($_.Path))"
    }) -join "; "
    throw "$ProductName is still running from the package output. Close it before building. Lock owner: $details"
}

function Invoke-StreamhousePackageOperation {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Operation,
        [Parameter(Mandatory = $true)][string]$Description,
        [ValidateRange(1, 100)][int]$Attempts = 12,
        [ValidateRange(0, 10000)][int]$DelayMilliseconds = 250
    )

    $lastError = $null
    for ($attempt = 1; $attempt -le $Attempts; $attempt++) {
        try {
            & $Operation
            return
        }
        catch {
            $lastError = $_
            if ($attempt -lt $Attempts -and $DelayMilliseconds -gt 0) {
                Start-Sleep -Milliseconds $DelayMilliseconds
            }
        }
    }

    throw "$Description failed after $Attempts attempt(s): $($lastError.Exception.Message)"
}

function Publish-StreamhousePackageOutput {
    param(
        [Parameter(Mandatory = $true)][string]$StagedBundle,
        [Parameter(Mandatory = $true)][string]$FinalBundle,
        [Parameter(Mandatory = $true)][string]$AllowedStagingRoot,
        [Parameter(Mandatory = $true)][string]$AllowedFinalRoot,
        [ValidateRange(1, 100)][int]$Attempts = 12,
        [ValidateRange(0, 10000)][int]$DelayMilliseconds = 250
    )

    if (-not (Test-StreamhousePathWithin -Path $StagedBundle -Root $AllowedStagingRoot)) {
        throw "Refusing to publish from outside the package staging root: $StagedBundle"
    }
    if (-not (Test-StreamhousePathWithin -Path $FinalBundle -Root $AllowedFinalRoot)) {
        throw "Refusing to replace output outside the distribution root: $FinalBundle"
    }
    if (-not (Test-Path -LiteralPath $StagedBundle -PathType Container)) {
        throw "Missing staged package: $StagedBundle"
    }

    $finalParent = Split-Path -Parent $FinalBundle
    New-Item -ItemType Directory -Force -Path $finalParent | Out-Null
    $backupBundle = Join-Path $finalParent (
        "." + (Split-Path -Leaf $FinalBundle) + ".previous." +
        [guid]::NewGuid().ToString("N")
    )
    $previousMoved = $false

    try {
        if (Test-Path -LiteralPath $FinalBundle) {
            Invoke-StreamhousePackageOperation `
                -Description "Replacing existing package output '$FinalBundle'" `
                -Attempts $Attempts `
                -DelayMilliseconds $DelayMilliseconds `
                -Operation {
                    Move-Item -LiteralPath $FinalBundle -Destination $backupBundle
                }
            $previousMoved = $true
        }

        try {
            Move-Item -LiteralPath $StagedBundle -Destination $FinalBundle
        }
        catch {
            if ($previousMoved -and -not (Test-Path -LiteralPath $FinalBundle)) {
                Move-Item -LiteralPath $backupBundle -Destination $FinalBundle
                $previousMoved = $false
            }
            throw
        }

        if ($previousMoved) {
            Invoke-StreamhousePackageOperation `
                -Description "Removing previous package output '$backupBundle'" `
                -Attempts $Attempts `
                -DelayMilliseconds $DelayMilliseconds `
                -Operation {
                    Remove-Item -LiteralPath $backupBundle -Recurse -Force
                }
            $previousMoved = $false
        }
    }
    catch {
        $owners = @(Get-StreamhousePackageProcesses -OutputPath $FinalBundle)
        if ($owners.Count -gt 0) {
            $details = ($owners | ForEach-Object {
                "$($_.ProcessName) (PID $($_.Id), $($_.Path))"
            }) -join "; "
            throw "$($_.Exception.Message) Lock owner: $details"
        }
        throw "$($_.Exception.Message) No process running from the package was found; check Explorer, antivirus, indexing, and other external Windows handles."
    }
}
