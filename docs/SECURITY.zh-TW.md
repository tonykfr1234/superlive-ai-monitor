# 資安規範

公開 Repo 永遠不能放真實 Telegram Token / Chat ID、OpenAI/ChatGPT/API Token、GitHub PAT、帳號密碼、Cookie、Session、`telegram.env`、`.env`、Logs、Event JPG、錄影檔或私人 Migration Payload。

每次 Push 前先執行：

```powershell
python .\scripts\security_scan.py .
```

若 Token 曾被 Commit，不能只刪最新版檔案；必須先到服務端撤銷/Rotate，再清理 Git History。
