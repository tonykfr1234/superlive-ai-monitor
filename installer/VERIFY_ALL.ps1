$ErrorActionPreference='Continue'
$Kit=Split-Path -Parent $MyInvocation.MyCommand.Path
$Root="$env:USERPROFILE\Downloads\SuperLive_AI"
$Py="$Root\.venv\Scripts\python.exe"
$TS=Get-Date -Format 'yyyyMMdd_HHmmss'
$Dir="$Root\logs\Universal_Verify"
New-Item -ItemType Directory -Force -Path $Dir|Out-Null
$Log="$Dir\Universal_Verify_$TS.log"
Start-Transcript -Path $Log -Force

Write-Host '============================================================'
Write-Host ' SuperLive AI - UNIVERSAL VERIFY'
Write-Host '============================================================'
Write-Host "User=$env:USERNAME"
Write-Host "Root=$Root"

Write-Host "`n=== PC / VM ==="
Get-CimInstance Win32_ComputerSystem|Select Manufacturer,Model|Format-List
Get-CimInstance Win32_Processor|Select Name,NumberOfCores,NumberOfLogicalProcessors,VirtualizationFirmwareEnabled,SecondLevelAddressTranslationExtensions|Format-List
if(Get-Command quser.exe -ErrorAction SilentlyContinue){ & quser.exe } else { Write-Host 'SESSION_QUERY=UNAVAILABLE' }

Write-Host "`n=== GPU / Torch ==="
Get-CimInstance Win32_VideoController|Select Name,DriverVersion|Format-List
if(Get-Command nvidia-smi -ErrorAction SilentlyContinue){nvidia-smi}else{Write-Host 'NVIDIA_SMI=NOT_FOUND - CPU fallback is normal in VMware'}
if(Test-Path $Py){
  & $Py --version
  & $Py -c "import torch,cv2,mss,ultralytics;print('torch=',torch.__version__);print('cuda=',torch.cuda.is_available());print('device=',torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU');print('opencv=',cv2.__version__);print('ultralytics=',ultralytics.__version__)"
}else{Write-Host 'VENV=FAIL'}

Write-Host "`n=== Production Files ==="
foreach($F in @('scripts\stage4b_motion_monitor.py','scripts\stage5_watchdog.py','scripts\stage5c_recorder.py','scripts\stage5c_recorder_watchdog.py','scripts\record_protocol.py','scripts\region_policy.py','scripts\person_activity.py','scripts\telegram_video.py','scripts\preview_ui.py','scripts\capture_region.py','config\notification_policy.json','config\recording_config.json','Start_AI_Monitor.bat','Stop_AI_Monitor.bat')){
  if(Test-Path "$Root\$F"){Write-Host "PASS $F"}else{Write-Host "FAIL $F"}
}

Write-Host "`n=== Notification Policy ==="
if(Test-Path "$Root\config\notification_policy.json"){
  $N=Get-Content "$Root\config\notification_policy.json" -Raw|ConvertFrom-Json
  Write-Host 'PERSON_ONLY=YES'
  Write-Host 'INACTIVITY_SECONDS=120'
  Write-Host 'NORMAL_CLIP_SECONDS=120_TO_300'
  Write-Host 'ROLLOVER=MOVEMENT_AT_LEAST_120_SECONDS_AND_BEFORE_300_SECONDS'
  Write-Host 'TELEGRAM_AUTO=COMPLETED_VIDEOS_AND_DAILY_TRIGGER_CSV'
  Write-Host 'TELEGRAM_QUIET_SECONDS_PER_CLIP=120_INCLUDES_BACKLOG_AND_RETRIES'
  Write-Host 'REARM=PERSON_MOVEMENT_OR_NEW_ARRIVAL'
  Write-Host 'PREVIEW_CONTROLS=R polygon; click 3+ vertices; Enter save; right-click / Backspace undo; Esc cancel; C full frame; Q stop'
  Write-Host ("ROI=" + ($N.roi | ConvertTo-Json -Depth 10 -Compress))
  Write-Host "ROI_COORDINATE_SPACE=$($N.roi_coordinate_space)"
}
if(Test-Path "$Root\config\telegram.env"){Write-Host 'TELEGRAM_CONFIG=PASS'}else{Write-Host 'TELEGRAM_CONFIG=NOT_CONFIGURED'}

Write-Host "`n=== C Recorder ==="
$D=Get-PSDrive C
$Total=$D.Used+$D.Free
$UsedPct=if($Total -gt 0){($D.Used/$Total)*100}else{0}
Write-Host "C_TOTAL_GB=$([math]::Round($Total/1GB,2))"
Write-Host "C_FREE_GB=$([math]::Round($D.Free/1GB,2))"
Write-Host "C_USED_PERCENT=$([math]::Round($UsedPct,2))"
if(Test-Path "$Root\config\recording_config.json"){
  $R=Get-Content "$Root\config\recording_config.json" -Raw|ConvertFrom-Json
  Write-Host "RECORD_ROOT=$($R.record_root)"
  Write-Host "MAX_SEGMENT_MINUTES=$($R.segment_minutes)"
  if(Test-Path -LiteralPath $R.record_root){Write-Host 'RECORD_FOLDER=PASS'}else{Write-Host 'RECORD_FOLDER=FAIL'}
  Write-Host "ROTATION_START=$($R.rotation_start_used_percent)"
  Write-Host "ROTATION_STOP=$($R.rotation_stop_used_percent)"
}

Write-Host "`n=== MuMu / MEmu / Capture ==="
$M=Get-Process -ErrorAction SilentlyContinue|Where-Object{$_.ProcessName -match 'MuMu|Nemu|MEmu'}
if($M){
  $M|Select ProcessName,Id,Path|Format-Table -AutoSize
  Write-Host 'MUMU_PROCESS=PASS'
  if(Test-Path $Py){& $Py "$Kit\vm_capture_probe.py"}
}else{
  Write-Host 'MUMU_PROCESS=NOT_RUNNING'
  Write-Host 'ACTION=Install/start MuMu or MEmu, login SuperLive, open live camera, rerun VERIFY_ALL.bat'
}

Write-Host "`n=== Compile ==="
if(Test-Path $Py){
  foreach($F in @('stage4b_motion_monitor.py','stage5_watchdog.py','stage5c_recorder.py','stage5c_recorder_watchdog.py','record_protocol.py','region_policy.py','person_activity.py','telegram_video.py','preview_ui.py','capture_region.py')){
    & $Py -m py_compile "$Root\scripts\$F"
    if($LASTEXITCODE -eq 0){Write-Host "COMPILE_PASS=$F"}else{Write-Host "COMPILE_FAIL=$F"}
  }
}

if(Test-Path $Py){
  & $Py -c "import imageio_ffmpeg; print('VIDEO_ENCODER=' + imageio_ffmpeg.get_ffmpeg_exe())"
  if($LASTEXITCODE -ne 0){Write-Host 'VIDEO_ENCODER=FAIL'}
}
Write-Host 'UNIVERSAL_VERIFY=COMPLETE'
Stop-Transcript
explorer.exe /select,"$Log"
