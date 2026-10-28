$helper = Join-Path (Split-Path -Parent $PSScriptRoot) "windows_package_output.ps1"
. $helper

Describe "Windows package output handling" {
    BeforeEach {
        $testRoot = Join-Path $env:STREAMHOUSE_TEST_TEMP (
            "package-output-" + [guid]::NewGuid().ToString("N")
        )
        $stagingRoot = Join-Path $testRoot "staging"
        $distRoot = Join-Path $testRoot "dist"
        New-Item -ItemType Directory -Force -Path $stagingRoot, $distRoot | Out-Null
    }

    AfterEach {
        if (Test-Path -LiteralPath $testRoot) {
            Remove-Item -LiteralPath $testRoot -Recurse -Force
        }
    }

    It "publishes a complete staged bundle over an existing output" {
        $stagedBundle = Join-Path $stagingRoot "StreamhouseHub"
        $finalBundle = Join-Path $distRoot "StreamhouseHub"
        New-Item -ItemType Directory -Path $stagedBundle, $finalBundle | Out-Null
        Set-Content -LiteralPath (Join-Path $stagedBundle "new.txt") -Value "new"
        Set-Content -LiteralPath (Join-Path $finalBundle "old.txt") -Value "old"

        Publish-StreamhousePackageOutput `
            -StagedBundle $stagedBundle `
            -FinalBundle $finalBundle `
            -AllowedStagingRoot $stagingRoot `
            -AllowedFinalRoot $distRoot `
            -DelayMilliseconds 0

        (Test-Path -LiteralPath (Join-Path $finalBundle "new.txt")) | Should Be $true
        (Test-Path -LiteralPath (Join-Path $finalBundle "old.txt")) | Should Be $false
        @(Get-ChildItem -LiteralPath $distRoot -Filter ".StreamhouseHub.previous.*").Count | Should Be 0
    }

    It "retries a transient sharing-style failure" {
        $script:attempt = 0
        Invoke-StreamhousePackageOperation `
            -Description "transient operation" `
            -Attempts 3 `
            -DelayMilliseconds 0 `
            -Operation {
                $script:attempt += 1
                if ($script:attempt -lt 3) {
                    throw "sharing violation"
                }
            }

        $script:attempt | Should Be 3
    }

    It "waits for a real transient Windows directory lock before publishing" {
        $stagedBundle = Join-Path $stagingRoot "StreamhouseHub"
        $finalBundle = Join-Path $distRoot "StreamhouseHub"
        New-Item -ItemType Directory -Path $stagedBundle, $finalBundle | Out-Null
        Set-Content -LiteralPath (Join-Path $stagedBundle "new.txt") -Value "new"
        Set-Content -LiteralPath (Join-Path $finalBundle "old.txt") -Value "old"
        $locker = Start-Process `
            -FilePath "powershell.exe" `
            -ArgumentList "-NoProfile", "-Command", "Start-Sleep -Milliseconds 600" `
            -WorkingDirectory $finalBundle `
            -WindowStyle Hidden `
            -PassThru

        try {
            Publish-StreamhousePackageOutput `
                -StagedBundle $stagedBundle `
                -FinalBundle $finalBundle `
                -AllowedStagingRoot $stagingRoot `
                -AllowedFinalRoot $distRoot `
                -Attempts 12 `
                -DelayMilliseconds 100
        }
        finally {
            $locker.WaitForExit()
            $locker.Dispose()
        }

        (Test-Path -LiteralPath (Join-Path $finalBundle "new.txt")) | Should Be $true
        (Test-Path -LiteralPath (Join-Path $finalBundle "old.txt")) | Should Be $false
    }

    It "reports a permanent operation failure after the bounded attempts" {
        {
            Invoke-StreamhousePackageOperation `
                -Description "permanent operation" `
                -Attempts 2 `
                -DelayMilliseconds 0 `
                -Operation { throw "access denied" }
        } | Should Throw "permanent operation failed after 2 attempt(s): access denied"
    }

    It "refuses a staged bundle outside the allowed staging root" {
        $outside = Join-Path $testRoot "outside\StreamhouseHub"
        $finalBundle = Join-Path $distRoot "StreamhouseHub"
        New-Item -ItemType Directory -Force -Path $outside | Out-Null

        {
            Publish-StreamhousePackageOutput `
                -StagedBundle $outside `
                -FinalBundle $finalBundle `
                -AllowedStagingRoot $stagingRoot `
                -AllowedFinalRoot $distRoot `
                -DelayMilliseconds 0
        } | Should Throw
    }

    It "detects the running process that owns an executable path" {
        $currentProcess = Get-Process -Id $PID
        $processDirectory = Split-Path -Parent $currentProcess.Path
        $owners = @(Get-StreamhousePackageProcesses -OutputPath $processDirectory)

        (@($owners | Select-Object -ExpandProperty Id) -contains $PID) | Should Be $true
    }
}
