# Baseline / 基準版本

## English

A baseline is the version used as the reference for installation, testing and future changes. The current policy uses person-only polygon detection and normal 120–300-second clips. The first 120 seconds allow no automatic rollover or new Telegram sends; movement from age 120 and before 300 immediately starts the next clip and its own quiet period. A 120-second continuous no-movement stop still returns to idle until new activity.

Only completed videos are automatic Telegram notifications; recording starts keep local Windows alerts and evidence. The entire video queue, including older retries, waits during the current quiet period. Already-dispatched requests cannot be recalled. Manual/capture interruptions can leave shorter local partial clips without padding or automatic delivery before the applicable quiet deadline.

Runtime sources live in `src/`; public configuration examples live in `config/`. The installer and updater share the runtime deployment helper. Local Telegram credentials, logs, videos and backups are not part of the source baseline and must never be committed.

Offline geometry, preview, activity, recording/protocol, encoding and deployment tests help catch regressions. Successful offline tests do not certify the actual camera view, GPU, MuMu session or Telegram account; those need an installation-specific check. See the [changelog](../CHANGELOG.md) for behavior changes and [architecture](ARCHITECTURE.md) for the bounding-box movement limitation.

## 繁體中文

Baseline（基準版本）是後續安裝、測試及修改所依據的程式版本。目前規則為多邊形內人員偵測、正常每段 120–300 秒。前 120 秒不自動分段，也不啟動新 Telegram 傳送；滿 120 秒、未達 300 秒時移動會立即開始新片及新的靜默期。連續 120 秒無移動仍會結束並等待新的活動。

只有完成影片是 Telegram 自動通知，開始錄影保留本機 Windows 通知與存證。靜默期暫緩整個影片佇列，包括舊片重試；已送出的網路請求無法撤回。手動／擷取中斷可能留下更短的本機片段，不補畫面湊長度，也不在適用的靜默期限前自動傳送。

主要程式在 `src/`，公開設定範本在 `config/`，安裝與更新共用部署工具。本機 Telegram 憑證、執行紀錄、影片與備份不是程式基準的一部分，不可提交。

離線幾何、預覽、活動、錄影／協定、編碼及部署測試用來防止退步；通過離線測試不代表實際攝影機畫面、GPU、MuMu 或 Telegram 已驗收，仍需在安裝環境檢查。行為變更請見[更新記錄](../CHANGELOG.md)，移動判斷限制請見[架構說明](ARCHITECTURE.md)。
