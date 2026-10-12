<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <strong>繁體中文</strong> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <a href="README.es.md">Español</a> · <a href="README.fr.md">Français</a> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <a href="README.it.md">Italiano</a>
</p>

<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">SuperLive AI Monitor</h1>
<p align="center">選好多邊形，錄下人員活動，透過 Telegram 收到事件影片。</p>
<p align="center">
  <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows">
  <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a>
</p>
<p align="center">
  <a href="#quick-start">快速開始</a> · <a href="#workflow">運作流程</a> · <a href="#features">功能</a> · <a href="#demo">示範</a> · <a href="#faq">常見問題</a> · <a href="#documentation">文件</a>
</p>

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

---

新版圖示與高畫質錄影：本機保留擷取原尺寸，以 H.264 CRF 18 錄製；Telegram 優先使用 CRF 20，只有超過傳送限制時才壓縮副本，本機原片不變。

SuperLive AI Monitor 為 Windows 上 MuMu 裡的 SuperLive 即時畫面加上事件錄影。選出想監控的多邊形，YOLO 就會偵測範圍內的人員、錄製活動，並把完成的影片傳到 Telegram，不需要攝影機的 RTSP 網址。

本專案為非官方社群專案，與 SuperLive／MuMu 原廠無隸屬關係。

12 種 README 語言是文件翻譯，程式預覽介面目前使用繁體中文。

<a id="quick-start"></a>
## 快速開始

1. 安裝 MuMu 與 SuperLive，登入並開啟要監控的攝影機即時畫面，保持 Windows 使用者登入。
2. Clone／下載本 Repo，執行 `installer\INSTALL_FROM_REPO.bat` 或上方 PowerShell 指令。
3. 執行 `installer\SET_TELEGRAM.bat`，在本機設定自己的 Bot 與 Chat；儲存庫不附憑證。
4. 保持即時畫面開啟，執行 `installer\VERIFY_ALL.bat`。
5. 啟動 `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat`，停止使用同目錄的 `Stop_AI_Monitor.bat`。

**已有安裝？** 執行 `installer\UPDATE_FROM_REPO.bat`。更新器先備份並停止程式，保留本機區域、Telegram 與儲存設定；完成後維持停止，確認畫面再手動啟動。不建立 Windows 自動啟動、排程工作或服務。

<a id="workflow"></a>
## 運作流程

```text
多邊形內出現新的人員活動
  → 開始錄影＋本機 Windows 通知與快照
  → 前 120 秒持續偵測，不自動分段、不啟動新的 Telegram 傳送
  → 滿 120 秒、未達 300 秒時偵測到移動，立即結束舊片並開始新片
  → 每段新片重新開始 120 秒 Telegram 靜默期
  → 連續 120 秒無移動，或達 300 秒上限，結束並等待
  → 完成影片留在佇列，等目前靜默期結束後才能開始傳送
```

啟動後首次看到人員也視為新的活動。正常影片為 **120–300 秒**。未滿 120 秒時，移動只重設無移動計時，不自動分段；滿 120 秒後、達 300 秒上限前，偵測到移動就立即結束舊片並開始新片。無人與靜止都算無移動；連續 120 秒無人移動會結束錄影，等待再次移動或新人進入。

**Telegram 自動通知只傳送完成影片。** 開始錄影保留 Windows 通知與本機存證，不再傳送 Telegram 起始照片。每段前 120 秒不啟動新的 Telegram 傳送，連舊片與重試也一起延後；連續分段可能讓待傳影片持續等待。靜默期開始前已送出的網路請求仍可能在期間內抵達，無法撤回。

手動停止或擷取失敗可能留下未滿 120 秒的本機短片，不會補畫面偽造片長，也不會在該片靜默期限前自動傳送；若已有新片錄製，還需等新片目前的靜默期結束。

選取區域或擷取／偵測無有效結果時屬於未知，會重設觀察區間；恢復後需重新累計有效觀察時間，不直接當成無移動。

<a id="features"></a>
## 功能

| 功能 | 行為 |
| --- | --- |
| 多邊形偵測 | 支援凸形、凹形及舊矩形；人員框須有實際面積重疊才計入 |
| 攝影機內容 | 排除 MuMu 標題／選單列，保留影像內的時間與通道名稱 |
| 預覽操作 | 可點擊按鈕與狀態列位於影像下方 |
| 活動錄影 | 正常每段 120–300 秒；滿 120 秒後移動就換新片；連續兩分鐘無移動結束 |
| 通知 | 開始時只有本機 Windows 通知與存證；Telegram 只自動傳完成影片 |
| 影片傳送 | 實際長度 H.264 MP4；持久化佇列與重試；每段前 120 秒不啟動新傳送 |
| 自動復原 | 擷取重試、監控與錄影各有 Watchdog |
| 儲存 | 10 FPS；`C:\SuperLive_Recordings`；磁碟使用率 80% 開始刪除、78% 停止 |

移動判斷依人員框的位置／大小變化，不是姿態辨識或實體感測器。程式忽略輕微抖動並累積慢速位移，但攝影機移動、遮擋及辨識誤差仍可能影響結果。影片保留完整裁切後的攝影機內容，不裁成多邊形大小。

<a id="demo"></a>
## 示範

以下 36 秒無聲示範使用**程式實際預覽繪製功能、合成場景、預設人員框與模擬錄影狀態**，搭配中英文字幕解說。這不是實際攝影機錄影，也不是 YOLO 辨識準確度證明。影片將 **120 秒區間壓縮**，方便快速查看起始 Telegram 靜默期、移動換片及後續等待流程。

![合成的多邊形與活動錄影示範](docs/media/superlive-demo.gif)

[播放或下載 MP4 示範影片](docs/media/superlive-demo.mp4)

| 多邊形選取 | 區域內活動錄影 | 無移動結束與等待重啟 |
| --- | --- | --- |
| ![依序選取多邊形頂點](docs/media/polygon-selection.png) | ![區域內有人活動時錄影](docs/media/activity-recording.png) | ![無移動結束後等待再次移動](docs/media/inactivity-rearm.png) |

## 最新變更

正常影片調整為 120–300 秒；滿 120 秒後偵測到移動，立即開始新片並重啟 Telegram 靜默期。取消自動傳送起始照片，只有完成影片會加入傳送，靜默期間整個待傳佇列暫緩。保留多邊形偵測及連續兩分鐘無移動結束規則。詳見[更新記錄](CHANGELOG.md)。

<a id="controls"></a>
## 操作

| 操作 | 功能 |
| --- | --- |
| R／設定區域 | 開始設定多邊形 |
| 滑鼠左鍵 | 沿邊緣依序加入至少三個不同頂點 |
| 右鍵／Backspace | 移除最後一點 |
| Enter／儲存 | 封閉並儲存邊線不交叉的有效區域 |
| Esc／取消 | 保留原本區域 |
| C／全畫面 | 清除區域限制 |
| Q／停止 | 結束監控與目前影片 |

快捷鍵需要預覽視窗取得焦點。區域以攝影機內容區的相對座標儲存；若裁切後不能安全轉換舊座標，請重新選取或明確選擇全畫面。

<a id="faq"></a>
## 常見問題

**人還站在畫面裡，為什麼沒有重新錄影？** 因無移動而結束後，會等待人員再次移動或新人進入。

**每段影片多長？** 正常為 120–300 秒；滿 120 秒後移動會開始新片，無移動也可能在上限前結束。手動停止或擷取故障可能留下更短的本機片段，不會補畫面湊長度。

**可以在 VMware 執行嗎？** 支援 CPU 推論。MuMu 需要可用的巢狀虛擬化；VMware 圖形加速不提供 CUDA 運算。請維持互動式 Windows 登入與即時畫面。

**完成影片為什麼還沒收到？** 目前影片前 120 秒會暫緩所有新傳送，舊片重試也一樣；連續換片會延長等待。網路失敗也會保留佇列重試。靜默期開始前已送出的請求，仍可能在期間內抵達。

<a id="docs"></a>
<a id="documentation"></a>
## 文件

| 主題 | 文件 |
| --- | --- |
| 日常操作 | [繁中使用指南](docs/USER_GUIDE.zh-TW.md) · [English User Guide](docs/USER_GUIDE.md) |
| 程式設計 | [架構](docs/ARCHITECTURE.md) · [錄影設計](docs/RECORDING.md) |
| 安裝規劃 | [硬體](docs/HARDWARE.md) · [VMware](docs/VMWARE.md) |
| 版本與安全 | [基準版本](docs/BASELINE.md) · [更新記錄](CHANGELOG.md) · [資安說明](SECURITY.md) |

程式在 `src/`，公開設定範本在 `config/`，部署工具在 `installer/`。實際攝影機畫面與 Telegram 收件仍需在自己的環境驗收，離線測試無法代替這項確認。

## 資安

不要提交真實 Token／Chat ID、API Key、密碼、Session、本機設定、Logs、事件圖、錄影或私人備份。Push 前執行與 GitHub Actions 相同的掃描：

```powershell
python .\scripts\security_scan.py .
```

## 授權

[MIT](LICENSE)。
