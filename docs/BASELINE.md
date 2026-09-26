# Baseline / 基準版本

## English

A **baseline** is a known-good, verified version of the system that acts as the reference for future installation, testing and modification. In this project it means the monitor, notification policy, watchdog and recorder have passed the relevant functional gates.

A baseline is **not** a credential bundle and must never contain secrets. Earlier installers used a `baseline/` directory because reinstall copied a verified runtime set. The public repository uses `src/` for clearer open-source organization.

## 繁體中文

**Baseline（基準版本）**就是「已驗證 PASS、可以當作後續安裝、修改、比較與回歸測試基準的穩定版本」。

它不是單純備份，也不是放 Token / 密碼的秘密資料夾。公開 GitHub 版本把主要程式放在 `src/`，讓結構更直觀；發 Release 時再用版本號標示新的穩定 baseline。
