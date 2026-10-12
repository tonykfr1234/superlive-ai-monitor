$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
$Root = "$env:USERPROFILE\Downloads\SuperLive_AI"
$Python = "$Root\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Existing installation was not found. Run installer\INSTALL_FROM_REPO.bat first.'
}
& $Python "$Repo\scripts\security_scan.py" $Repo
if ($LASTEXITCODE -ne 0) { throw 'Repository security scan failed.' }

& "$PSScriptRoot\DEPLOY_RUNTIME.ps1" -Root $Root -Python $Python -InstallUpdateDependencies
Write-Host 'UPDATE_FROM_REPO=PASS'
Write-Host 'Your polygon, recording path, storage settings and Telegram configuration were preserved.'
Write-Host 'Open MuMu / SuperLive live view and run the deployed Start_AI_Monitor.bat when ready.'
Write-Host 'R: polygon; click 3+ vertices; Enter: save; right-click / Backspace: undo; Esc: cancel.'
