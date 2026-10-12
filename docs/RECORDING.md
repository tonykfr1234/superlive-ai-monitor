# Recording Design / 錄影設計

## Clip lifecycle / 影片規則

Normal automatically completed clips last **120–300 seconds**. When the recorder is idle, a newly observed person or renewed movement in the selected polygon starts a clip, a local Windows alert and local evidence. No Telegram start photo is sent. Detection continues during recording.

正常自動完成的影片為 **120–300 秒**。閒置時，指定多邊形內首次看到人員或再次移動，就開始影片、發出本機 Windows 通知並留下本機存證，不傳送 Telegram 起始照片。錄影期間持續偵測。

| Clip age / 目前片長 | Automatic behavior / 自動行為 |
| --- | --- |
| Under 120 seconds / 未滿 120 秒 | Keep recording; movement resets inactivity but does not roll over / 繼續錄影，移動重設無移動計時，不自動換片 |
| At least 120, below 300 seconds / 滿 120、未滿 300 秒 | Movement immediately ends the old clip and starts a new clip / 偵測到移動立即結束舊片並開始新片 |
| 120 continuous seconds without movement / 連續 120 秒無移動 | End the clip and wait for new movement or arrival / 結束並等待再次移動或新人進入 |
| 300-second cap / 達 300 秒上限 | End the current clip / 結束目前影片 |

The minimum clip age and continuous inactivity interval are separate conditions. Absent and stationary people count as no movement. After an inactivity stop, an unchanged stationary person does not immediately start another recording. Selection or invalid capture/inference resets the inactivity observation interval instead of counting as observed inactivity.

單段最低片長與連續無移動時間是兩個不同條件。無人與靜止都算無移動；因無移動結束後，同一個靜止者不會立即再次觸發。選取區域或擷取／偵測無有效結果時，會重設無移動觀察區間，不當成已確認無移動。

Movement is inferred from person-box position/size. Small jitter is filtered and slow displacement accumulates; camera motion, occlusion and detection errors can still affect this proxy. It is not pose analysis.

移動依人員框的位置／大小變化判斷，會過濾輕微抖動並累積慢速位移；攝影機移動、遮擋及辨識誤差仍可能影響結果，這不是姿態辨識。

## Telegram quiet periods / Telegram 靜默期

**Completed videos are the only automatic Telegram notifications.** Each clip starts a 120-second quiet period. The program does not start new Telegram sends during that period, including older queued clips and retries. Movement-triggered rollover immediately starts another clip and another quiet period, so repeated rollovers can keep the backlog waiting.

**Telegram 自動通知只傳送完成影片。** 每段影片開始時都有 120 秒靜默期，期間不啟動新的 Telegram 傳送，舊片與重試也包含在內。移動換片會立即開始新片及新的靜默期，所以連續換片可能讓待傳影片持續等待。

A network request already dispatched before a quiet period may finish or arrive during it; it cannot be recalled. This rule controls the start of new sends, not the arrival time of an in-flight message. Explicit Telegram setup tests are separate from automatic monitoring.

靜默期開始前已送出的網路請求仍可能在期間內完成或抵達，無法撤回。此規則限制的是新傳送的啟動時間，不是已送出訊息的抵達時間。主動執行的 Telegram 設定測試與自動監控分開。

## Interruptions and actual duration / 中斷與實際片長

Manual stop or capture failure may leave a local partial clip shorter than 120 seconds. The program does not pad missing footage to satisfy the normal minimum. A partial clip cannot be automatically delivered before its own quiet deadline; a newer clip's quiet period can defer it further. Pending jobs remain available across restart.

手動停止或擷取故障可能留下未滿 120 秒的本機片段，不會補畫面偽造片長。短片不會在自身靜默期限前自動傳送；如果新片還在靜默期，舊片會再延後。待傳工作會保留，重啟後繼續處理。

## Image and storage / 影像與儲存

Videos retain the full cropped camera/display content, including its timestamps and channel labels, without the MuMu title/menu area or preview controls. The polygon determines activity, not the saved image boundary. Finished clips keep their actual duration and are converted to Telegram-playable H.264 MP4 by the persistent background delivery queue.

影片保留完整裁切後的攝影機內容、時間及通道名稱，不含 MuMu 標題／選單列或預覽控制列；多邊形決定活動範圍，不裁切影片形狀。完成影片保留實際片長，由持久化背景佇列轉成 Telegram 可播放的 H.264 MP4。

| Setting / 設定 | Default / 預設 |
| --- | --- |
| Folder / 目錄 | `C:\SuperLive_Recordings` |
| Target FPS / 錄影速率 | 10 |
| Normal clip duration / 正常片長 | 120–300 seconds |
| Quiet period per clip / 每段靜默期 | 120 seconds |
| Continuous inactivity stop / 連續無移動結束 | 120 seconds |
| Disk check / 磁碟檢查 | 60 seconds |
| Rotation starts / 開始循環 | Drive used >= 80% |
| Rotation stops / 停止刪除 | Drive used <= 78% |

Rotation deletes the oldest eligible closed MP4 files from the recording folder based on total recording-drive usage. Active and pending-delivery files are protected. It stops at the lower watermark. Continued quiet periods and delivery failures can keep more files protected, so allow storage for a backlog.

循環策略依錄影磁碟總使用率，刪除錄影資料夾內最舊、可刪除的已關閉 MP4，降至低水位後停止。目前錄影及待傳工作保護的檔案不會刪除；連續靜默期及傳送失敗會讓更多檔案保留，請預留佇列堆積空間。

A thin-provisioned VMware disk can exhaust the host before the guest reaches its threshold. Plan both capacities together. Video size depends on resolution, scene content, codec and FPS; measure actual usage when planning retention.

VMware 精簡配置磁碟可能在 Guest 達門檻前就耗盡 Host 空間，請一起規劃兩端容量。影片大小依解析度、畫面、編碼及 FPS 而變，保留天數請用實際錄影量估算。

## Image quality / 畫質

Local recording uses H.264 CRF 18 at the original captured dimensions (rounded down to even pixels), with a target of 10 FPS. It does not upscale a low-resolution camera view. Telegram first encodes full-resolution CRF 20; oversized copies use a duration-based bitrate budget below 49 MB. The local original is never replaced. Higher quality uses more CPU and disk space.

本機保留擷取原尺寸，H.264 CRF 18、目標 10 FPS；不將低解析度畫面放大偽裝成高畫質。Telegram 優先 CRF 20，只有超過 49 MB 時才調整傳送副本碼率。原片不變；較高畫質會增加 CPU 與磁碟用量。
