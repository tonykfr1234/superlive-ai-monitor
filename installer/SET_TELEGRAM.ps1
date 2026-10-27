$ErrorActionPreference = 'Stop'
$Root = "$env:USERPROFILE\Downloads\SuperLive_AI"
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run INSTALL_FROM_REPO.bat first.' }
& $Python "$Root\scripts\setup_wizard.py" --root $Root
if ($LASTEXITCODE -notin @(0, 2)) { throw 'Unable to open settings.' }
