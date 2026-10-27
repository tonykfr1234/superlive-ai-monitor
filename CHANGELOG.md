# Changelog

## 2026-10-27

- Replace the earlier one-plate-per-clip policy with continuous live-frame OCR, including while idle. Confirm each reading across independent frames, keep scanning other plates, and persist a 180-second cooldown per normalized observed plate across restarts and clip rollovers. Do not process historical videos.
- Send new live plate alerts immediately from an independent worker, bypassing recording quiet periods and video uploads. Successful plate deliveries enforce their own durable 180-second cooldown; videos remain ordered by recording trigger time with a separate watermark. Video captions may include multiple plates belonging to that exact clip. Old/historical plate events are not replayed.
- 車牌改為持續辨識即時畫面，未錄影時也運作，不再每段只留一個車牌；多影格確認後通知，同一實際讀值三分鐘內不重複，重啟與換片保留冷卻，不處理歷史影片。
- 車牌通知改用獨立程序，立即傳送、不等影片也不受前兩分鐘靜默限制；影片仍依錄影時間先後傳送，並使用獨立的傳送進度。影片可保留同段多筆車牌摘要；舊的歷史事件不重新推播。以下單一車牌及共用排序規則為當日較早版本，已由此更新取代。
- Keep Telegram notifications in detection-time order with concise recording times and observed plates. Highlight exact configured targets with 🚨 ⭐, label fuzzy associations as 🔎 ⚠️ possible matches, and omit unmatched-list and missing-plate notices. Private targets remain local configuration only.
- Telegram 維持依偵測時間推播，簡化為錄影時間與實際車牌；完全符合本機指定車牌時以 🚨 ⭐ 標示，容錯比對使用 🔎 ⚠️「可能符合」，移除未符合名單與車牌未確認文字。私人車牌只保存在本機設定。
- Simplify the preview footer to the latest vehicle detection time and associated plate. Hide routine OCR/source/progress text, including the historical-processing notice; historical replay remains disabled by default.
- 預覽底部僅保留最近一次車輛偵測時間與對應車牌，移除辨識狀態、來源、掃描進度及歷史處理提示；歷史影片仍不會自動處理。
- Recognize only fresh frames from the active recording. Confirm at most one complete-format plate from three independent, spatially consistent frames, then stop OCR for that clip; the durable clip identity survives worker restart. Keep actual readings separate from fuzzy allowlist matches and associate summaries/captions with the exact recording ID.
- Disable historical replay by default and remove its progress from the live controls. The offline CLI requires an explicit `--allow-history` opt-in; existing files and results are retained without rescanning.
- Add private seven-day recognition memory. Clean, high-confidence results from distinct clips teach repeated OCR confusions. Memory can rank close alternatives actually read in the current frame only after three independent clips; inferred selections never become training anchors. This is evidence-based candidate ranking, not OCR model retraining or proof of the real plate.
- 只辨識正在錄製的新片段：至少三張獨立且位置連續的畫面確認同一完整格式讀值後，本段停止 OCR，重啟也不重複列出其他號碼。車牌摘要與 Telegram 說明依錄影 ID 對應，原始讀值與名單容錯比對分開。
- 預設停用歷史補辨識，移除主畫面的歷史掃描進度；既有影片與紀錄保留。舊命令不會啟動掃描，離線工具需明確指定 `--allow-history` 才會執行。
- 累積七天內新片段的清楚辨識及同畫面混淆字串，至少三段獨立影片支持後才輔助選擇目前畫面實際讀出的近似候選；推測修正不會回頭當作學習答案。這是本機統計記憶，未重新訓練 OCR 模型，也不能保證反覆讀值一定正確。

## 2026-10-26

- Order Telegram videos and confirmed plate messages together by original detection time. An older failed delivery holds newer notifications until retry succeeds; an unfinished recording also holds its later plate events. Preserve recording quiet periods and the independent 23:00 CSV exception. Backlogged daily reports send oldest first.
- Stream high-quality video uploads in bounded multipart chunks with Content-Length instead of buffering and writing the entire file at once; keep TLS verification and the original MP4.
- Show the latest live vehicle detection time below the preview buttons, with an observed plate only when its time and location correspond to the vehicle. Persist this local summary across restart; historical replay cannot replace it.
- Show Taiwan detection times and the latest vehicle/observed plate information within each delivered event. Plate records already included in a video caption are not sent again; late OCR older than the delivered timeline stays in local records instead of inserting an older alert at the bottom. Existing Telegram messages cannot be reordered.
- Telegram 影片與已確認車牌合併依原始偵測時間由早到晚傳送；舊通知失敗或等待重試時，後面的通知一起等待，錄影尚未完成時也不先送該片車牌。保留每段前兩分鐘靜默及每日 23:00 CSV 例外，積欠日報依日期補送。
- 高畫質影片改以分塊串流上傳，避免一次緩衝及寫入整部影片；保留原片與 TLS 憑證驗證。
- 監控視窗按鈕下方顯示最近一次即時車輛偵測時間；車牌須有對應時間與位置才顯示實際讀值，否則標示未確認。摘要只保存本機，重新啟動仍可查看，歷史補辨識不會覆蓋它。
- 訊息顯示台灣偵測時間，底部列出該通知內最新車輛／實際車牌資訊；已包含於影片說明的車牌不重複推播。晚到且早於已送時間的 OCR 留在本機紀錄，不再插入舊時間通知；既有 Telegram 訊息無法重新排序。

## 2026-10-24

- Add opt-in ADB camera-frame stall recovery, separate from person/vehicle inactivity. Reopen SuperLive and verify the configured device, channel and quality before publishing camera frames again; retry failures with a bounded delay and honor manual stop. Public examples leave the private camera name blank and recovery disabled.
- 新增可選的 ADB 畫面停滯復原：與人物／車輛無移動分開判斷，重開 SuperLive 後確認指定裝置、頻道與畫質再恢復影像。失敗等待重試，停止程式後不再操作；公開範本關閉功能，不包含私人裝置名稱。

## 2026-10-22

- Keep matching plate records visible in a right sidebar, grouped by Taiwan calendar date with daily counts and newest records first; narrow windows place the list below the camera. Keep OCR and capture status in the bottom controls, preserve the image aspect ratio, and use “其他紀錄” to expand and jump to other results or collapse them and return to matches. Two-edit matching, seven-day retention and filename links remain unchanged.
- 右側常駐顯示符合本機車牌名單的紀錄，依台灣日期分組並列出每日筆數，由新到舊排列；窄視窗改放影像下方。狀態與畫質留在底部，影像維持等比例；「其他紀錄」可展開並跳到其他結果，收起後返回符合紀錄。兩碼容錯、七天保存與點檔名定位原片功能不變。

## 2026-10-21

- Retry brief Windows permission errors while reading plate samples with a small bounded delay; persistent failures remain visible and inference errors are not retried as file locks.
- 車牌影像遇到 Windows 短暫檔案鎖時會有限次重試；持續失敗仍顯示錯誤。

- Move OCR, capture quality and replay status into the bottom control area. Keep the camera view full-width by default; a “車牌紀錄” button opens and closes the history drawer below the image without covering camera pixels.
- 辨識狀態、畫質與補辨識進度集中到底部按鈕區；預設不佔用右側，按「車牌紀錄」才展開下方清單。

- Expand only confirmed matches to the local allowlist (up to two edits) by default. Keep other results in a collapsible section without deleting them. Click a recording filename to reveal the original MP4 in Windows File Explorer; local paths remain private.
- 預設展開符合本機車牌名單、兩碼容錯內的確認結果；其他辨識紀錄收折保留。點擊影片檔名即可開啟檔案總管並選取原片，完整路徑只保留於本機。

- Retain up to seven days of recognition history, including recordings with no plate, unconfirmed candidates and scan failures. Show filenames and distinguish recording replay from live observations; batch replay updates local history without bulk Telegram notifications. Add mouse-wheel, draggable scrollbar and PageUp/PageDown navigation.

- Trigger recording on person **or vehicle** movement inside the polygon: people, cars, motorcycles, buses and trucks. Keep the 120–300-second clip boundaries, movement rollover, continuous 120-second inactivity stop and initial 120-second Telegram quiet period unchanged; the daily CSV remains exempt.
- Separate people, vehicle counts and vehicle types in Telegram video captions and daily CSV columns. Vehicle clips without a confirmed plate explicitly say “車牌未確認”. Preserve actual OCR text, Taiwan event time and fuzzy allowlist results separately; replay-derived records identify the source basename and clip offset as “原片補辨識”.
- Scan the full camera image plus overlapping tiles for small plates, independently of the recording polygon or person/vehicle detections. Keep multi-frame confirmation and deduplicate candidates from the same frame.
- Recover the isolated plate worker after exits, startup timeouts and stale heartbeats with bounded retries, while keeping the preview and movement detector responsive. Show the scan state and failure reason without inventing a plate result.
- Wrap source resolution, quality, OCR health, errors and up to seven days of recognition history within the preview panel; show separate people and vehicle counts. Narrow windows use a taller bottom panel to keep text clear of the controls.
- 新增人物或車輛（汽車／機車／巴士／卡車）移動觸發；原有兩分鐘靜默、兩至五分鐘片長及每日 23:00 CSV 例外規則不變。Telegram 與 CSV 區分人物、車輛與車牌，沒有確認讀值就標示未確認。
- 車牌改以全畫面加重疊區塊掃描，異常辨識程序自動恢復；來源、畫質、錯誤原因及最多七天的歷史紀錄自動換行，可透過滾輪、可拖曳捲軸及 PageUp／PageDown 檢視，避免欄位截字及遮擋。
- Recover automatically from unavailable ADB devices, unconfirmed highest quality and stalled H.264 streams. Connect configured TCP devices, retry with bounded backoff, and close quality popups before capture; no manual monitor restart is required.
- 修正啟動時畫質確認失敗後永久黑畫面的問題；ADB 自動連線、持續重試，串流卡住時自動恢復，畫質選單確認後收起。
- Send the original high-quality MP4 directly when under the Telegram size limit. Oversized delivery copies are temporary and removed after successful delivery.
- Add optional full-resolution ADB H.264 capture shared by detector and recorder, with stale-frame rejection. Verify highest SuperLive quality and dismiss controls before monitoring.
- Show YOLO11n and retained recognition history. Add isolated FastALPR/CCT OCR, temporal confirmation, private allowlist matching with up to two edits, and quiet-period-aware Telegram plate messages. Preserve observed text separately from fuzzy matches.
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
