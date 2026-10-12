$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Root = "$env:USERPROFILE\Downloads\SuperLive_AI"

function Find-Python312 {
    foreach ($Candidate in @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe", 'C:\Python312\python.exe')) {
        if (Test-Path -LiteralPath $Candidate -PathType Leaf) {
            $Version = & $Candidate --version 2>&1
            if ("$Version" -match 'Python 3\.12') { return $Candidate }
        }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $Candidate = py -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $Candidate) { return $Candidate.Trim() }
    }
    return $null
}

Write-Host '=== SuperLive AI Open-Source Installer ==='
$Python = Find-Python312
if (-not $Python) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw 'Python 3.12 was not found and winget is unavailable. Install Python 3.12 and retry.'
    }
    & winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 installation failed.' }
    $Python = Find-Python312
}
if (-not $Python) { throw 'Python 3.12 was not found after installation.' }

& $Python "$Repo\scripts\security_scan.py" $Repo
if ($LASTEXITCODE -ne 0) { throw 'Repository security scan failed.' }

# Back up and stop the installed processes before changing files or dependencies.
& "$PSScriptRoot\DEPLOY_RUNTIME.ps1" -Root $Root -Python $Python -InstallDependencies -ConfigureTorch

powercfg /change monitor-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null

Write-Host 'INSTALL=PASS'
Write-Host "Runtime=$Root"
Write-Host 'Existing polygon, recording directory, storage thresholds and Telegram configuration were preserved.'
Write-Host 'Configure Telegram locally with installer\SET_TELEGRAM.bat.'
Write-Host 'Open MuMu / SuperLive live view, then run the deployed Start_AI_Monitor.bat.'
Write-Host 'No monitor was started and no Telegram test message was sent.'
