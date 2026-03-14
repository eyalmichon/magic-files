# Magic Files

A Telegram bot that receives scanned PDFs, uses Gemini AI to suggest the right folder and file name, and saves them to Google Drive.

## Features

- Send a PDF to the bot on Telegram
- Gemini analyzes the document and suggests a folder path + file name
- File names match the existing naming pattern in each folder
- Asks for missing info (e.g. billing period) instead of guessing
- Drill-down folder navigation if you want to change the suggestion
- Create new folders on the fly
- Duplicate detection with overwrite confirmation (bot-created files only)
- Read-only + create-only Drive permissions (cannot modify your existing files)
- Mixed Hebrew/English text displays correctly in Telegram

## Quick Deploy (Docker host)

From the Docker host LXC console:

```bash
bash -c "$(wget -qLO - https://raw.githubusercontent.com/eyalmichon/magic-files/main/scripts/deploy.sh)"
```

The script will prompt for your Telegram bot token, Gemini API key, and admin Telegram ID, then walk you through Google Drive authorization.

## Manual Setup

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- A Gemini API key from [Google AI Studio](https://aistudio.google.com/apikey)

### 1. Install dependencies

```bash
uv sync
```

### 2. Configure

Create a `.env` file in the project root:

```
TELEGRAM_BOT_TOKEN=your-telegram-bot-token
GEMINI_API_KEY=your-gemini-api-key
ADMIN_TELEGRAM_ID=your-telegram-user-id
```

### 3. Authorize Google Drive

```bash
uv run python -m scripts.auth_drive
```

Choose **Auto** if you have a browser on the same machine, or **Manual** for remote/headless environments. The refresh token is saved to `token.json`.

### 4. Start the bot

```bash
uv run python -m bot.main
```

Send `/start` to the bot, then `/setup` to pick your root Drive folder.

## How it works

1. You send a PDF to the bot on Telegram
2. The bot uploads the PDF to Gemini for analysis
3. Gemini suggests a folder path based on your Drive structure
4. The bot checks existing file names in the target folder
5. Gemini suggests a file name matching the existing pattern
6. If info is missing (e.g. billing period), it asks you instead of guessing
7. You confirm, change the folder, rename, or create a new folder
8. The bot uploads the PDF to Google Drive and returns a link

## Safety

- **OAuth scopes**: `drive.readonly` (list folders/files) + `drive.file` (create only)
- The bot **cannot** delete, rename, move, or modify your existing files
- Duplicate files are detected — overwrites only apply to files the bot itself created
- Every upload is tagged with `appProperties` so the bot can identify its own files
- Only users listed in `state.json` (or matching `ADMIN_TELEGRAM_ID`) can use the bot

## Project structure

```
magic-files/
  bot/
    main.py         Entry point
    handlers.py     Telegram message/callback handlers
    drive.py        Google Drive API (list, upload, create folder)
    gemini.py       Gemini PDF analysis (folder + name suggestion)
    config.py       Settings from .env via pydantic-settings
    state.py        Runtime state (root folder, allowed users)
    oauth.py        OAuth client config and scopes
  scripts/
    auth_drive.py   Google Drive authorization (auto + manual modes)
    deploy.sh       One-liner deploy script for Docker hosts
  .env              Secrets (not committed)
  state.json        Runtime state (not committed)
  token.json        Cached OAuth token (not committed)
  pyproject.toml    Project config & dependencies
  Dockerfile        Container image definition
```
