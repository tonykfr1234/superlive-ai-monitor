$ErrorActionPreference='Stop'
$Root="$env:USERPROFILE\Downloads\SuperLive_AI"
$Cfg="$Root\config"
$EnvFile="$Cfg\telegram.env"
New-Item -ItemType Directory -Force -Path $Cfg|Out-Null
Write-Host "=== Telegram setup inside VMware ==="
Write-Host "Token will NOT be printed or copied from old PC."
$Sec=Read-Host 'BOT_TOKEN' -AsSecureString
$Ptr=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($Sec)
try{$Token=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($Ptr)}finally{[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($Ptr)}
if([string]::IsNullOrWhiteSpace($Token)){throw 'BOT_TOKEN empty'}
$Base="https://api.telegram.org/bot$Token"
$Me=Invoke-RestMethod -Method Get -Uri "$Base/getMe" -TimeoutSec 20
if(-not $Me.ok){throw 'Telegram getMe failed'}
Write-Host "BOT=@$($Me.result.username) AUTH=PASS"
Write-Host "Open this bot in Telegram, press Start, send: test"
Read-Host 'After sending test, press Enter here'
$U=Invoke-RestMethod -Method Get -Uri "$Base/getUpdates" -TimeoutSec 20
$Chats=@()
foreach($X in $U.result){$M=$X.message;if($M -and $M.chat){$Chats += [pscustomobject]@{Update=$X.update_id;ChatID=[string]$M.chat.id;Type=[string]$M.chat.type;First=[string]$M.chat.first_name;Last=[string]$M.chat.last_name;Text=[string]$M.text}}}
if($Chats.Count -eq 0){throw 'No Telegram chat found. Send test to the bot and rerun.'}
$C=$Chats|Sort Update -Descending|Select -First 1
if($C.ChatID -notmatch '^-?[0-9]+$'){throw 'Detected CHAT_ID is invalid'}
Write-Host "CHAT_ID=[CONFIGURED] TYPE=$($C.Type)"
@("BOT_TOKEN=$Token","CHAT_ID=$($C.ChatID)")|Set-Content $EnvFile -Encoding UTF8
$Test=Invoke-RestMethod -Method Post -Uri "$Base/sendMessage" -Body @{chat_id=$C.ChatID;text="SuperLive AI Telegram test: PASS"} -TimeoutSec 20
if(-not $Test.ok){throw 'Telegram send test failed'}
$Token=$null;$Sec=$null
Write-Host "TELEGRAM_VM_SETUP=PASS"
Write-Host "Saved locally: $EnvFile"
