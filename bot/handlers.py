"""Telegram bot handlers — PDF receive, folder navigation, save flow."""
from __future__ import annotations

import html
import logging
from enum import IntEnum, auto

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from bot import drive, gemini
from bot.config import get_settings
from bot.state import get_state

logger = logging.getLogger(__name__)

BROWSE_FOLDERS = "browse_folders"


# ---------------------------------------------------------------------------
# Auth — reject messages from unknown users
# ---------------------------------------------------------------------------

def _is_first_run() -> bool:
    return not get_state().allowed_user_ids


def _is_authorized(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    allowed = get_state().allowed_user_ids
    if not allowed:
        admin_id = get_settings().admin_telegram_id
        if admin_id:
            return user.id == admin_id
        return True
    return user.id in allowed


async def _reject(update: Update) -> None:
    user = update.effective_user
    uid = user.id if user else "unknown"
    logger.warning("Unauthorized access attempt from user ID %s", uid)
    text = "This bot is private."
    if update.message:
        await update.message.reply_text(text)
    elif update.callback_query:
        await update.callback_query.answer(text, show_alert=True)


# ---------------------------------------------------------------------------
# Conversation states
# ---------------------------------------------------------------------------

class State(IntEnum):
    SUGGESTION = auto()
    BROWSE_FOLDER = auto()
    AWAIT_FOLDER_NAME = auto()
    AWAIT_FILE_NAME = auto()
    AWAIT_NAME_INPUT = auto()
    CONFIRM_OVERWRITE = auto()
    SETUP_PICK_ROOT = auto()


# ---------------------------------------------------------------------------
# User-data keys
# ---------------------------------------------------------------------------
PDF_BYTES = "pdf_bytes"
PDF_FILENAME = "pdf_filename"
SUGGESTED_PATH = "suggested_path"
SUGGESTED_NAME = "suggested_name"
SELECTED_FOLDER_ID = "selected_folder_id"
SELECTED_FOLDER_PATH = "selected_folder_path"
SELECTED_NAME = "selected_name"
BROWSE_STACK = "browse_stack"  # list of (folder_id, folder_name)
CONFIDENCE = "confidence"
DOC_SUMMARY = "doc_summary"
NAME_TEMPLATE = "name_template"
DUPLICATE_FILE_ID = "duplicate_file_id"


# ---------------------------------------------------------------------------
# Keyboard builders
# ---------------------------------------------------------------------------

def _suggestion_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Save", callback_data="save")],
        [
            InlineKeyboardButton("Change Folder", callback_data="change_folder"),
            InlineKeyboardButton("New Folder", callback_data="new_folder"),
        ],
        [InlineKeyboardButton("Rename", callback_data="rename")],
    ])


def _folder_keyboard(
    children: list[dict],
    context: ContextTypes.DEFAULT_TYPE,
    show_select_here: bool = True,
) -> InlineKeyboardMarkup:
    context.user_data[BROWSE_FOLDERS] = children
    rows: list[list[InlineKeyboardButton]] = []
    for i, child in enumerate(children):
        rows.append([InlineKeyboardButton(
            child["name"], callback_data=f"f:{i}",
        )])

    bottom_row: list[InlineKeyboardButton] = []
    if show_select_here:
        bottom_row.append(InlineKeyboardButton("Select This Folder", callback_data="select_here"))
    bottom_row.append(InlineKeyboardButton("Back", callback_data="back"))
    rows.append(bottom_row)
    return InlineKeyboardMarkup(rows)


def _overwrite_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Yes, overwrite", callback_data="overwrite_yes"),
            InlineKeyboardButton("No, save as copy", callback_data="overwrite_no"),
        ]
    ])


def _path_display(path: list[str], name: str) -> str:
    """Render folder path + file name as a vertical breadcrumb.

    Uses Unicode LTR marks (\\u200E) to force left-to-right rendering on each
    line, preventing Telegram's bidi algorithm from reordering mixed
    Hebrew/English content.
    """
    LTR = "\u200E"
    if not path:
        return f"{LTR}\U0001F4C4 {name}"
    lines = []
    for i, segment in enumerate(path):
        prefix = "\u2003" * i
        lines.append(f"{LTR}{prefix}\U0001F4C2 {segment}")
    prefix = "\u2003" * len(path)
    lines.append(f"{LTR}{prefix}\U0001F4C4 {name}")
    return "\n".join(lines)


def _unique_name(name: str, folder_id: str) -> str:
    """Append an incrementing suffix until no collision in *folder_id*."""
    base, _, ext = name.rpartition(".")
    if not (ext and base):
        base, ext = name, ""

    for n in range(2, 100):
        candidate = f"{base} ({n}).{ext}" if ext else f"{base} ({n})"
        exists, _, _ = drive.check_duplicate(candidate, folder_id)
        if not exists:
            return candidate
    return f"{base} (copy).{ext}" if ext else f"{base} (copy)"


# ---------------------------------------------------------------------------
# Entry: receive PDF
# ---------------------------------------------------------------------------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — user sends a PDF."""
    if not _is_authorized(update):
        await _reject(update)
        return ConversationHandler.END

    _cleanup_user_data(context)

    if not get_state().root_folder_id:
        await update.message.reply_text(
            "Root folder not set yet. Run /setup first."
        )
        return ConversationHandler.END

    doc = update.message.document
    if not doc or doc.mime_type != "application/pdf":
        await update.message.reply_text("Please send a PDF file.")
        return ConversationHandler.END

    max_size = get_settings().max_file_size_mb * 1024 * 1024
    if doc.file_size and doc.file_size > max_size:
        await update.message.reply_text(f"File too large (max {get_settings().max_file_size_mb} MB). Please send a smaller PDF.")
        return ConversationHandler.END

    await update.message.reply_text("Analyzing your document...")

    tg_file = await doc.get_file()
    pdf_bytes = await tg_file.download_as_bytearray()
    pdf_bytes = bytes(pdf_bytes)

    context.user_data[PDF_BYTES] = pdf_bytes
    context.user_data[PDF_FILENAME] = doc.file_name or "document.pdf"

    try:
        result = await gemini.analyze_pdf(pdf_bytes, context.user_data[PDF_FILENAME])
    except Exception:
        logger.exception("Gemini analysis failed")
        await update.message.reply_text(
            "Sorry, I couldn't analyze this document. You can still file it manually."
        )
        result = {
            "path": [],
            "confidence": "low",
            "suggested_name": context.user_data[PDF_FILENAME],
            "doc_summary": "",
        }

    path = result["path"]
    confidence = result["confidence"]
    suggested_name = result.get("suggested_name")
    needs_input = result.get("needs_input")
    name_template = result.get("name_template")

    tree = drive.list_folder_tree()
    folder_id = drive.resolve_path(path, tree)
    if folder_id is None:
        folder_id = get_state().root_folder_id
        path = []
        confidence = "low"

    context.user_data[SUGGESTED_PATH] = path
    context.user_data[SUGGESTED_NAME] = suggested_name
    context.user_data[SELECTED_FOLDER_ID] = folder_id
    context.user_data[SELECTED_FOLDER_PATH] = path
    context.user_data[SELECTED_NAME] = suggested_name
    context.user_data[CONFIDENCE] = confidence
    context.user_data[DOC_SUMMARY] = result.get("doc_summary", "")
    context.user_data[NAME_TEMPLATE] = name_template

    if needs_input and name_template:
        path_display = _path_display(path, name_template.replace("{input}", "___"))
        await update.message.reply_text(
            f"I found the folder but need one detail for the name:\n\n"
            f"{path_display}\n\n"
            f"\U00002753 {needs_input}"
        )
        return State.AWAIT_NAME_INPUT

    display_name = suggested_name or context.user_data[PDF_FILENAME]
    context.user_data[SELECTED_NAME] = display_name
    display = _path_display(path, display_name)
    if confidence == "low":
        msg = f"I'm not sure where this belongs. Best guess:\n\n{display}"
    else:
        msg = f"I'd save this as:\n\n{display}"

    await update.message.reply_text(msg, reply_markup=_suggestion_keyboard())
    return State.SUGGESTION


# ---------------------------------------------------------------------------
# SUGGESTION state — handle action buttons
# ---------------------------------------------------------------------------

async def handle_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped Save."""
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer("Unauthorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    folder_id = context.user_data[SELECTED_FOLDER_ID]
    name = context.user_data[SELECTED_NAME]
    pdf_bytes = context.user_data[PDF_BYTES]

    exists, is_ours, file_id = drive.check_duplicate(name, folder_id)

    if exists and is_ours:
        context.user_data[DUPLICATE_FILE_ID] = file_id
        safe_name = html.escape(name)
        await query.edit_message_text(
            f"A file named <b>{safe_name}</b> already exists (uploaded by this bot).\n\nOverwrite it?",
            reply_markup=_overwrite_keyboard(),
            parse_mode="HTML",
        )
        return State.CONFIRM_OVERWRITE

    if exists:
        name = _unique_name(name, folder_id)
        context.user_data[SELECTED_NAME] = name

    await query.edit_message_text("Uploading...")

    try:
        link = drive.upload_file(pdf_bytes, name, folder_id)
        path_str = _path_display(context.user_data[SELECTED_FOLDER_PATH], name)
        safe_link = html.escape(link)
        await query.edit_message_text(
            f"Saved!\n\n{path_str}\n\n<a href=\"{safe_link}\">Open in Drive</a>",
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Upload failed")
        await query.edit_message_text("Upload failed. Please try again.")

    _cleanup_user_data(context)
    return ConversationHandler.END


async def handle_change_folder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped Change Folder — start browsing from root."""
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer("Unauthorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    root_id = get_state().root_folder_id
    children = drive.get_children(root_id)
    context.user_data[BROWSE_STACK] = [(root_id, "Files")]

    if not children:
        await query.edit_message_text("No folders found.", reply_markup=_suggestion_keyboard())
        return State.SUGGESTION

    await query.edit_message_text(
        "Select a folder:\n\nFiles /",
        reply_markup=_folder_keyboard(children, context, show_select_here=False),
    )
    return State.BROWSE_FOLDER


async def handle_new_folder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped New Folder."""
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer("Unauthorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    path_parts = context.user_data.get(SELECTED_FOLDER_PATH, [])
    path_str = "\n".join(f"\u200E{'\u2003' * i}\U0001F4C2 {p}" for i, p in enumerate(path_parts)) if path_parts else "\u200E\U0001F4C2 Files"
    await query.edit_message_text(
        f"Type the new folder name (will be created inside):\n\n{path_str}"
    )
    return State.AWAIT_FOLDER_NAME


async def handle_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User tapped Rename."""
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer("Unauthorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    await query.edit_message_text("Type the new file name:")
    return State.AWAIT_FILE_NAME


# ---------------------------------------------------------------------------
# AWAIT_NAME_INPUT state — user provides missing info for the name
# ---------------------------------------------------------------------------

async def handle_name_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User typed the missing info (e.g. billing period) for the file name."""
    user_input = update.message.text.strip()
    template = context.user_data.get(NAME_TEMPLATE, "{input}.pdf")
    name = template.replace("{input}", user_input)

    context.user_data[SELECTED_NAME] = name
    path = context.user_data.get(SELECTED_FOLDER_PATH, [])
    display = _path_display(path, name)

    await update.message.reply_text(
        f"Got it! I'd save this as:\n\n{display}",
        reply_markup=_suggestion_keyboard(),
    )
    return State.SUGGESTION


async def _re_suggest_and_reply(
    query, context: ContextTypes.DEFAULT_TYPE,
    folder_id: str, path_names: list[str],
) -> int:
    """Re-run name suggestion for a newly selected folder and reply."""
    try:
        siblings = drive.list_files(folder_id)
        doc_summary = context.user_data.get(DOC_SUMMARY, "")
        if doc_summary:
            name_result = await gemini.suggest_name(doc_summary, siblings)
            if name_result.get("needs_input") and name_result.get("template"):
                context.user_data[NAME_TEMPLATE] = name_result["template"]
                path_display = _path_display(
                    path_names,
                    name_result["template"].replace("{input}", "___"),
                )
                await query.edit_message_text(
                    f"I found the folder but need one detail for the name:\n\n"
                    f"{path_display}\n\n"
                    f"\U00002753 {name_result['needs_input']}"
                )
                return State.AWAIT_NAME_INPUT
            context.user_data[SELECTED_NAME] = name_result.get("name") or context.user_data.get(SELECTED_NAME)
    except Exception:
        logger.exception("Name re-suggestion failed")

    name = context.user_data[SELECTED_NAME]
    display = _path_display(path_names, name)
    await query.edit_message_text(
        f"Updated:\n\n{display}",
        reply_markup=_suggestion_keyboard(),
    )
    return State.SUGGESTION


# ---------------------------------------------------------------------------
# BROWSE_FOLDER state
# ---------------------------------------------------------------------------

async def handle_folder_browse(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer("Unauthorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()
    data = query.data

    stack: list[tuple[str, str]] = context.user_data.get(BROWSE_STACK, [])

    if data == "back":
        if len(stack) > 1:
            stack.pop()
        parent_id, parent_name = stack[-1]
        children = drive.get_children(parent_id)
        breadcrumb = "\n".join(f"\u200E{'\u2003' * i}\U0001F4C2 {name}" for i, (_, name) in enumerate(stack))
        show_select = len(stack) > 1
        await query.edit_message_text(
            f"Select a folder:\n\n{breadcrumb}",
            reply_markup=_folder_keyboard(children, context, show_select_here=show_select),
        )
        return State.BROWSE_FOLDER

    if data == "select_here":
        folder_id, folder_name = stack[-1]
        path_names = [name for _, name in stack[1:]]
        context.user_data[SELECTED_FOLDER_ID] = folder_id
        context.user_data[SELECTED_FOLDER_PATH] = path_names

        return await _re_suggest_and_reply(query, context, folder_id, path_names)

    if data.startswith("f:"):
        idx = int(data.split(":")[1])
        folders = context.user_data.get(BROWSE_FOLDERS, [])
        if idx >= len(folders):
            return State.BROWSE_FOLDER
        folder_id = folders[idx]["id"]
        folder_name = folders[idx]["name"]

        stack.append((folder_id, folder_name))
        context.user_data[BROWSE_STACK] = stack

        children = drive.get_children(folder_id)
        breadcrumb = "\n".join(f"\u200E{'\u2003' * i}\U0001F4C2 {name}" for i, (_, name) in enumerate(stack))

        if not children:
            path_names = [name for _, name in stack[1:]]
            context.user_data[SELECTED_FOLDER_ID] = folder_id
            context.user_data[SELECTED_FOLDER_PATH] = path_names

            return await _re_suggest_and_reply(query, context, folder_id, path_names)

        await query.edit_message_text(
            f"Select a folder:\n\n{breadcrumb}",
            reply_markup=_folder_keyboard(children, context),
        )
        return State.BROWSE_FOLDER

    return State.BROWSE_FOLDER


# ---------------------------------------------------------------------------
# AWAIT_FOLDER_NAME state
# ---------------------------------------------------------------------------

async def handle_new_folder_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    folder_name = update.message.text.strip()
    if not folder_name:
        await update.message.reply_text("Folder name can't be empty. Try again:")
        return State.AWAIT_FOLDER_NAME

    parent_id = context.user_data[SELECTED_FOLDER_ID]
    try:
        new_id = drive.create_folder(folder_name, parent_id)
    except Exception:
        logger.exception("Folder creation failed")
        await update.message.reply_text("Failed to create folder. Try again:")
        return State.AWAIT_FOLDER_NAME

    path = list(context.user_data.get(SELECTED_FOLDER_PATH, []))
    path.append(folder_name)
    context.user_data[SELECTED_FOLDER_ID] = new_id
    context.user_data[SELECTED_FOLDER_PATH] = path

    name = context.user_data[SELECTED_NAME]
    display = _path_display(path, name)
    await update.message.reply_text(
        f"Folder created! Updated:\n\n{display}",
        reply_markup=_suggestion_keyboard(),
    )
    return State.SUGGESTION


# ---------------------------------------------------------------------------
# AWAIT_FILE_NAME state
# ---------------------------------------------------------------------------

async def handle_file_rename(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name = update.message.text.strip()
    if not new_name:
        await update.message.reply_text("Name can't be empty. Try again:")
        return State.AWAIT_FILE_NAME

    context.user_data[SELECTED_NAME] = new_name
    path = context.user_data.get(SELECTED_FOLDER_PATH, [])
    display = _path_display(path, new_name)
    await update.message.reply_text(
        f"Updated:\n\n{display}",
        reply_markup=_suggestion_keyboard(),
    )
    return State.SUGGESTION


# ---------------------------------------------------------------------------
# CONFIRM_OVERWRITE state
# ---------------------------------------------------------------------------

async def handle_overwrite(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if not _is_authorized(update):
        await query.answer("Unauthorized.", show_alert=True)
        return ConversationHandler.END
    await query.answer()

    name = context.user_data[SELECTED_NAME]
    folder_id = context.user_data[SELECTED_FOLDER_ID]
    pdf_bytes = context.user_data[PDF_BYTES]

    if query.data == "overwrite_yes":
        file_id = context.user_data.get(DUPLICATE_FILE_ID)
        await query.edit_message_text("Overwriting...")
        try:
            link = drive.upload_file(pdf_bytes, name, folder_id, overwrite_id=file_id)
            path_str = _path_display(context.user_data[SELECTED_FOLDER_PATH], name)
            safe_link = html.escape(link)
            await query.edit_message_text(
                f"Overwritten!\n\n{path_str}\n\n<a href=\"{safe_link}\">Open in Drive</a>",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Overwrite failed")
            await query.edit_message_text("Overwrite failed. Please try again.")
    else:
        new_name = _unique_name(name, folder_id)
        context.user_data[SELECTED_NAME] = new_name

        await query.edit_message_text("Uploading as copy...")
        try:
            link = drive.upload_file(pdf_bytes, new_name, folder_id)
            path_str = _path_display(context.user_data[SELECTED_FOLDER_PATH], new_name)
            safe_link = html.escape(link)
            await query.edit_message_text(
                f"Saved!\n\n{path_str}\n\n<a href=\"{safe_link}\">Open in Drive</a>",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Upload failed")
            await query.edit_message_text("Upload failed. Please try again.")

    _cleanup_user_data(context)
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Cancel / helpers
# ---------------------------------------------------------------------------

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _cleanup_user_data(context)
    if update.message:
        await update.message.reply_text("Cancelled.")
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Cancelled.")
    return ConversationHandler.END


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_authorized(update):
        await _reject(update)
        return

    user = update.effective_user

    if _is_first_run():
        state = get_state()
        state.allowed_user_ids = [user.id]
        state.save()
        logger.info("First-run: registered user %s (%s) as admin", user.id, user.full_name)

    root = get_state().root_folder_id
    if not root:
        await update.message.reply_text(
            "Almost ready! Run /setup to pick your root Drive folder."
        )
        return

    await update.message.reply_text(
        "Hi! Send me a PDF and I'll help you file it in Google Drive."
    )


async def setup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the root folder picker flow."""
    if not _is_authorized(update):
        await _reject(update)
        return ConversationHandler.END

    try:
        service = drive.get_service()
        resp = (
            service.files()
            .list(
                q="'root' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id, name)",
                orderBy="name",
                pageSize=50,
            )
            .execute()
        )
        folders = resp.get("files", [])
    except Exception:
        logger.exception("Failed to list root folders")
        await update.message.reply_text("Failed to access Drive. Check your credentials.")
        return ConversationHandler.END

    if not folders:
        await update.message.reply_text("No folders found in your Drive root.")
        return ConversationHandler.END

    context.user_data["setup_folders"] = folders
    rows = []
    for i, f in enumerate(folders):
        rows.append([InlineKeyboardButton(f"\u200E\U0001F4C2 {f['name']}", callback_data=f"sf:{i}")])

    await update.message.reply_text(
        "Pick the root folder where all your documents are stored:",
        reply_markup=InlineKeyboardMarkup(rows),
    )
    return State.SETUP_PICK_ROOT


async def handle_setup_pick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User picked a root folder during setup."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if not data.startswith("sf:"):
        return State.SETUP_PICK_ROOT

    idx = int(data.split(":")[1])
    folders = context.user_data.get("setup_folders", [])
    if idx >= len(folders):
        return State.SETUP_PICK_ROOT
    folder_id = folders[idx]["id"]
    folder_name = folders[idx]["name"]

    state = get_state()
    state.root_folder_id = folder_id
    state.save()
    drive.invalidate_cache()
    logger.info("Setup: root folder set to %s (%s)", folder_name, folder_id)

    await query.edit_message_text(
        f"\U00002705 Root folder set to: {folder_name}\n\n"
        "You're all set! Send me a PDF to get started."
    )
    return ConversationHandler.END


def _cleanup_user_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in (
        PDF_BYTES, PDF_FILENAME, SUGGESTED_PATH, SUGGESTED_NAME,
        SELECTED_FOLDER_ID, SELECTED_FOLDER_PATH, SELECTED_NAME,
        BROWSE_STACK, BROWSE_FOLDERS, CONFIDENCE, DOC_SUMMARY,
        NAME_TEMPLATE, DUPLICATE_FILE_ID,
    ):
        context.user_data.pop(key, None)


# ---------------------------------------------------------------------------
# Build the ConversationHandler
# ---------------------------------------------------------------------------

async def _timeout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Clean up user data when conversation times out."""
    _cleanup_user_data(context)
    return ConversationHandler.END


def build_setup_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("setup", setup_command),
        ],
        states={
            State.SETUP_PICK_ROOT: [
                CallbackQueryHandler(handle_setup_pick, pattern=r"^sf:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ],
    )


def build_conversation_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Document.PDF, handle_document),
        ],
        states={
            State.SUGGESTION: [
                CallbackQueryHandler(handle_save, pattern=r"^save$"),
                CallbackQueryHandler(handle_change_folder, pattern=r"^change_folder$"),
                CallbackQueryHandler(handle_new_folder, pattern=r"^new_folder$"),
                CallbackQueryHandler(handle_rename, pattern=r"^rename$"),
            ],
            State.BROWSE_FOLDER: [
                CallbackQueryHandler(handle_folder_browse),
            ],
            State.AWAIT_FOLDER_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_new_folder_name),
            ],
            State.AWAIT_FILE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_file_rename),
            ],
            State.AWAIT_NAME_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name_input),
            ],
            State.CONFIRM_OVERWRITE: [
                CallbackQueryHandler(handle_overwrite),
            ],
            ConversationHandler.TIMEOUT: [
                MessageHandler(filters.ALL, _timeout),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
        ],
        conversation_timeout=get_settings().conversation_timeout_sec,
        allow_reentry=True,
    )
