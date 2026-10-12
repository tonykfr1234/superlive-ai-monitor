param(
    [string]$Root = "$env:USERPROFILE\Downloads\SuperLive_AI",
    [string]$Python = '',
    [switch]$InstallUpdateDependencies,
    [switch]$InstallDependencies,
    [switch]$ConfigureTorch
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptMap = [ordered]@{
    'monitor.py' = 'stage4b_motion_monitor.py'
    'watchdog.py' = 'stage5_watchdog.py'
    'recorder.py' = 'stage5c_recorder.py'
    'recorder_watchdog.py' = 'stage5c_recorder_watchdog.py'
    'record_protocol.py' = 'record_protocol.py'
    'region_policy.py' = 'region_policy.py'
    'person_activity.py' = 'person_activity.py'
    'telegram_video.py' = 'telegram_video.py'
    'preview_ui.py' = 'preview_ui.py'
    'capture_region.py' = 'capture_region.py'
}
$Scripts = @($ScriptMap.Values)
$Root = [IO.Path]::GetFullPath($Root).TrimEnd('\')

# Validate the complete package and JSON before touching the installed version.
foreach ($Name in $ScriptMap.Keys) {
    if (-not (Test-Path -LiteralPath "$Repo\src\$Name" -PathType Leaf)) { throw "Package file missing: $Name" }
    if ($Python -and (Test-Path -LiteralPath $Python)) {
        & $Python -c "import pathlib,sys; p=pathlib.Path(sys.argv[1]); compile(p.read_text(encoding='utf-8-sig'), str(p), 'exec')" "$Repo\src\$Name"
        if ($LASTEXITCODE -ne 0) { throw "Package Python syntax error: $Name. No installed files were modified." }
    }
}
$Policy = Get-Content -LiteralPath "$Repo\config\notification_policy.example.json" -Raw | ConvertFrom-Json
$Recording = Get-Content -LiteralPath "$Repo\config\recording_config.example.json" -Raw | ConvertFrom-Json
if (-not (Test-Path -LiteralPath "$Repo\config\telegram.env.example" -PathType Leaf)) {
    throw 'Telegram configuration example is missing. No installed files were modified.'
}
if (Test-Path -LiteralPath "$Root\config\notification_policy.json") {
    $ExistingPolicy = Get-Content -LiteralPath "$Root\config\notification_policy.json" -Raw | ConvertFrom-Json
    foreach ($Name in @('roi', 'roi_coordinate_space', 'roi_reselect_required', 'target_fps', 'confidence', 'imgsz', 'capture_max_failures', 'capture_retry_seconds')) {
        if ($null -ne $ExistingPolicy.PSObject.Properties[$Name]) {
            $Policy | Add-Member -MemberType NoteProperty -Name $Name -Value $ExistingPolicy.$Name -Force
        }
    }
    # Preserve the absence of a coordinate marker on a legacy rectangle so
    # the monitor can perform its one-time conversion after content cropping.
    if ($null -ne $ExistingPolicy.roi -and $null -eq $ExistingPolicy.PSObject.Properties['roi_coordinate_space']) {
        $Policy.PSObject.Properties.Remove('roi_coordinate_space')
        $Policy.PSObject.Properties.Remove('roi_reselect_required')
    }
}
$Policy.watch_classes = @(0)
if (Test-Path -LiteralPath "$Root\config\recording_config.json") {
    $ExistingRecording = Get-Content -LiteralPath "$Root\config\recording_config.json" -Raw | ConvertFrom-Json
    foreach ($Property in $ExistingRecording.PSObject.Properties) {
        $Recording | Add-Member -MemberType NoteProperty -Name $Property.Name -Value $Property.Value -Force
    }
}
$Recording.segment_minutes = 5
if ([string]::IsNullOrWhiteSpace($Recording.record_root)) { throw 'Recording directory is missing in recording_config.json' }

# Keep a local rollback copy, including existing credentials without reading them.
if (Test-Path -LiteralPath $Root) {
    $Backup = Join-Path $Root ('backups\event_recording_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 6))
    New-Item -ItemType Directory -Force -Path $Backup | Out-Null
    foreach ($Name in @('scripts', 'config', 'Start_AI_Monitor.bat', 'Stop_AI_Monitor.bat')) {
        $Source = Join-Path $Root $Name
        if (Test-Path -LiteralPath $Source) { Copy-Item -LiteralPath $Source -Destination $Backup -Recurse -Force }
    }
    Write-Host "BACKUP=$Backup"
}

foreach ($Dir in @('scripts', 'config', 'runtime', 'models', 'events', 'logs\Stage5', 'logs\Stage5C')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $Root $Dir) | Out-Null
}
Set-Content -LiteralPath "$Root\runtime\stop.flag" -Value 'STOP' -Encoding ASCII

function Get-InstalledSuperLiveProcesses {
    $ScriptPatterns = $Scripts | ForEach-Object { '(?i)(?:^|["\s])' + [regex]::Escape((Join-Path "$Root\scripts" $_)) + '(?=["\s]|$)' }
    @(Get-CimInstance Win32_Process -ErrorAction Stop | Where-Object {
        $Process = $_
        if ($Process.Name -notin @('python.exe', 'pythonw.exe')) { return $false }
        foreach ($Pattern in $ScriptPatterns) {
            if ($Process.CommandLine -match $Pattern) { return $true }
        }
        return $false
    })
}

# Never replace files underneath a live process or kill unrelated Python work.
$Deadline = (Get-Date).AddSeconds(120)
do {
    $Running = @(Get-InstalledSuperLiveProcesses)
    if ($Running.Count -eq 0) { break }
    if ((Get-Date) -ge $Deadline) {
        throw 'SuperLive did not stop within 120 seconds. No production files were replaced. Close its windows and rerun the update.'
    }
    Start-Sleep -Seconds 2
} while ($true)
Write-Host 'SUPERLIVE_STOPPED=PASS'

if ($InstallDependencies) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Python 3.12 was not found.' }
    $VenvPython = Join-Path $Root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        & $Python -m venv "$Root\.venv"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Python environment.' }
    }
    $Python = $VenvPython
    & $Python -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw 'Unable to update the Python package tools.' }
    & $Python -m pip install -r "$Repo\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. No production files were replaced.' }
    if ($ConfigureTorch) {
        $UseCuda = $false
        if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
            & $Python -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cu126
            if ($LASTEXITCODE -eq 0) {
                $Cuda = & $Python -c "import torch; print(torch.cuda.is_available())" 2>$null
                $UseCuda = $LASTEXITCODE -eq 0 -and "$Cuda" -match 'True'
            }
        }
        if (-not $UseCuda) {
            & $Python -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/cpu
            if ($LASTEXITCODE -ne 0) { throw 'PyTorch CPU installation failed.' }
        }
    }
}

if ($InstallUpdateDependencies) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'Existing Python venv was not found. Use installer\INSTALL_FROM_REPO.bat for a new installation.' }
    & $Python -m pip install 'imageio-ffmpeg>=0.6.0'
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install the video encoder dependency. No production files were replaced.' }
    & $Python -c "import imageio_ffmpeg; print('VIDEO_ENCODER=' + imageio_ffmpeg.get_ffmpeg_exe())"
    if ($LASTEXITCODE -ne 0) { throw 'Video encoder dependency check failed.' }
}

foreach ($Name in $ScriptMap.Keys) { Copy-Item -LiteralPath "$Repo\src\$Name" -Destination (Join-Path "$Root\scripts" $ScriptMap[$Name]) -Force }
$Policy | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath "$Root\config\notification_policy.json" -Encoding UTF8
$Recording | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath "$Root\config\recording_config.json" -Encoding UTF8
if (-not (Test-Path -LiteralPath "$Root\config\telegram.env")) {
    Copy-Item -LiteralPath "$Repo\config\telegram.env.example" -Destination "$Root\config\telegram.env"
    Write-Host 'TELEGRAM_CONFIG=TEMPLATE_INSTALLED'
} else { Write-Host 'TELEGRAM_CONFIG=PRESERVED' }
New-Item -ItemType Directory -Force -Path $Recording.record_root | Out-Null

@'
@echo off
setlocal
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "AIWD=%ROOT%\scripts\stage5_watchdog.py"
set "RECWD=%ROOT%\scripts\stage5c_recorder_watchdog.py"
set "RUNTIME=%ROOT%\runtime"
if not exist "%PY%" (echo [FAIL] Python venv missing & pause & exit /b 1)
if not exist "%AIWD%" (echo [FAIL] AI watchdog missing & pause & exit /b 1)
if not exist "%RECWD%" (echo [FAIL] Recorder watchdog missing & pause & exit /b 1)
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
if exist "%RUNTIME%\stop.flag" del /f /q "%RUNTIME%\stop.flag"
start "SuperLive Recorder Watchdog" /min "%PY%" "%RECWD%"
timeout /t 2 /nobreak >nul
start "SuperLive AI Watchdog" /min "%PY%" "%AIWD%"
echo Person activity starts recording with a local Windows alert and snapshot. No Telegram start photo.
echo Normal clips last 120 to 300 seconds. Detection continues throughout recording.
echo First 120 seconds: no automatic rollover and no new Telegram sends, including old videos and retries.
echo Movement at or after 120 seconds and before 300 seconds immediately starts a new clip and quiet period.
echo No movement for 120 continuous seconds ends recording; wait for renewed movement or a new arrival.
echo Telegram automatically sends completed videos only, after the applicable quiet periods.
echo Repeated rollovers can delay queued videos. Already-dispatched network requests may still arrive.
echo Manual stop or capture failure may save a shorter local clip; no padding or delivery before its quiet deadline.
echo Preview controls: R = select polygon, C = full frame, Q = stop.
echo Click at least 3 vertices, Enter = save, right-click or Backspace = undo, Esc = cancel.
echo SuperLive AI START requested.
exit /b 0
'@ | Set-Content -LiteralPath "$Root\Start_AI_Monitor.bat" -Encoding ASCII

@'
@echo off
setlocal
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "RUNTIME=%ROOT%\runtime"
if not exist "%RUNTIME%" mkdir "%RUNTIME%" >nul 2>&1
> "%RUNTIME%\stop.flag" echo STOP
echo Stop requested. The active video will be finalized before exit.
echo Partial footage is saved at its actual length. Pending Telegram deliveries resume after restart when quiet periods allow.
exit /b 0
'@ | Set-Content -LiteralPath "$Root\Stop_AI_Monitor.bat" -Encoding ASCII

if ($Python -and (Test-Path -LiteralPath $Python)) {
    foreach ($Name in $Scripts) {
        & $Python -m py_compile "$Root\scripts\$Name"
        if ($LASTEXITCODE -ne 0) { throw "Compile failed: $Name. The backup is available at $Backup" }
    }
}
Write-Host "RECORD_ROOT=$($Recording.record_root)"
Write-Host 'SEGMENT_MINUTES=5'
Write-Host 'RECORDING_MODE=NORMAL_MIN_120_MAX_300_SECONDS_MOVEMENT_ROLLOVER'
Write-Host 'ROLLOVER=MOVEMENT_AT_LEAST_120_SECONDS_AND_BEFORE_300_SECONDS'
Write-Host 'INACTIVITY_SECONDS=120'
Write-Host 'TELEGRAM_AUTO=COMPLETED_VIDEOS_ONLY'
Write-Host 'TELEGRAM_QUIET_SECONDS_PER_CLIP=120_INCLUDES_BACKLOG_AND_RETRIES'
Write-Host 'REARM=PERSON_MOVEMENT_OR_NEW_ARRIVAL'
Write-Host 'PERSON_ONLY=YES'
Write-Host 'ROI=EXISTING_SELECTION_PRESERVED_OR_FULL_FRAME'
Write-Host 'EVENT_FILES_DEPLOYED=PASS'
