# Changelog

## 2026-10-21

- Send the original high-quality MP4 directly when under the Telegram size limit. Oversized delivery copies are temporary and removed after successful delivery.
- Add optional full-resolution ADB H.264 capture shared by detector and recorder, with stale-frame rejection. Verify highest SuperLive quality and dismiss controls before monitoring.
- Show YOLO11n and the latest five confirmed plate observations. Add isolated FastALPR/CCT OCR, temporal confirmation, private allowlist matching with up to two edits, and quiet-period-aware Telegram plate messages. Preserve observed text separately from fuzzy matches.
- 車牌名單與攝影機畫面保留本機；兩碼容錯是名單比對，不代表 OCR 每碼正確。


## 2026-10-19

- Support MEmu (逍遙模擬器) alongside MuMu, using MEmu's render window to exclude its top and side toolbars.
- Preserve preview and recording aspect ratio when windows resize; pad unused video space instead of stretching.
- 預覽與錄影等比例縮放，空白處補黑邊，避免拉伸；新增逍遙模擬器支援。切換模擬器後請重新設定多邊形，並只開啟一個監看的模擬器視窗。

## Unreleased - 2026-10-12

- Add daily 23:00 Asia/Taipei trigger CSV reports beside the day’s videos, sent through Telegram sendDocument with durable retry and restart recovery. Include zero-event days and only the 00:00–23:00 interval. No midnight supplements. Daily CSV reports are explicitly exempt from recording quiet periods.
- 每日晚上 11 點彙整觸發時間 CSV，與影片同資料夾保存並傳送 Telegram；只計入 00:00～23:00，不補送午夜完整版；每日 CSV 不受錄影靜默限制，支援斷線重試及重啟補送。

- Add a custom multi-size Windows preview/taskbar icon and app identity.
- Record original capture dimensions directly as H.264 CRF 18. Telegram starts with full-resolution CRF 20 and applies a duration-based bitrate budget only for oversized delivery copies; local originals remain unchanged.

- Retry brief Windows file-sharing locks when atomically replacing recorder state, preserving the previous JSON if replacement still fails.

- Port the current monitor, recorder and shared helpers into the repository's native `src/` layout, with public configuration templates and a shared install/update deployment helper.
- Replace the person/vehicle reminder policy with person-only activity detection in a saved polygon. Support concave polygons, positive-area box overlap and legacy rectangle settings.
- Add same-window polygon selection, undo, save/cancel and full-frame controls below the preview.
- Crop the MuMu title/menu area while retaining camera timestamps and channel labels. Migrate old region coordinates safely or require reselection.
- Use normal clip durations of 120–300 seconds. The first 120 seconds prohibit automatic rollover; movement from 120 seconds and before the 300-second cap immediately closes the old clip and starts a new one.
- Continue detection and retain the 120-second continuous no-movement stop. After an inactivity stop, wait for renewed movement or a new arrival; unknown capture/inference or selection resets the inactivity observation interval.
- Filter minor person-box jitter and accumulate slow displacement. Document that box position/size is a movement proxy affected by camera motion, occlusion and detection errors.
- Remove automatic Telegram start photos. Keep Windows start alerts and local evidence; completed H.264 MP4 videos are the only automatic Telegram notifications.
- Start a fresh 120-second Telegram quiet period for every clip. No new sends begin during it, including older queued videos and retries; repeated rollovers can postpone the backlog. Already-dispatched network requests cannot be recalled.
- Preserve actual partial recordings on manual stop or capture interruption, even when shorter than 120 seconds. Do not pad footage or automatically deliver before the applicable quiet deadline.
- Preserve existing region, Telegram and storage settings through a graceful backup/stop/update flow. Keep the repository's established installer entry point and security files.
- Add 12 localized READMEs with a centered language selector, quick navigation, feature tables, operating workflow and FAQ.
- Add three generated demonstration images and a 36-second GIF/MP4 walkthrough using a synthetic scene and a clearly labeled, compressed timeline for the first-120-second quiet period, movement rollover and inactivity.
- Expand offline regression coverage and Windows CI for geometry, minimum/maximum clip boundaries, rollover, Telegram quiet periods, event protocols, encoding and private-file scanning; use an isolated installation for deployment checks.
- Harden the public-source scanner against credential filename variants, placeholder exemptions and skipped oversized files. Allow only the five documented synthetic media assets.

### 繁體中文

- 新版監控、錄影與共用模組整合到原本的 `src/`，保留儲存庫安裝方式、憑證隔離與既有設定。
- 偵測改為多邊形內的人員，支援凹形、舊矩形及同視窗頂點操作。
- 正常影片為 120–300 秒；前 120 秒不自動換片，滿 120 秒、未達上限時偵測到移動，立即結束舊片並開始新片。
- 保留連續兩分鐘無移動結束及閒置時等待新活動的規則；取消 Telegram 起始照片，只有完成影片自動傳送，Windows 通知與本機存證保留。
- 每段重新開始 120 秒 Telegram 靜默期，所有新傳送與舊片重試一併暫緩；連續換片可能延後佇列，已送出的網路請求無法撤回。
- 手動停止或擷取故障可留下未滿兩分鐘的本機片段，不補畫面偽造片長，也不在適用的靜默期限前自動傳送。
- 新增 12 種語言 README、置中語言列、操作說明與 FAQ，以及三張合成示範圖和 36 秒的 GIF／MP4 示範影片，明確標示時間壓縮。
- 擴充最短／最長片長、換片、Telegram 靜默期等離線測試，搭配 Windows CI、隔離部署檢查與公開檔案／憑證掃描。

## 0.1.0 - 2026-09-27

- Initial public-source layout.
- Person/vehicle YOLO + ByteTrack monitoring.
- Moving/still presence notification policy.
- Windows and Telegram photo alerts.
- Event JPG/CSV evidence.
- MSS/BitBlt capture self-healing.
- AI and recorder watchdogs.
- 30-minute rolling MP4 recording with 80/78% disk watermarks.
- VMware CPU fallback and nested-virtualization documentation.
- English and Traditional Chinese documentation.
- Secret scanner and GitHub Actions security gate.
