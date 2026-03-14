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

The script will walk you through everything below automatically.

## Manual Setup

### 1. Create a Telegram bot

1. Open [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, pick a name and username
3. Copy the **bot token**

### 2. Get a Gemini API key

1. Go to [Google AI Studio](https://aistudio.google.com/apikey)
2. Create an API key and copy it

### 3. Set up Google Drive access

1. Go to [Google Cloud Console](https://console.cloud.google.com) and create a project (or use an existing one)
2. Enable the [Google Drive API](https://console.cloud.google.com/apis/library/drive.googleapis.com)
3. Go to [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent) → set to **External** → add your Google account as a **test user**
4. Go to [Credentials](https://console.cloud.google.com/apis/credentials) → **Create Credentials** → **OAuth client ID** → choose **Desktop app**
5. Copy the **Client ID** and **Client Secret**

### 4. Configure

Create a `.env` file in the project root:

```
TELEGRAM_BOT_TOKEN=your-bot-token
GEMINI_API_KEY=your-gemini-key
ADMIN_TELEGRAM_ID=your-telegram-user-id
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-your-client-secret
```

> To find your Telegram user ID, send `/start` to [@userinfobot](https://t.me/userinfobot).

### 5. Install and run

```bash
uv sync
uv run python -m scripts.auth_drive   # sign in to Google Drive
uv run python -m bot.main             # start the bot
```

On first start, send `/start` to the bot, then `/setup` to pick your root Drive folder.

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
    oauth.py        OAuth scopes and client config builder
  scripts/
    auth_drive.py   Google Drive authorization (auto + manual modes)
    deploy.sh       One-liner deploy script for Docker hosts
  .env              Secrets (not committed)
  state.json        Runtime state (not committed)
  token.json        Cached OAuth token (not committed)
  pyproject.toml    Project config & dependencies
  Dockerfile        Container image definition
```
