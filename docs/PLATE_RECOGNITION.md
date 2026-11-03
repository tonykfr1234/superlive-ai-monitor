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
| 車牌偵測 | YOLOv9-t（384 輸入）車牌偵測模型（fast-alpr / open-image-models），整張畫面加四個重疊 60% 分塊掃描，再對主監控 YOLO 找到的最大 3 台車（外擴 15%）各掃一次，車牌在偵測器輸入中放大數倍 |
| 字元辨識 | CCT-S v2 global OCR（fast-plate-ocr），車牌框外擴 15% 再辨識，避免切到邊緣字元 |
| 影像增強 | 對每個車牌裁切另做 Lanczos 3 倍放大、雙邊濾波去雜訊、CLAHE 對比、銳化，四種結果都辨識一次，不使用生成式補字 |
| 切換畫質時持續辨識 | 切到高畫質的 12～44 秒內，車牌程序照常使用未遮罩的完整畫面（車通常正好在這段時間經過） |
| 台灣格式檢查 | 汽車 ABC-1234、AB-1234、1234-AB；機車 ABC-123、123-ABC；拒絕 I、O 與不可能的組合 |
| 格式修正 | 只在格式確定某位置是字母或數字、而且只有一種格式成立時，修正常見混淆（字母位 8→B、5→S、2→Z、6→G；數字位 O/D/Q→0、I→1、B→8、S→5、Z→2、G→6）；原始讀值仍保存在證據中 |
| 多幀共識 | 同一段錄影中至少 3 張不同畫面、信心值 ≥ 0.85 才確認 |
| 字元投票共識 | 小車牌每張畫面常錯一個不同的字：同一個移動中的車牌至少 3 張畫面逐字投票（依每個字元的 OCR 機率加權），投出的車牌須為有效台灣格式、至少 2 張畫面逐字讀到、每個位置勝出比例 > 50% 且累積機率 ≥ 1.5 |
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

### 修正後實測（2026-11-03 晚，當天 17 段錄影）

正式監控當天 16 段錄影全部記為「未辨識到車牌」。在同一批真實畫面上比較（15 段有車的影片、278 張含車畫面、每 2 秒一張，約為正式程序取樣率的一半）：

| | 修正前 | 修正後 |
|---|---|---|
| 確認出的車牌 | 0 | **2（皆正確，0 錯誤）** |
| 讀到有效台灣格式的畫面 | 15 | 23 |
| 每張畫面耗時（CPU 滿載下） | 1.86 秒 | 3.56 秒 |

- 單看 OCR：14 張肉眼可讀的真實車牌圖（45～83 像素寬），舊做法完全讀對 0 張，車牌框外擴 15% 後 3 張。
- 只有 1 台車有人工確認的答案，樣本很小，**不是準確率**；另一筆由 3 張畫面逐字一致確認，車牌圖肉眼判讀相同。
- 很多「沒有車牌」是正確的：遠處機車（約 80 像素寬）或畫面邊緣的停放車輛，任何畫質都讀不出來。
- 以上是錄影離線重播；正式監控中的效果要等下一次真實車輛事件確認。

### 已知限制

- 來源解析度：SuperLive 畫面是 1920×1080，遠處車牌常只有幾十個像素寬；45 像素以下的車牌目前讀不出來。
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
| Detection | YOLOv9-t (384 input) plate detector (fast-alpr / open-image-models) on the full frame, four overlapping 60% tiles, and a 15%-padded crop of each of the 3 largest vehicles found by the monitor's YOLO, which enlarges the plate several times in the detector input |
| OCR | CCT-S v2 global model (fast-plate-ocr) on the plate box padded by 15%, so edge characters are not clipped |
| Reading during quality switch | During the 12–44 s switch to high quality, plate reading continues on the unmasked full frame (vehicles usually pass during exactly this time) |
| Enhancement | Each crop is also read after 3× Lanczos upscaling, bilateral denoise, CLAHE and unsharp masking; no generative filling |
| Taiwan layouts | Cars ABC-1234, AB-1234, 1234-AB; motorcycles ABC-123, 123-ABC; I/O and impossible layouts rejected |
| Layout repair | Common confusions are fixed only where the layout forces a letter or digit and exactly one layout fits (letter slots 8→B, 5→S, 2→Z, 6→G; digit slots O/D/Q→0, I→1, B→8, S→5, Z→2, G→6); the raw reading stays in the evidence |
| Consensus | At least 3 distinct frames with confidence ≥ 0.85 within one recording |
| Character vote | Small plates are often read with a different wrong character per frame: at least 3 frames of one moving plate vote per character, weighted by each character's OCR probability. The voted plate must be a valid Taiwan layout, read verbatim in at least 2 frames, win each position by more than 50%, and reach a summed probability of 1.5 per position |
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

### After the fix (evening of 2026-11-03, that day's 17 clips)

In live monitoring all 16 clips of the day were recorded as "no plate recognized". Comparison on the same real frames (15 clips with vehicles, 278 vehicle frames, one every 2 s, about half the live worker's sampling rate):

| | Before | After |
|---|---|---|
| Plates confirmed | 0 | **2 (both correct, 0 wrong)** |
| Frames with a valid Taiwan read | 15 | 23 |
| Time per frame (CPU fully loaded) | 1.86 s | 3.56 s |

- OCR alone: on 14 real, human-readable plate crops (45–83 px wide) the old crop read 0 exactly; padding the box by 15% read 3.
- Only one vehicle has a human-verified answer, so this is a small sample and **not an accuracy figure**; the other confirmation agreed verbatim across 3 frames and matches the plate as read by eye.
- Many "no plate" results are correct: a distant scooter (~80 px wide) or a parked car at the frame edge cannot be read at any quality.
- These are offline replays of recordings; the effect in live monitoring will be confirmed by the next real vehicle event.

### Known limits

- Source resolution: the SuperLive view is 1920×1080; distant plates are often only a few dozen pixels wide, and plates under about 45 px are not read.
- Motion blur, night reflections and headlight glare can make characters unreadable.
- Switching to high quality takes about 12–30 s; a vehicle that leaves quickly may still be filmed in low quality.
- Taiwan layouts only; plates in other formats are rejected.
