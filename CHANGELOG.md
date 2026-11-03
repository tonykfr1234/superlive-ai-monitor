# Changelog

## 2026-11-03 — 每次事件只切一次高畫質、完整畫面錄影、車牌辨識升級 / One quality switch per event, full-frame clips, plate recognition upgrade

### 改了什麼

- **每次事件只切一次高畫質、最多 3 次**：偵測到人或車時從低畫質切到高畫質一次（目的是看清車牌），確認選到「最高」後整個事件期間不再點按；切換失敗才重試，同一事件最多 3 次，第 3 次仍失敗就記錄 `ADB_ACTIVITY_QUALITY_GAVE_UP` 並停止點按，等下一次事件再試。回到低畫質同樣最多 3 次。每次切換的紀錄都帶 `event=…` 與 `attempt=n/3`。
- **Telegram／錄影改回完整畫面（修正「被切割」）**：畫質切換期間為了不讓 AI 把選單當成目標，會把畫面上 12%、下 18% 與選單位置塗灰；舊版把這個遮罩寫進了錄影共用的畫面，所以影片（以及寄到 Telegram 的影片）在事件一開始常出現上下灰帶。實測 2026-11-03 的 8 段影片中，每段有 3%～38% 的取樣畫面被遮。現在錄影、Telegram 一律取完整 1920×1080 原始畫面，遮罩只用在 AI 辨識自己的那份畫面，且切換結束後只再保留 3 秒（原 30 秒），不再在切到高畫質後遮住車牌區域。
- Telegram `sendVideo` 帶上影片實際寬高，Telegram 不再自行猜比例裁切預覽。
- **右上角逍遙模擬器視窗改為滿版**：模擬器內的 SuperLive 本來就是 1920×1080 滿版（ADB 截圖確認），但啟動時把整個視窗（含 33 px 標題列與 44 px 右側工具列）排成 16:9，畫面區只剩 835×493（偏高），所以右側與下方出現暗帶。現在先量出模擬器邊框，再讓「畫面區」剛好 16:9：實測視窗 879×503、畫面區 835×470（1.777），暗帶消失。未更改模擬器本身設定；無法量測時（例如 MuMu）沿用原本排法。
- **車牌辨識：台灣車牌格式檢查**：只接受台灣核發的格式（汽車 ABC-1234、AB-1234、1234-AB；機車 ABC-123、123-ABC），且不接受台灣車牌不使用的字母 I、O。格式明確時才修正常見混淆（字母位置的 8→B、5→S、2→Z、6→G；數字位置的 O/D/Q→0、I→1、B→8、S→5、Z→2、G→6），只有恰好一種格式成立才採用，原始讀值保留在證據中。
- 人車離開後保持高畫質 60 秒（原 15 秒）才回低畫質：CPU 閒置時推論可能停 3～12 秒，原本 15 秒容易在同一事件中高低來回切換。

### 車牌辨識 / License plate recognition

- 偵測模型比較後**維持 YOLOv9-t-384**：在 16 段真實錄影（239 張畫面）上，較大的 YOLOv9-s-608 多框出 70% 車牌（128 vs 75 張），但讀得出有效台灣車牌的畫面幾乎一樣（58 vs 57），確認出車牌的影片同為 4/16；每張畫面耗時 3.0 秒 vs 0.38 秒（正式設定 2 執行緒），而且全核心跑大模型時主監控推論會從每秒一次掉到 20～60 秒一次。
- 新增台灣車牌格式檢查與格式限定的混淆修正（見上）；在這 16 段中沒有改變任何結果，屬於防誤讀保護，不是量測到的提升。
- 新增 `docs/PLATE_RECOGNITION.md`（中英文）與 README「車牌辨識」段落：技術、實測數字、測試條件與限制；所有車牌號碼與影像只留在本機。

### 怎麼測

- 實機畫質切換（11:53、11:58，正式監控同時運作）：高 25.9／26.0 秒、低 22.4／23.8 秒，全部成功，切換後 Android 控制項樹中沒有任何畫質選單節點（選單確實關閉）；第二次切換期間每秒檢查一次，SuperLive AI 視窗 100% 有回應、YOLO 推論最大間隔 3.1 秒、監控 PID 不變。
- 使用者約 11:04 看到的「沒有回應」、面板黑畫面、選單開著：當時是部署後監控重新啟動（啟動時切到低畫質，選單會打開約 15～26 秒），加上 10:31～11:15 的車牌基準測試佔滿 CPU，YOLO（在視窗主執行緒上）一次推論超過 5 秒，Windows 就把視窗標成沒有回應。基準測試限制 2 核心後，視窗與推論都恢復正常。
- 653 項離線測試全部通過（新增：模擬器畫面區 16:9 排版、一次事件只切一次、失敗 3 次後停止、重試成功即停止點按、下一次事件重新計次、錄影讀到完整畫面而 AI 才遮罩、Telegram 影片寬高、台灣格式／修正）；`security_scan=PASS`；隔離部署測試 `ISOLATED_DEPLOYMENT_TEST=PASS`；部署後三個變更檔與原始碼雜湊一致。
- 遮罩問題：以 2026-11-03 07:44～09:59 的 8 段真實影片每 20 張取 1 張檢查上下灰帶，每段 3%～38% 被遮（修正前）。
- 車牌：本機離線比較 3 個偵測模型（同一 OCR、同一畫面），另以正式工作程序設定量測每張畫面耗時。
- 正式監控：本版 2026-11-03 10:09／11:08 兩次部署後，啟動時切到低畫質皆第一次成功（`attempt=1/3`，14.5 秒／26.1 秒，最大畫面間隔 1.0／0.9 秒），監控、錄影與兩個 watchdog 持續運作。
- 前一版（c461d32）2026-11-02 18:42～11-03 10:08 的真實事件：7 次切到高畫質全部第一次成功（14.7～31.0 秒），切換期間最大畫面間隔 0.4～3.2 秒，監控整晚未停；其中一次人車剛離開 15 秒就切回低畫質、隨即又有人車出現，因此本版改為保持 60 秒。

### 已知限制

- **本版部署後到發布前沒有出現真實人車事件**：「一次事件只切一次／最多 3 次」只由單元測試與前一版的真實事件紀錄驗證；完整畫面錄影與 Telegram 寬高也尚未在本版的真實事件影片上確認。依專案規則未發送測試用 Telegram 訊息，下一次真實事件的影片就是驗收證據。
- 3 次都失敗時，該事件維持低畫質，車牌可能看不清（紀錄 `ADB_ACTIVITY_QUALITY_GAVE_UP`）。
- 車牌：16 段中 12 段沒有確認出車牌（太小、角度、移動模糊）；比對對象是系統先前自己確認的結果，只有 1 筆人工確認，不是人工標註準確率。t-640 只測了 2 段。
- 測試大模型時曾讓主監控推論變慢（10:31～11:15）；之後把測試程序限制在 2 核心、閒置優先權，推論恢復每秒一次以上。
- YOLO 推論仍在視窗主執行緒；CPU 被其他程式佔滿到單次推論超過 5 秒時，視窗會暫時顯示「沒有回應」（擷取、畫質切換、錄影不受影響）。把推論移到背景執行緒是後續工作。

## 2026-11-02 — 依人車自動切換畫質（工具列隱藏修正）／ Activity quality switching with hidden toolbar

### Release Note 維護規則

- 每次修改程式、設定、安裝器或文件，都必須同步更新本檔，記錄日期、變更內容、驗證結果及已知限制；交付回覆也需附精簡 Release Note。

### 改了什麼

- **畫質按鈕找不到（主因）**：SuperLive 監看畫面的工具列約一秒多就自動隱藏，而 Android 控制項樹（uiautomator dump）一次要 1.5 秒以上，等樹拿到時按鈕已消失。現在改成在同一個 `adb shell` 指令內「點畫面顯示工具列 → 等 0.3 秒 → 點畫質按鈕」，並與「直接點按鈕」交替重試（點畫面會切換工具列顯示／隱藏，兩種狀態都能命中），最多四次，整體 60 秒逾時，失敗 5 秒後重試。
- 畫質按鈕位置第一次學到後存到本機 `runtime/superlive_quality_button.json`，監控重啟不必再靠剛好抓到工具列；學習時最多重新顯示工具列三次。
- 畫質切換在背景執行緒執行，AI 擷取與辨識持續進行；切換期間選單區域不送進 AI，車牌辨識暫停。每次切換記錄 `seconds`、`max_frame_gap`、`dumps` 方便現場判讀。
- 畫質確認若剛好遇到串流換段，最多等 4 秒取得新畫面，不再立即失敗。
- 串流每 5 秒檢查 SuperLive 程序的 `pidof` 在畫質操作期間暫停；平時單次 3 秒逾時不再讓整條擷取重連，連續三次才視為裝置無回應。
- **錄影不中斷**：錄影端原本連續 5 次（約 1 秒）讀不到新畫面就結束影片；現在短暫停頓時重複上一張畫面，停頓超過 20 秒（`capture_stall_seconds`）才結束；事件開始時最多等 10 秒取得第一張畫面。
- **監控／錄影不再因狀態檔被讀取而當掉**：Windows 上其他程式正在讀 `camera_activity.json`、`person_vehicle_activity.json` 或 `recording_state.json` 時，原子取代會出現 `PermissionError`，舊版會讓監控程序結束或錄影中斷；現在略過該次寫入，下一次再寫。畫質判斷讀取活動檔改為每秒最多兩次。
- 保留 1920×1080 擷取，解碼每秒 10 張；CPU 推論兩個執行緒。
- 車牌預覽：每段影片獨立驗證清晰度、移動與片段關聯後才顯示（Codex 先前待發布的變更，一併發布）。
- 提醒規則維持不變：台灣時間 23:00～06:00 靜音；06:00～22:00 每兩小時檢查，過去兩小時沒偵測到人或車就推播請使用者查看監視器。

### 怎麼測

- 單元／回歸：639 項離線測試全部通過（含新增的工具列隱藏、按鈕記憶、`pidof` 逾時容忍、錄影停頓、狀態檔鎖定等測試）；`security_scan=PASS`；隔離部署測試 `ISOLATED_DEPLOYMENT_TEST=PASS`。
- 實機（逍遙模擬器 + SuperLive，工具列隱藏狀態）：單獨呼叫 `set_quality` 高→低→高→低四次全部成功（18～28 秒）。
- 實機受控測試（以活動訊號驅動、YOLO 同時推論真實畫面）：低 13.5 秒、高 15.7 秒、低 19.9 秒完成，各階段 YOLO 推論最大間隔 0.89／0.5／0.77 秒，零重試。
- 實機正式監控、真實人車事件：見下方「實測紀錄」。

### 實測紀錄（2026-11-02，正式監控、真實事件，非測試訊息）

| 時間 | 事件 | 畫質切換 | 切換期間最大畫面間隔 | 錄影／推播 |
|---|---|---|---|---|
| 17:56～17:59 | 車輛進入、離開 | 低→高（17:57:41）→低（17:58:45） | —（舊量測，YOLO 間隔 20～30 秒） | 第一段影片 6 秒即中斷（已修正錄影停頓容忍），兩段影片 Telegram 皆 PASS |
| 18:31～18:36 | 人＋車 | 高 17.7 秒、低 17.6 秒 | 0.6 秒／0.5 秒 | 5 分鐘影片 `COMPLETE=True`，期間多次短暫停頓自動續錄；Telegram PASS |
| 18:39 | 人 | 高 46.5 秒 | 0.8 秒 | 影片因部署新版被手動停止（非故障）；Telegram PASS |

- 監控在 18:31:00 因 `camera_activity.json` 被同時讀取（`PermissionError`）而結束一次，watchdog 10 秒後自動重啟；本版已改為略過該次寫入，部署後持續運作未再發生。
- 監控、錄影、兩個 watchdog 在畫質切換期間 PID 不變（17:31～18:11 取樣）。

### 已知限制

- 每次切換約 12～25 秒（主要是 4～6 次 uiautomator dump），期間畫面仍持續送進 AI。
- 正式監控在 CPU 上本來就約每秒一次推論，閒置時也會有 3～12 秒的推論間隔（畫面靜止時 screenrecord 不出新畫面）；這不是畫質切換造成的。
- 偶發的 `ADB stream stalled` 重連（約每 15 分鐘一次，與畫質切換無關）仍存在。
- 23:00 靜音與隔日 06:00 檢查只以模擬時間驗證；本次未發送任何測試用 Telegram 訊息，實測期間的推播都是真實事件。

## 2026-11-02 — Daytime person/vehicle activity checks

- Launch both watchdogs with `pythonw.exe` and run their monitor/recorder children with `CREATE_NO_WINDOW`. The monitoring preview remains visible, but the two black watchdog consoles no longer appear; logs continue in `logs/Stage5` and `logs/Stage5C`.
- On first render, place the emulator at the top-right and the SuperLive AI preview at the bottom-right of the same monitor. Equal-width, aspect-aware rectangles are derived from the current work area with a gap between them, so they do not overlap and adapt to the display resolution.
- Replace the notification-silence counter with fixed Taiwan-time checks at 06:00, 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00 and 22:00. Send one Telegram prompt to inspect the camera when the preceding two hours contain no person or vehicle observation.
- Keep the check completely silent from 23:00 through 06:00, including retries that reach the quiet period. Persist the checked slot across restarts and make other Telegram deliveries independent from this activity check.
- 將原本以「多久沒有成功推播」計時的提醒改為台灣時間固定兩小時檢查。過去兩小時沒有人或車時，推播請使用者查看監視器；23:00～06:00 完全靜默，重啟保留已檢查時段，其他 Telegram 通知不會重設判斷。
- 兩個 watchdog 改用無主控台模式在背景執行，監控／錄影子程序也不再建立黑色命令視窗；監控預覽仍正常顯示，紀錄仍寫入原本的 `logs` 資料夾。
- 首次顯示預覽時，自動把逍遙模擬器排在同一螢幕右上、SuperLive AI 預覽排在右下；兩者等寬、依畫面比例分配高度並保留間隔，隨目前工作區大小調整且不重疊。

## 2026-10-28 — Consolidated installation and current behavior guide

- Consolidate fresh installation, existing-PC updates, current recording/plate notification rules and troubleshooting in the Traditional Chinese installation guide.
- Distinguish publishing source from applying an update on an installed PC; preserve private configuration and keep application activation under user control.
- This documentation release does not change the monitoring or OCR algorithms. Earlier changelog entries describe superseded behavior where the current guide says otherwise.

## 2026-10-27 — Small-plate OCR enhancement

- Compare original OCR with bounded enlargement, edge-preserving denoising, local contrast and mild sharpening; preserve raw evidence and count one source frame once.
- Keep failed variants isolated and retain the original image for human review. No generative reconstruction or historical video scan.
- Add a direct ZIP installation entry and explain extraction, first-run configuration and enhancement limitations.

## 2026-10-27 — Manual plate confirmation and vehicle registry

- Add the P / Confirm plate button and a separate frozen-image review window, with per-vehicle selection, editable correct plate/color, and a private transactional SQLite registry with audit records.
- Prioritize human-confirmed plates within configurable 1–3-character edit distance. Require three independent spatially consistent frames; use reliable body color only as supporting evidence and retain ambiguous candidates for review.
- Keep raw OCR evidence, mark database inferences in the GUI/Telegram, share canonical plate cooldowns, and exclude inferred results from learning anchors. Late manual confirmations cannot relabel a newer vehicle.
- Show unconfirmed live candidates and exact-frame crops for review. Manual saves do not send retrospective notifications or scan old recordings. Database entries persist; review pictures expire after seven days.


## 2026-10-27 — One-click setup and plate close-up

- Root INSTALL.bat installs dependencies and opens a local ADB / Telegram / plate settings window; Settings.bat reopens it later.
- Discover local MuMu/MEmu ADB installations and endpoints, retry offline devices and require selection when multiple devices are found. Preserve pinned devices and existing private settings.
- Latest vehicle card: vehicle on the left, exact-evidence plate crop and formatted plate text on the right. Validate spatial/time association and image hashes; never substitute a later frame.
- Verified spellings rank only actual confident OCR candidates. Explicit local manual corrections preserve raw evidence, share canonical notification cooldowns and are excluded from automatic training anchors.
- Fresh recordings default to the runtime recordings directory. Installation no longer changes Windows power settings. Updated setup guides and synthetic dashboard illustrations.


## 2026-10-27

- Add a responsive four-card preview: live camera, plate history, latest vehicle crop and consolidated controls. Drag headers to swap cards, press L to restore the default layout, and persist ordering locally. Preserve camera proportions with a soft backdrop, recording pulse and hover/drag highlights.
- Save only the latest bounded vehicle-group crop locally, with its capture time and a hash binding it to the observation JSON. Restore matching photos after restart; a new group never inherits the previous vehicle photo. Publish regenerated synthetic screenshots, bilingual diagram captions and an animated layout demo.
- 改為四區介面：左上即時畫面、右上車牌紀錄、左下最近車輛圖片與時間、右下集中操作。拖曳標題可交換位置，L 還原預設排列並儲存本機；畫面等比例呈現、柔化背景補齊，加入錄影呼吸燈及滑鼠提示效果。
- 最近車輛圖片只保存在本機，附擷取時間並核對圖片雜湊後還原；新車群不沿用舊圖片。同步更新合成示例圖、中英文圖說、排列動畫及使用說明，不公開真實監視器畫面。
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