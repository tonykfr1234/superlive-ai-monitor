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
    'telegram_upload.py' = 'telegram_upload.py'
    'preview_ui.py' = 'preview_ui.py'
    'latest_vehicle.py' = 'latest_vehicle.py'
    'adb_discovery.py' = 'adb_discovery.py'
    'setup_wizard.py' = 'setup_wizard.py'
    'plate_corrections.py' = 'plate_corrections.py'
    'vehicle_registry.py' = 'vehicle_registry.py'
    'plate_review.py' = 'plate_review.py'
    'capture_region.py' = 'capture_region.py'
    'app_branding.py' = 'app_branding.py'
    'hq_video.py' = 'hq_video.py'
    'daily_report.py' = 'daily_report.py'
    'adb_capture.py' = 'adb_capture.py'
    'stream_health.py' = 'stream_health.py'
    'superlive_recovery.py' = 'superlive_recovery.py'
    'plate_recognition.py' = 'plate_recognition.py'
    'plate_history.py' = 'plate_history.py'
    'plate_replay.py' = 'plate_replay.py'
    'video_location.py' = 'video_location.py'
}
$Scripts = @($ScriptMap.Values)
if (-not (Test-Path -LiteralPath "$Repo\assets\superlive-ai.ico" -PathType Leaf)) { throw 'Application icon is missing.' }
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
foreach ($Name in @('capture_config', 'plate_allowlist')) {
    $null = Get-Content -LiteralPath "$Repo\config\$Name.example.json" -Raw | ConvertFrom-Json
}
if (-not (Test-Path -LiteralPath "$Repo\requirements-plates.txt" -PathType Leaf)) {
    throw 'Isolated plate OCR requirements are missing. No installed files were modified.'
}
if (-not (Test-Path -LiteralPath "$Repo\config\telegram.env.example" -PathType Leaf)) {
    throw 'Telegram configuration example is missing. No installed files were modified.'
}
if (Test-Path -LiteralPath "$Root\config\notification_policy.json") {
    $ExistingPolicy = Get-Content -LiteralPath "$Root\config\notification_policy.json" -Raw | ConvertFrom-Json
    foreach ($Name in @('roi', 'roi_coordinate_space', 'roi_reselect_required', 'target_fps', 'confidence', 'imgsz', 'capture_max_failures', 'capture_retry_seconds', 'plate_enabled')) {
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
$Policy.watch_classes = @(0, 2, 3, 5, 7)
if (Test-Path -LiteralPath "$Root\config\recording_config.json") {
    $ExistingRecording = Get-Content -LiteralPath "$Root\config\recording_config.json" -Raw | ConvertFrom-Json
    foreach ($Property in $ExistingRecording.PSObject.Properties) {
        $Recording | Add-Member -MemberType NoteProperty -Name $Property.Name -Value $Property.Value -Force
    }
} else {
    # New installations must work without elevation or a writable C:\ root.
    $Recording.record_root = Join-Path $Root 'recordings'
}
$Recording.segment_minutes = 5
if ([string]::IsNullOrWhiteSpace($Recording.record_root)) { throw 'Recording directory is missing in recording_config.json' }

# Keep a local rollback copy, including existing credentials without reading them.
if (Test-Path -LiteralPath $Root) {
    $Backup = Join-Path $Root ('backups\event_recording_' + (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0, 6))
    New-Item -ItemType Directory -Force -Path $Backup | Out-Null
    foreach ($Name in @('scripts', 'config', 'assets', 'docs', 'Start_AI_Monitor.bat', 'Stop_AI_Monitor.bat', 'Settings.bat')) {
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

if ($InstallDependencies -or $InstallUpdateDependencies) {
    # Headless OpenCV must never replace the GUI-enabled cv2 in the person venv.
    $PlatePython = Join-Path $Root '.plate-venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $PlatePython -PathType Leaf)) {
        & $Python -m venv "$Root\.plate-venv"
        if ($LASTEXITCODE -ne 0) { throw 'Unable to create the isolated plate OCR environment.' }
    }
    & $PlatePython -m pip install -r "$Repo\requirements-plates.txt"
    if ($LASTEXITCODE -ne 0) { throw 'Isolated plate OCR dependency installation failed. No production files were replaced.' }
    & $PlatePython -c "import fast_alpr, fast_plate_ocr, onnxruntime; print('PLATE_OCR_ENVIRONMENT=PASS')"
    if ($LASTEXITCODE -ne 0) { throw 'Isolated plate OCR dependency check failed.' }
}

foreach ($Name in $ScriptMap.Keys) { Copy-Item -LiteralPath "$Repo\src\$Name" -Destination (Join-Path "$Root\scripts" $ScriptMap[$Name]) -Force }
New-Item -ItemType Directory -Force -Path "$Root\assets" | Out-Null
Copy-Item -LiteralPath "$Repo\assets\superlive-ai.ico" -Destination "$Root\assets\superlive-ai.ico" -Force
New-Item -ItemType Directory -Force -Path "$Root\docs\media" | Out-Null
foreach ($Name in @('DASHBOARD.en.md', 'DASHBOARD.zh-TW.md', 'INSTALL.en.md', 'INSTALL.zh-TW.md', 'VEHICLE_DATABASE.en.md', 'VEHICLE_DATABASE.zh-TW.md', 'PLATE_ENHANCEMENT.zh-TW.md')) {
    Copy-Item -LiteralPath "$Repo\docs\$Name" -Destination "$Root\docs\$Name" -Force
}
foreach ($Name in @('dashboard-preview.png', 'dashboard-guide.en.png', 'dashboard-guide.zh-TW.png', 'dashboard-layout.gif')) {
    Copy-Item -LiteralPath "$Repo\docs\media\$Name" -Destination "$Root\docs\media\$Name" -Force
}
foreach ($Guide in Get-ChildItem -LiteralPath "$Repo\docs" -Filter '*.txt' -File) {
    Copy-Item -LiteralPath $Guide.FullName -Destination "$Root\docs" -Force
    if (-not (Test-Path -LiteralPath (Join-Path $Root $Guide.Name))) {
        Copy-Item -LiteralPath $Guide.FullName -Destination $Root
    }
}
$Policy | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath "$Root\config\notification_policy.json" -Encoding UTF8
$Recording | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath "$Root\config\recording_config.json" -Encoding UTF8
Copy-Item -LiteralPath "$Repo\requirements-plates.txt" -Destination "$Root\requirements-plates.txt" -Force
foreach ($Name in @('capture_config', 'plate_allowlist')) {
    if (-not (Test-Path -LiteralPath "$Root\config\$Name.json")) {
        Copy-Item -LiteralPath "$Repo\config\$Name.example.json" -Destination "$Root\config\$Name.json"
    }
}
if (-not (Test-Path -LiteralPath "$Root\config\telegram.env")) {
    Copy-Item -LiteralPath "$Repo\config\telegram.env.example" -Destination "$Root\config\telegram.env"
    Write-Host 'TELEGRAM_CONFIG=TEMPLATE_INSTALLED'
} else { Write-Host 'TELEGRAM_CONFIG=PRESERVED' }
New-Item -ItemType Directory -Force -Path $Recording.record_root | Out-Null

@'
@echo off
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\setup_wizard.py" --root "%~dp0."
if errorlevel 1 pause
'@ | Set-Content -LiteralPath "$Root\Settings.bat" -Encoding ASCII

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
echo Person or vehicle activity starts recording with a local Windows alert and snapshot. No Telegram start photo.
echo Normal clips last 120 to 300 seconds. Detection continues throughout recording.
echo First 120 seconds: no automatic rollover or new video uploads. Confirmed plates notify immediately.
echo Movement at or after 120 seconds and before 300 seconds immediately starts a new clip and quiet period.
echo No movement for 120 continuous seconds ends recording; wait for renewed movement or a new arrival.
echo Videos honor quiet periods. Daily 23:00 Taiwan-time CSV reports are exempt and cover 00:00-23:00 only.
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
Write-Host 'TELEGRAM_AUTO=COMPLETED_VIDEOS_AND_DAILY_TRIGGER_CSV'
Write-Host 'TELEGRAM_QUIET_SECONDS_PER_CLIP=120_INCLUDES_BACKLOG_AND_RETRIES'
Write-Host 'REARM=PERSON_OR_VEHICLE_MOVEMENT_OR_NEW_ARRIVAL'
Write-Host 'ACTIVITY_CLASSES=PERSON_CAR_MOTORCYCLE_BUS_TRUCK'
Write-Host 'ROI=EXISTING_SELECTION_PRESERVED_OR_FULL_FRAME'
Write-Host 'EVENT_FILES_DEPLOYED=PASS'
