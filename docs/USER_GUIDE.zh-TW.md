# 使用指南（繁體中文）

## 1. 準備 Windows / MuMu

建議 Windows 11。監控帳號必須保持登入。安裝 MuMu，在 MuMu 裡安裝 SuperLive、登入帳號，並先打開真正要監控的攝影機即時畫面。

## 2. 安裝

在 Repo 根目錄執行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

Runtime 會部署到 `%USERPROFILE%\Downloads\SuperLive_AI`，不寫死任何 Windows 使用者名稱。

## 3. Telegram

執行 `installer\SET_TELEGRAM.bat`。Bot Token 只在本機輸入；依提示對 Bot 傳送 `test`，程式會抓真正的數字 Chat ID。憑證只存於 Runtime 的 `config\telegram.env`，GitHub 不會包含它。

## 4. 啟動前驗證

保持 MuMu / SuperLive 即時畫面開著，再執行 Verify。重點確認 MuMu Process、Capture 非黑畫面、Python Compile、Telegram（若啟用）以及錄影目錄可寫。

## 5. 日常啟停

只使用：

```text
Start_AI_Monitor.bat
Stop_AI_Monitor.bat
```

Start 會啟動 AI Watchdog 與 Recorder Watchdog。Stop 會建立正常停止旗標，避免 Watchdog 把你自己關掉的程式重新救回來。

## 6. 通知規則

預設：

- 第一次看到 Person / Vehicle：立即通知。
- 持續 MOVING：同類別最多每 5 分鐘一次。
- 持續 STILL：同類別每 10 分鐘一次。
- 完全消失 30 秒：Session Reset，下次出現立即通知。

要改時間可調整 Runtime 的 `config\notification_policy.json`。

## 7. 錄影

Recorder 錄的是 MuMu 原始監視器畫面，與 AI 紅綠框 Preview 分離。預設 10 FPS、每 30 分鐘一支 MP4、存 `C:\SuperLive_Recordings`，磁碟使用率達 80% 開始刪最舊錄影，降到 78% 停止刪除。

## 8. 常見問題

- **畫面全黑：** Windows Session 必須保持登入；真正 Logoff 會讓 GUI Capture 失去來源。
- **MuMu 找不到：** 先開 MuMu 與 SuperLive 即時畫面。
- **VM 裡 YOLO 慢：** VMware 3D acceleration 不等於 CUDA；CPU fallback 是正常狀態。
- **Telegram 失敗：** 檢查本機 `telegram.env`、網路與 Bot/Chat。
- **Recorder 重啟：** 查看 `logs\Stage5C\recorder_process.log` 與 `recorder_watchdog.log`。
