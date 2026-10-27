# One-click Windows installation

1. Install and open MuMu or MEmu, enable its ADB option, and open your camera's live view in SuperLive. Emulator installation and camera sign-in remain local user steps.
2. Download the complete GitHub ZIP and extract it, or clone the repository. Double-click **`INSTALL.bat`** at its root.
3. The installer provisions Python 3.12 through winget when needed, installs monitoring and isolated OCR dependencies, then opens the settings window. Without winget, install Python 3.12 first and rerun.
4. Confirm the detected local emulator. Multiple devices require an explicit selection. Discovery retries every 15 seconds; you may also select `adb.exe` manually. Only local emulator endpoints are probed, never the LAN.
5. Enter your Telegram Bot Token and numeric Chat ID in the Telegram tab. Start the bot in Telegram first. Token verification sends no message and does not verify the destination Chat ID. Leave both fields empty to disable Telegram.
6. Enter highlighted plates in the plate tab and choose **Save and start monitoring** (`儲存並啟動監控`). Use R and Enter to define your polygon once the correct camera appears. The settings window currently uses Traditional Chinese labels.

Runtime: `%USERPROFILE%\Downloads\SuperLive_AI`. New installations store videos and reports in its `recordings` directory; upgrades preserve existing paths. Reopen `Settings.bat` for changes, `Start_AI_Monitor.bat` to start, and `Stop_AI_Monitor.bat` to stop. Settings take effect on the next monitor start. Installation does not alter power settings or add automatic startup services/tasks.

For upgrades, download the complete new version and run `installer\UPDATE_FROM_REPO.bat`, then restart monitoring. A pinned ADB device reconnects without silently switching to another emulator.

## Vehicle and plate close-up

The lower-left card shows the vehicle on its left and an enlarged plate crop plus plate text on its right. Crops come from the exact OCR evidence frame, with spatial/time association and image-hash validation. Missing evidence shows a placeholder. Enlargement does not invent image detail.

Verified spellings can rank near-tied, high-confidence candidates actually returned by OCR; they never insert unseen characters. Explicit manual aliases use `observed=correct` (synthetic example: `ABC1235=ABC1234`) and apply only to that complete observed string. Raw evidence is retained and notifications mark manual corrections. Aliases share the corrected plate's three-minute cooldown and are excluded from automatic learning anchors.

Credentials, camera selections, real plates, corrections and images remain in local `config`/`runtime` directories. Do not publish the installed runtime folder. Public illustrations use synthetic scenes only.
