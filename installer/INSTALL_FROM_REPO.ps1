$ErrorActionPreference = "Stop"

$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Root = "$env:USERPROFILE\Downloads\SuperLive_AI"
$Py = $null

function Find-Python312 {
    $Candidates = @(
        "C:\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    foreach ($P in $Candidates) {
        if (Test-Path $P) {
            $V = & $P --version 2>&1
            if ("$V" -match 'Python 3\.12') { return $P }
        }
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $P = py -3.12 -c "import sys;print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $P) { return $P.Trim() }
    }
    return $null
}

Write-Host "=== SuperLive AI Open-Source Installer ==="
Write-Host "Repo=$Repo"
Write-Host "Target=$Root"

# Security gate before deployment.
$ScanPy = Find-Python312
if ($ScanPy) {
    & $ScanPy "$Repo\scripts\security_scan.py" $Repo
    if ($LASTEXITCODE -ne 0) { throw "Security scan failed" }
}

$Py = Find-Python312
if (-not $Py) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "Python 3.12 not found and winget is unavailable"
    }
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements
    $Py = Find-Python312
}
if (-not $Py) { throw "Python 3.12 installation failed" }

foreach ($D in @(
    $Root,"$Root\scripts","$Root\config","$Root\models","$Root\events",
    "$Root\runtime","$Root\logs\Stage5","$Root\logs\Stage5C","C:\SuperLive_Recordings"
)) { New-Item -ItemType Directory -Force -Path $D | Out-Null }

# Preserve local Telegram credentials during upgrades.
$Telegram = $null
if (Test-Path "$Root\config\telegram.env") {
    $Telegram = Get-Content "$Root\config\telegram.env" -Raw
}

Copy-Item "$Repo\src\monitor.py" "$Root\scripts\stage4b_motion_monitor.py" -Force
Copy-Item "$Repo\src\watchdog.py" "$Root\scripts\stage5_watchdog.py" -Force
Copy-Item "$Repo\src\recorder.py" "$Root\scripts\stage5c_recorder.py" -Force
Copy-Item "$Repo\src\recorder_watchdog.py" "$Root\scripts\stage5c_recorder_watchdog.py" -Force
Copy-Item "$Repo\config\notification_policy.example.json" "$Root\config\notification_policy.json" -Force
Copy-Item "$Repo\config\recording_config.example.json" "$Root\config\recording_config.json" -Force

if ($Telegram) {
    Set-Content "$Root\config\telegram.env" -Value $Telegram -Encoding UTF8
}

if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) {
    & $Py -m venv "$Root\.venv"
}
$VPy = "$Root\.venv\Scripts\python.exe"

& $VPy -m pip install --upgrade pip setuptools wheel
& $VPy -m pip install -r "$Repo\requirements.txt"

# Prefer CUDA only when a usable NVIDIA device exists in this Windows instance.
$UseCuda = $false
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    & $VPy -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu126
    $Cuda = & $VPy -c "import torch;print(torch.cuda.is_available())" 2>$null
    if ("$Cuda" -match 'True') { $UseCuda = $true }
}
if (-not $UseCuda) {
    & $VPy -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cpu
}

foreach ($F in @('stage4b_motion_monitor.py','stage5_watchdog.py','stage5c_recorder.py','stage5c_recorder_watchdog.py')) {
    & $VPy -m py_compile "$Root\scripts\$F"
    if ($LASTEXITCODE -ne 0) { throw "Compile failed: $F" }
}

@'
@echo off
setlocal
set "ROOT=%USERPROFILE%\Downloads\SuperLive_AI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "RUNTIME=%ROOT%\runtime"
if exist "%RUNTIME%\stop.flag" del /f /q "%RUNTIME%\stop.flag"
start "SuperLive AI Watchdog" /min "%PY%" "%ROOT%\scripts\stage5_watchdog.py"
timeout /t 2 /nobreak >nul
start "SuperLive Recorder Watchdog" /min "%PY%" "%ROOT%\scripts\stage5c_recorder_watchdog.py"
echo SuperLive AI START requested.
exit /b 0
'@ | Set-Content "$Root\Start_AI_Monitor.bat" -Encoding ASCII

@'
@echo off
setlocal
set "ROOT=%USERPROFILE%\Downloads\SuperLive_AI"
if not exist "%ROOT%\runtime" mkdir "%ROOT%\runtime" >nul 2>&1
> "%ROOT%\runtime\stop.flag" echo STOP
echo Stop request sent to AI + Recorder watchdogs.
timeout /t 8 /nobreak >nul
exit /b 0
'@ | Set-Content "$Root\Stop_AI_Monitor.bat" -Encoding ASCII

powercfg /change monitor-timeout-ac 0 | Out-Null
powercfg /change standby-timeout-ac 0 | Out-Null
powercfg /change hibernate-timeout-ac 0 | Out-Null

Write-Host "INSTALL=PASS"
Write-Host "Runtime=$Root"
Write-Host "Recording=C:\SuperLive_Recordings"
Write-Host "Telegram setup: installer\SET_TELEGRAM.bat"
