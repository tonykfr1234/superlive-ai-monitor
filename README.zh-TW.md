# SuperLive AI Monitor

> Windows / VMware 長時間智慧監控：從 MuMu 裡的 SuperLive 即時畫面擷取影像，以 YOLO + ByteTrack 偵測人員與車輛，搭配 Windows / Telegram 通知、事件存證、循環錄影與 Watchdog 自動復原。

[English README](README.md) · [繁中使用指南](docs/USER_GUIDE.zh-TW.md) · [英文 User Guide](docs/USER_GUIDE.md) · [資安說明](SECURITY.md)

## 專案亮點

這個專案適合「攝影機只能透過 App / MuMu 看，但又想加上 AI 長時間監控」的情境：

- YOLO 偵測 **Person / Car / Motorcycle / Bus / Truck**。
- ByteTrack + 時序位移分析判斷 `MOVING` / `STILL`。
- 人或車第一次出現就通知。
- 持續移動：同類別最多 **5 分鐘一次**。
- 持續存在但不動：**10 分鐘一次**提醒。
- 消失 30 秒後重新出現：立即通知。
- Windows Notification + Telegram 事件 JPG。
- Event JPG + CSV 存證。
- MuMu 原始畫面 24/7 分段錄影。
- MSS / BitBlt 暫時失敗先自我修復；救不回來再交給 Watchdog 重啟。
- VMware 沒有 CUDA 時可使用 CPU fallback。

本專案為非官方社群專案，與 SuperLive / MuMu 原廠無隸屬關係。

## 錄影預設

```text
C:\SuperLive_Recordings
10 FPS
每 30 分鐘一支 MP4
每 60 秒檢查磁碟
C: 總使用率 >= 80% → 刪最舊的已關閉 MP4
降到 <= 78% → 停止刪除
```

注意：80% 是**整個磁碟總使用率**，不是錄影資料夾可以單獨吃到 80%。VMware thin-provisioned VMDK 還必須考慮 Host 真正剩餘容量。詳見 [錄影設計](docs/RECORDING.md)。

## Baseline 是什麼？

**Baseline = 已驗證、可當作後續修改與安裝基準的穩定版本。**

它不是「秘密設定檔」，也不等於單純備份。之前安裝包裡的 `baseline/` 是把已經測試 PASS 的 Monitor / Watchdog / Recorder 當成重灌基準；公開 GitHub 版把程式整理到 `src/`，比較符合一般開源專案結構。

## 快速安裝

先安裝 MuMu，並在 MuMu 內安裝/登入 SuperLive。Clone / Download 本 Repo 後執行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

安裝位置：`%USERPROFILE%\Downloads\SuperLive_AI`。

Telegram 請在自己的電腦本機執行 `installer\SET_TELEGRAM.bat`。真正 Token / Chat ID 只存於本機 `config\telegram.env`，這個檔案永遠不能 Commit / Push。

正式操作只有：

```text
Start_AI_Monitor.bat
Stop_AI_Monitor.bat
```

預設不建立 Windows 自動啟動、Task Scheduler 或 Windows Service。

## 建議硬體

### 實體 Windows

| 項目 | 最低實用 | 建議 24/7 |
|---|---|---|
| CPU | 現代 4C/8T | 6 核以上現代 CPU |
| RAM | 8 GB | 16–32 GB |
| GPU | CPU inference 可跑 | NVIDIA CUDA GPU 可提高 YOLO FPS |
| Storage | SSD | 獨立、高耐寫 SSD/NVMe |

### VMware

| 項目 | 建議起始值 |
|---|---|
| vCPU | 6 vCPU：`1 Processor × 6 Cores` |
| RAM | 8 GB 起；Host RAM 足夠建議 16 GB |
| 3D | 開啟 `Accelerate 3D graphics` |
| Graphics memory | 約 2 GB 對 GUI 工作負載通常足夠 |
| Nested VT | 開啟 `Virtualize Intel VT-x/EPT` |
| AI | 沒 NVIDIA passthrough 時 CPU fallback 是正常狀態 |

**VMware 3D acceleration ≠ CUDA passthrough。** 它可以幫 Windows / MuMu GUI rendering，但不會讓 PyTorch 把 VMware SVGA 3D 或 Intel Iris Xe 當成 CUDA GPU。

## 資安原則

公開 GitHub **絕對不能**包含 Telegram Bot Token / 真實 Chat ID、OpenAI / ChatGPT / API Token、GitHub Token、帳號密碼、Cookie、Session、`telegram.env` / `.env`、Logs、Events、錄影檔或私人 Migration Payload。

每次 Push 前執行：

```powershell
python .\scripts\security_scan.py .
```

GitHub Actions 也會執行同一套掃描。

## License

MIT License。
