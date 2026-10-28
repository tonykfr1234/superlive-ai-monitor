param([string]$Python = (Get-Command python -ErrorAction Stop).Source)
$ErrorActionPreference = 'Stop'
$Kit = Split-Path -Parent $PSScriptRoot
$TestRoot = Join-Path (Join-Path ([IO.Path]::GetTempPath()) 'superlive-tests') ('deployment_test_' + [guid]::NewGuid().ToString('N'))
$InstallRoot = Join-Path $TestRoot 'installed'
$RecordingRoot = Join-Path $TestRoot 'recordings'
New-Item -ItemType Directory -Force -Path "$InstallRoot\config", "$InstallRoot\scripts" | Out-Null
# The deployment helper only sees this fixture, never the live installation.
@{roi=@(0.2,0.3,0.8,0.9); confidence=0.42; target_fps=7; moving_notify_seconds=300; plate_enabled=$false} | ConvertTo-Json | Set-Content "$InstallRoot\config\notification_policy.json" -Encoding UTF8
@{record_root=$RecordingRoot; target_fps=8; segment_minutes=30; rotation_start_used_percent=85; rotation_stop_used_percent=82; capture_max_failures=5; capture_retry_seconds=1; disk_check_seconds=60} | ConvertTo-Json | Set-Content "$InstallRoot\config\recording_config.json" -Encoding UTF8
Set-Content "$InstallRoot\config\telegram.env" -Value 'TEST_FIXTURE_NO_REAL_CREDENTIALS' -Encoding ASCII
Set-Content "$InstallRoot\scripts\stage4b_motion_monitor.py" -Value '# old fixture' -Encoding ASCII
$CredentialHash = (Get-FileHash "$InstallRoot\config\telegram.env").Hash
@{source='adb'; adb_path='D:/Emulator/adb.exe'; serial='fixture-device'; ensure_highest=$false; recovery=@{enabled=$true; device_name='Private fixture camera'; channel=7; stale_seconds=90; retry_seconds=45; frame_roi=@(0.1,0.2,0.9,0.8)}} | ConvertTo-Json -Depth 5 | Set-Content "$InstallRoot\config\capture_config.json" -Encoding UTF8
Set-Content "$InstallRoot\config\plate_allowlist.json" -Value '["TEST-42"]' -Encoding UTF8
$CaptureHash = (Get-FileHash "$InstallRoot\config\capture_config.json").Hash
$AllowlistHash = (Get-FileHash "$InstallRoot\config\plate_allowlist.json").Hash
# Isolate process discovery; the fixture has no running processes.
function Get-CimInstance { @() }
& "$Kit\installer\DEPLOY_RUNTIME.ps1" -Root $InstallRoot -Python $Python
$Policy = Get-Content "$InstallRoot\config\notification_policy.json" -Raw | ConvertFrom-Json
$Recording = Get-Content "$InstallRoot\config\recording_config.json" -Raw | ConvertFrom-Json
if (($Policy.roi -join ',') -ne '0.2,0.3,0.8,0.9' -or $Policy.confidence -ne 0.42) { throw 'ROI/confidence not preserved' }
if ($Policy.plate_enabled -ne $false) { throw 'User plate enablement preference not preserved' }
if ($null -ne $Policy.PSObject.Properties['roi_coordinate_space']) { throw 'Legacy ROI incorrectly marked as converted' }
if (($Policy.watch_classes -join ',') -ne '0,2,3,5,7' -or $null -ne $Policy.PSObject.Properties['moving_notify_seconds']) { throw 'Activity classes not migrated' }
if ($Recording.segment_minutes -ne 5 -or $Recording.record_root -ne $RecordingRoot -or $Recording.rotation_start_used_percent -ne 85 -or $Recording.rotation_stop_used_percent -ne 82) { throw 'Recording settings not migrated/preserved' }
if ((Get-FileHash "$InstallRoot\config\telegram.env").Hash -ne $CredentialHash) { throw 'Credentials modified' }
if ((Get-FileHash "$InstallRoot\config\capture_config.json").Hash -ne $CaptureHash) { throw 'Existing ADB device settings modified' }
if ((Get-FileHash "$InstallRoot\config\plate_allowlist.json").Hash -ne $AllowlistHash) { throw 'Private plate allowlist modified' }
if (Test-Path "$InstallRoot\.plate-venv") { throw 'Deployment without dependency flags must not create an OCR environment' }
foreach ($Name in @('record_protocol.py','region_policy.py','person_activity.py','telegram_video.py', 'telegram_upload.py', 'preview_ui.py', 'latest_vehicle.py', 'capture_region.py','stage4b_motion_monitor.py','stage5c_recorder.py')) {
    if (-not (Test-Path -LiteralPath "$InstallRoot\scripts\$Name")) { throw "Missing $Name" }
}
$Backups = @(Get-ChildItem -LiteralPath "$InstallRoot\backups" -Directory)
if ($Backups.Count -ne 1) { throw 'Backup not created' }
if ((Get-Content -LiteralPath (Join-Path $Backups[0].FullName 'scripts\stage4b_motion_monitor.py') -Raw).Trim() -ne '# old fixture') { throw 'Backup not original' }
if (-not (Test-Path "$InstallRoot\runtime\stop.flag")) { throw 'Stop flag not retained for explicit start' }
$Policy.roi = @(0.1, 0.01, 0.7, 0.08)
$Policy | Add-Member -MemberType NoteProperty -Name 'roi_coordinate_space' -Value 'mumu_content_v1' -Force
$Policy | Add-Member -MemberType NoteProperty -Name 'roi_reselect_required' -Value $true -Force
$Policy | ConvertTo-Json -Depth 10 | Set-Content "$InstallRoot\config\notification_policy.json" -Encoding UTF8
& "$Kit\installer\DEPLOY_RUNTIME.ps1" -Root $InstallRoot -Python $Python
$RepeatPolicy = Get-Content "$InstallRoot\config\notification_policy.json" -Raw | ConvertFrom-Json
if ($RepeatPolicy.roi_coordinate_space -ne 'mumu_content_v1' -or $RepeatPolicy.roi_reselect_required -ne $true -or ($RepeatPolicy.roi -join ',') -ne '0.1,0.01,0.7,0.08') { throw 'Repeated update lost coordinate/reselection state' }
if ((Get-FileHash "$InstallRoot\config\telegram.env").Hash -ne $CredentialHash) { throw 'Repeated update modified credentials' }
$Polygon = @(@(0.1, 0.1), @(0.9, 0.1), @(0.5, 0.5), @(0.1, 0.9))
$RepeatPolicy.roi = $Polygon
$RepeatPolicy.roi_reselect_required = $false
$RepeatPolicy | ConvertTo-Json -Depth 10 | Set-Content "$InstallRoot\config\notification_policy.json" -Encoding UTF8
& "$Kit\installer\DEPLOY_RUNTIME.ps1" -Root $InstallRoot -Python $Python
$PolygonPolicy = Get-Content "$InstallRoot\config\notification_policy.json" -Raw | ConvertFrom-Json
if (($PolygonPolicy.roi | ConvertTo-Json -Compress -Depth 10) -ne ($Polygon | ConvertTo-Json -Compress -Depth 10)) { throw 'Polygon vertices were not preserved during update' }
if ($PolygonPolicy.roi_coordinate_space -ne 'mumu_content_v1' -or $PolygonPolicy.roi_reselect_required -ne $false) { throw 'Polygon coordinate state was not preserved' }
if ((Get-FileHash "$InstallRoot\config\telegram.env").Hash -ne $CredentialHash) { throw 'Polygon update modified credentials' }
& $Python -c "import json,sys; from pathlib import Path; root=Path(sys.argv[1]); sys.path.insert(0,str(root/'scripts')); from region_policy import validate_roi,box_in_roi; roi=validate_roi(json.loads((root/'config'/'notification_policy.json').read_text(encoding='utf-8-sig'))['roi']); assert len(roi)==4 and len(roi[0])==2; assert box_in_roi((15,15,25,25),roi,100,100); assert not box_in_roi((65,65,75,75),roi,100,100); print('DEPLOYED_POLYGON_GEOMETRY=PASS')" $InstallRoot
if ($LASTEXITCODE -ne 0) { throw 'Deployed polygon configuration or detection failed' }
& $Python -c "import sys; from pathlib import Path; sys.path.insert(0,str(Path(sys.argv[1])/'scripts')); from person_activity import PersonActivityTracker,InactivityTimer; from record_protocol import valid_end_request; tracker=PersonActivityTracker(); person=[{'box':(10,10,30,50),'confidence':0.9}]; assert tracker.update(person,100,100,0); assert not tracker.update(person,100,100,1); timer=InactivityTimer(120); assert not timer.observe('event',False,0); assert timer.observe('event',False,120); assert valid_end_request({'request_id':'event','reason':'NO_MOVEMENT','created_at':100},'event',now=100); print('DEPLOYED_ACTIVITY_POLICY=PASS')" $InstallRoot
if ($LASTEXITCODE -ne 0) { throw 'Deployed activity policy failed' }
$Mapping = @{
    'monitor.py'='stage4b_motion_monitor.py'; 'watchdog.py'='stage5_watchdog.py'
    'recorder.py'='stage5c_recorder.py'; 'recorder_watchdog.py'='stage5c_recorder_watchdog.py'
    'record_protocol.py'='record_protocol.py'; 'region_policy.py'='region_policy.py'
    'person_activity.py'='person_activity.py'; 'telegram_video.py'='telegram_video.py'
    'preview_ui.py'='preview_ui.py'; 'capture_region.py'='capture_region.py'
    'adb_capture.py'='adb_capture.py'; 'plate_recognition.py'='plate_recognition.py'
    'stream_health.py'='stream_health.py'; 'superlive_recovery.py'='superlive_recovery.py'
    'plate_history.py'='plate_history.py'; 'plate_replay.py'='plate_replay.py'
    'video_location.py'='video_location.py'
}
foreach ($Name in $Mapping.Keys) {
    if ((Get-FileHash -LiteralPath "$Kit\src\$Name").Hash -ne (Get-FileHash -LiteralPath (Join-Path "$InstallRoot\scripts" $Mapping[$Name])).Hash) {
        throw "Repository-to-runtime mapping differs: $Name"
    }
}

# A clean credential/config installation also stays inside the temporary fixture.
$FreshRoot = Join-Path $TestRoot 'fresh'
New-Item -ItemType Directory -Force -Path "$FreshRoot\config" | Out-Null
@{record_root=(Join-Path $TestRoot 'fresh-recordings')} | ConvertTo-Json | Set-Content "$FreshRoot\config\recording_config.json" -Encoding UTF8
& "$Kit\installer\DEPLOY_RUNTIME.ps1" -Root $FreshRoot -Python $Python
if ((Get-FileHash -LiteralPath "$FreshRoot\config\telegram.env").Hash -ne (Get-FileHash -LiteralPath "$Kit\config\telegram.env.example").Hash) { throw 'New install did not use safe configuration example' }
$Template = Get-Content -LiteralPath "$FreshRoot\config\telegram.env"
if ($Template -notcontains 'BOT_TOKEN=' -or $Template -notcontains 'CHAT_ID=') { throw 'New install must have empty Telegram settings' }
$FreshPolicy = Get-Content -LiteralPath "$FreshRoot\config\notification_policy.json" -Raw | ConvertFrom-Json
$DefaultPolicy = Get-Content -LiteralPath "$Kit\config\notification_policy.example.json" -Raw | ConvertFrom-Json
if (($FreshPolicy.roi | ConvertTo-Json -Depth 8 -Compress) -ne ($DefaultPolicy.roi | ConvertTo-Json -Depth 8 -Compress) -or $FreshPolicy.roi_coordinate_space -ne 'mumu_content_v1') { throw 'New install ROI defaults incorrect' }
if (-not (Test-Path -LiteralPath "$FreshRoot\runtime\stop.flag")) { throw 'New install must remain stopped for explicit start' }
$Capture = Get-Content -LiteralPath "$FreshRoot\config\capture_config.json" -Raw | ConvertFrom-Json
if ($Capture.source -ne 'adb' -or $Capture.auto_detect -ne $true -or $Capture.serial -ne '' -or $Capture.ensure_highest -ne $true) { throw 'Safe camera capture defaults missing' }
if ($Capture.recovery.enabled -ne $false -or $Capture.recovery.device_name -ne '' -or $Capture.recovery.channel -ne 1 -or $Capture.recovery.stale_seconds -ne 60 -or $Capture.recovery.retry_seconds -ne 30 -or $null -ne $Capture.recovery.frame_roi) { throw 'Public recovery defaults must stay disabled with no private target' }
$Allowlist = Get-Content -LiteralPath "$FreshRoot\config\plate_allowlist.json" -Raw
if ($Allowlist -notmatch '^\s*\[\s*\]\s*$') { throw 'Public installation must not contain a private plate target' }
if ((Get-FileHash "$FreshRoot\requirements-plates.txt").Hash -ne (Get-FileHash "$Kit\requirements-plates.txt").Hash) { throw 'Isolated OCR requirements missing' }
if ((Get-FileHash "$InstallRoot\config\capture_config.json").Hash -ne $CaptureHash -or (Get-FileHash "$InstallRoot\config\plate_allowlist.json").Hash -ne $AllowlistHash) { throw 'Repeated upgrades changed local camera or plate settings' }
Write-Host 'ISOLATED_DEPLOYMENT_TEST=PASS'
foreach ($Name in @('app_branding.py', 'hq_video.py', 'daily_report.py')) {
    if (-not (Test-Path -LiteralPath "$FreshRoot\scripts\$Name")) { throw "Missing new runtime helper: $Name" }
}
foreach ($Name in @('adb_discovery.py', 'plate_corrections.py', 'setup_wizard.py', 'vehicle_registry.py', 'plate_review.py')) {
    if ((Get-FileHash "$FreshRoot\scripts\$Name").Hash -ne (Get-FileHash "$Kit\src\$Name").Hash) { throw "New helper mismatch: $Name" }
}
if (-not (Test-Path "$FreshRoot\Settings.bat")) { throw 'Settings launcher missing' }
if ((Get-FileHash "$FreshRoot\assets\superlive-ai.ico").Hash -ne (Get-FileHash "$Kit\assets\superlive-ai.ico").Hash) { throw 'Icon deployment mismatch' }
foreach ($Name in @('DASHBOARD.en.md', 'DASHBOARD.zh-TW.md', 'INSTALL.en.md', 'INSTALL.zh-TW.md', 'media\dashboard-preview.png', 'media\dashboard-guide.en.png', 'media\dashboard-guide.zh-TW.png', 'media\dashboard-layout.gif')) {
    if ((Get-FileHash "$FreshRoot\docs\$Name").Hash -ne (Get-FileHash "$Kit\docs\$Name").Hash) { throw "Dashboard documentation mismatch: $Name" }
}
# Leave a small fixture for inspection. No recursive deletion is performed.
Write-Host "FIXTURE=$TestRoot"
