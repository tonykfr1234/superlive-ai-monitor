# Security Policy

## Never commit secrets

This repository must never contain real credentials or private runtime data, including:

- Telegram `BOT_TOKEN` or real `CHAT_ID`;
- OpenAI / ChatGPT / API keys;
- GitHub personal access tokens;
- usernames/passwords intended for private services;
- cookies, browser/session exports, authentication headers;
- `telegram.env`, `.env`, credential JSON files;
- logs, event images, recordings or private migration payloads.

Use `config/telegram.env.example` only as a template. Store the real file in the deployed runtime outside the repository.

## Before every push

Run:

```powershell
python .\scripts\security_scan.py .
```

The GitHub Actions workflow runs the same scanner on push and pull request.

## If a secret is exposed

1. Revoke/rotate it at the provider immediately.
2. Remove it from the current tree.
3. Rewrite Git history if it was committed; deleting the latest file is not enough.
4. Re-run the scanner before pushing again.

## Reporting

Please report security issues privately to the repository owner rather than publishing active credentials in an issue.
