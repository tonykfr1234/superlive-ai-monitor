# 車牌辨識 / License plate recognition

[中文](#中文) · [English](#english)

## 中文

### 能做什麼

- 監看畫面中偵測到車輛時自動錄影，同時在 **完整 1920×1080 畫面** 上找車牌並辨識。
- 只在多張不同畫面都讀到同一個車牌時才確認（多幀共識），單張畫面的讀值不會直接推播。
- 偵測到人或車時，監看畫面會 **自動從低畫質切到高畫質一次**（最多重試 3 次），讓車牌有更多像素可讀；人車離開 60 秒後再回低畫質。
- 只接受 **台灣車牌格式**，並排除台灣車牌不使用的字母 I、O。
- 全部在本機 CPU 執行，不把影像或車牌送到任何外部辨識服務。

### 採用的技術

| 環節 | 做法 |
|---|---|
| 車牌偵測 | YOLOv9-t（384 輸入）車牌偵測模型（fast-alpr / open-image-models），整張畫面加四個重疊 60% 分塊掃描，遠處小車牌也有足夠像素 |
| 字元辨識 | CCT-S v2 global OCR（fast-plate-ocr） |
| 影像增強 | 對每個車牌裁切另做 Lanczos 3 倍放大、雙邊濾波去雜訊、CLAHE 對比、銳化，四種結果都辨識一次，不使用生成式補字 |
| 台灣格式檢查 | 汽車 ABC-1234、AB-1234、1234-AB；機車 ABC-123、123-ABC；拒絕 I、O 與不可能的組合 |
| 格式修正 | 只在格式確定某位置是字母或數字、而且只有一種格式成立時，修正常見混淆（字母位 8→B、5→S、2→Z、6→G；數字位 O/D/Q→0、I→1、B→8、S→5、Z→2、G→6）；原始讀值仍保存在證據中 |
| 多幀共識 | 同一段錄影中至少 3 張不同畫面、信心值 ≥ 0.85 才確認 |
| 畫質自動切換 | 人車事件時切到 SuperLive 最高畫質，每次事件只切一次，失敗最多 3 次 |
| 人工校正 | 本機確認過的車牌會影響之後的同車比對；資料只留在本機 |

### 實測結果（2026-11-03，本機實際錄影）

測試條件：16 段真實錄影（2026-10-28～11-02，時間 07:44～22:45，地下停車場固定照明，1920×1080、每秒 10 張），每段前 60 秒每 3 秒取一張，共 239 張畫面；Windows、CPU 推論，與正式監控同時執行。

| 偵測模型 | 找到車牌框的畫面 | 讀出有效台灣車牌的畫面 | 確認出車牌的影片 | 與先前確認結果一致 | 每張畫面耗時（2 執行緒） |
|---|---|---|---|---|---|
| **YOLOv9-t-384（採用）** | 75 / 239 | 57 / 239 | 4 / 16 | 4 / 4 | 平均 0.38 秒 |
| YOLOv9-s-608 | 128 / 239 | 58 / 239 | 4 / 16 | 4 / 4 | 平均 3.0 秒（最慢 5.8 秒） |
| YOLOv9-t-640 | 只測 2 段（35 張）：15 張可讀 | — | 1 / 2 | 1 / 1 | 平均 1.04 秒 |

結論：較大的 s-608 能多框出 70% 的車牌，但多出來的都太小或太糊讀不出字，可讀車牌幾乎相同，耗時卻是 8 倍，且會拖慢主監控，所以維持 t-384。真正影響可讀性的是畫面像素，因此本版的重點是：事件時切到高畫質、錄影與辨識一律使用完整畫面、加上台灣格式檢查。

說明：
- 「與先前確認結果一致」是和系統先前在同一段影片確認的車牌比較；目前只有 1 筆人工確認資料，**這不是人工標註的準確率**。
- 16 段影片中有 12 段沒有確認出車牌（車牌太小、角度、移動模糊或車輛未進入畫面），因此不能宣稱「每台車都能讀到」。
- 台灣格式檢查在這 16 段中沒有改變任何結果（沒有出現需要修正或排除的高信心讀值）；它是防止誤讀的保護，不是已量測到的提升。
- 測試用的影像與車牌號碼只留在本機，不上傳 GitHub。

### 已知限制

- 來源解析度：SuperLive 畫面是 1920×1080，遠處車牌常只有幾十個像素寬。
- 移動模糊、夜間反光與車燈眩光會讓字元讀不出來。
- 切到高畫質需要約 12～30 秒，車輛很快離開時可能還在低畫質。
- 只支援台灣車牌格式；其他格式的車牌會被排除。

## English

### What it does

- Records automatically when a vehicle appears and reads plates on the **full 1920×1080 camera frame**.
- A plate is confirmed only when several independent frames agree (multi-frame consensus); single-frame reads are never pushed.
- On a person/vehicle event the SuperLive view is **switched from low to high quality once** (at most 3 attempts), giving plates more pixels; it returns to low 60 s after the scene is empty.
- Only **Taiwan plate layouts** are accepted, and the letters I and O, which Taiwan does not issue, are rejected.
- Everything runs locally on the CPU; no image or plate is sent to an external recognition service.

### Techniques

| Stage | Method |
|---|---|
| Detection | YOLOv9-t (384 input) plate detector (fast-alpr / open-image-models) on the full frame plus four overlapping 60% tiles for small distant plates |
| OCR | CCT-S v2 global model (fast-plate-ocr) |
| Enhancement | Each crop is also read after 3× Lanczos upscaling, bilateral denoise, CLAHE and unsharp masking; no generative filling |
| Taiwan layouts | Cars ABC-1234, AB-1234, 1234-AB; motorcycles ABC-123, 123-ABC; I/O and impossible layouts rejected |
| Layout repair | Common confusions are fixed only where the layout forces a letter or digit and exactly one layout fits (letter slots 8→B, 5→S, 2→Z, 6→G; digit slots O/D/Q→0, I→1, B→8, S→5, Z→2, G→6); the raw reading stays in the evidence |
| Consensus | At least 3 distinct frames with confidence ≥ 0.85 within one recording |
| Quality switch | Highest SuperLive quality during person/vehicle events, once per event, at most 3 attempts |
| Manual review | Locally confirmed plates inform later matching; data stays local |

### Measured results (2026-11-03, real local recordings)

Conditions: 16 real clips (2026-10-28 to 11-02, 07:44–22:45, underground car park with constant artificial light, 1920×1080 at 10 fps); one frame every 3 s from the first 60 s of each clip, 239 frames; Windows, CPU inference, running alongside the live monitor.

| Detector | Frames with a plate box | Frames with a valid Taiwan read | Clips with a confirmed plate | Agrees with earlier confirmation | Time per frame (2 threads) |
|---|---|---|---|---|---|
| **YOLOv9-t-384 (used)** | 75 / 239 | 57 / 239 | 4 / 16 | 4 / 4 | 0.38 s mean |
| YOLOv9-s-608 | 128 / 239 | 58 / 239 | 4 / 16 | 4 / 4 | 3.0 s mean (5.8 s max) |
| YOLOv9-t-640 | 2 clips only (35 frames): 15 readable | — | 1 / 2 | 1 / 1 | 1.04 s mean |

The larger s-608 boxes 70% more plates, but the extra ones are too small or blurred to read: readable plates are essentially unchanged while each frame costs 8× more and slows the main monitor. t-384 is therefore kept. Pixel detail is what limits reading, so this release focuses on switching to high quality during events, always using the full frame for recording and recognition, and Taiwan layout checking.

Notes:
- "Agrees with earlier confirmation" compares with plates the system itself confirmed earlier for the same clip; only one human confirmation exists, so **this is not a human-labelled accuracy figure**.
- 12 of 16 clips produced no confirmed plate (too small, angle, motion blur, or the vehicle never entered the view); we do not claim every vehicle is read.
- Taiwan layout checking changed no result on these 16 clips; it is a safeguard against misreads, not a measured gain.
- Test images and plate numbers stay local and are never uploaded.

### Known limits

- Source resolution: the SuperLive view is 1920×1080; distant plates are often only a few dozen pixels wide.
- Motion blur, night reflections and headlight glare can make characters unreadable.
- Switching to high quality takes about 12–30 s; a vehicle that leaves quickly may still be filmed in low quality.
- Taiwan layouts only; plates in other formats are rejected.
