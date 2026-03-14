# Drive Bot

A Telegram bot that receives scanned PDFs, uses Gemini AI to suggest the right folder and file name, and saves them to Google Drive.

## Features

- Send a PDF to the bot on Telegram
- Gemini analyzes the document and suggests a folder path + file name
- File names match the existing naming pattern in each folder
- Drill-down folder navigation if you want to change the suggestion
- Create new folders on the fly
- Duplicate detection with overwrite confirmation (bot-created files only)
- Read-only + create-only Drive permissions (cannot modify your existing files)

## Prerequisites

- Python 3.11+
- A Google Cloud project with Drive API and Generative Language API enabled
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

## Setup

### 1. Create a Telegram bot

1. Open Telegram and talk to [@BotFather](https://t.me/BotFather)
2. Send `/newbot`, pick a name and username
3. Copy the bot token

### 2. Google Cloud project

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a new project (or use an existing one)
3. Enable these APIs:
   - [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)
   - [Generative Language API](https://console.cloud.google.com/apis/library/generativelanguage.googleapis.com)

### 3. OAuth credentials (for Drive access)

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Choose **Desktop app** as the application type
4. Download the JSON file and save it as `credentials.json` in the project root

### 4. Gemini API key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create an API key
3. Copy it

### 5. Configure

Set the environment variables:

```bash
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
export GEMINI_API_KEY="your-gemini-api-key"
```

The `config.yaml` file is pre-configured with the root Drive folder ID. Edit it if your folder structure is different.

### 6. Install dependencies

```bash
uv sync
```

This creates a `.venv/` in the project root and installs everything. No manual venv activation needed.

### 7. Run the cleanup script (optional, one-time)

This scans your Drive and proposes file renames (adding `.pdf` extensions, standardizing bill names, etc.):

```bash
uv run python -m scripts.cleanup
```

Use `--dry-run` to preview changes without renaming.

### 8. Start the bot

```bash
uv run python -m bot.main
```

On first run, a browser window will open for Google Drive OAuth consent. After that, the refresh token is cached in `token.json`.

## How it works

1. You send a PDF to the bot on Telegram
2. The bot uploads the PDF to Gemini for analysis
3. Gemini suggests a folder path based on your Drive structure
4. The bot checks existing file names in the target folder
5. Gemini suggests a file name matching the existing pattern
6. You confirm, change the folder, rename, or create a new folder
7. The bot uploads the PDF to Google Drive and returns a link

## Safety

- **OAuth scopes**: `drive.readonly` (list folders/files) + `drive.file` (create only)
- The bot **cannot** delete, rename, move, or modify your existing files
- Duplicate files are detected — overwrites only apply to files the bot itself created
- Every upload is tagged with `appProperties` so the bot can identify its own files

## Project structure

```
drive-bot/
  bot/
    main.py         Entry point
    handlers.py     Telegram message/callback handlers
    drive.py        Google Drive API (list, upload, create folder)
    gemini.py       Gemini PDF analysis (folder + name suggestion)
    config.py       YAML config loader with env-var support
  scripts/
    cleanup.py      One-time Drive file rename tool
  config.yaml       Bot configuration
  pyproject.toml    Project config & dependencies
  credentials.json  OAuth credentials (not committed)
  token.json        Cached OAuth token (not committed)
```
