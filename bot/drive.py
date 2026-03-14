"""Google Drive integration — read-only listing + create-only uploads.

Safety guarantees:
  - OAuth scopes: drive.readonly (list anything) + drive.file (create only)
  - No delete / rename / move / update helpers exist in this module
  - Duplicate uploads are detected; overwrite only for bot-created files
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import google.auth
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload

from bot.oauth import SCOPES, get_client_config
from bot.state import get_state

logger = logging.getLogger(__name__)

APP_PROPERTY_KEY = "uploaded_by"
APP_PROPERTY_VAL = "magic-files"

_FOLDER_MIME = "application/vnd.google-apps.folder"
_MAX_TREE_DEPTH = 5

_service = None
_credentials = None

# In-memory cache: {parent_id: (timestamp, tree)}
_folder_cache: dict[str, tuple[float, list[dict]]] = {}
_CACHE_TTL = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Auth — tries service account, then ADC, then OAuth flow
# ---------------------------------------------------------------------------

def _get_credentials():
    base = Path(__file__).resolve().parent.parent
    sa_path = base / "secrets" / "adc.json"
    if not sa_path.exists():
        sa_path = base / "service-account.json"

    if sa_path.exists():
        try:
            creds = service_account.Credentials.from_service_account_file(
                str(sa_path), scopes=SCOPES,
            )
            logger.info("Using service account credentials (%s)", sa_path.name)
            return creds
        except Exception:
            logger.debug("Service account auth failed", exc_info=True)

    try:
        creds, _ = google.auth.default(scopes=SCOPES)
        creds.refresh(Request())
        logger.info("Using Application Default Credentials (gcloud)")
        return creds
    except Exception:
        logger.debug("ADC auth failed", exc_info=True)

    token_path = base / "token.json"

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            logger.debug("Failed to load saved token", exc_info=True)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_config(get_client_config(), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=False)
        token_path.write_text(creds.to_json())

    return creds


def get_service():
    global _service, _credentials
    if _service is not None and _credentials is not None:
        if hasattr(_credentials, "expired") and _credentials.expired:
            if hasattr(_credentials, "refresh_token") and _credentials.refresh_token:
                try:
                    _credentials.refresh(Request())
                    return _service
                except Exception:
                    logger.debug("Credential refresh failed, rebuilding service", exc_info=True)
            _service = None
            _credentials = None

    if _service is None:
        _credentials = _get_credentials()
        _service = build("drive", "v3", credentials=_credentials)
    return _service


# ---------------------------------------------------------------------------
# Folder listing (recursive, cached)
# ---------------------------------------------------------------------------

def _list_children_folders(parent_id: str) -> list[dict]:
    """Return immediate child folders of *parent_id*."""
    service = get_service()
    q = (
        f"'{parent_id}' in parents "
        f"and mimeType='{_FOLDER_MIME}' "
        "and trashed=false"
    )
    results: list[dict] = []
    page_token: str | None = None
    while True:
        resp = (
            service.files()
            .list(q=q, fields="nextPageToken, files(id, name)", pageSize=100,
                  orderBy="name", pageToken=page_token)
            .execute()
        )
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def list_folder_tree(
    parent_id: str | None = None,
    *,
    force: bool = False,
    max_depth: int = _MAX_TREE_DEPTH,
) -> list[dict]:
    """Recursively list the folder tree under *parent_id*.

    Returns a list of dicts:
        [{"id": "...", "name": "...", "children": [...]}, ...]
    """
    if parent_id is None:
        parent_id = get_state().root_folder_id

    now = time.time()
    if not force and parent_id in _folder_cache:
        ts, cached = _folder_cache[parent_id]
        if now - ts < _CACHE_TTL:
            return cached

    if max_depth <= 0:
        return []

    children = _list_children_folders(parent_id)
    tree = []
    for child in children:
        subtree = list_folder_tree(child["id"], force=force, max_depth=max_depth - 1)
        tree.append({
            "id": child["id"],
            "name": child["name"],
            "children": subtree,
        })

    _folder_cache[parent_id] = (now, tree)
    return tree


def get_children(parent_id: str | None = None) -> list[dict]:
    """Return immediate child folders (id + name) of *parent_id*.

    Uses the tree cache when available to avoid redundant API calls.
    """
    if parent_id is None:
        parent_id = get_state().root_folder_id

    now = time.time()
    if parent_id in _folder_cache:
        ts, cached = _folder_cache[parent_id]
        if now - ts < _CACHE_TTL:
            return [{"id": n["id"], "name": n["name"]} for n in cached]

    return _list_children_folders(parent_id)


def invalidate_cache() -> None:
    _folder_cache.clear()


# ---------------------------------------------------------------------------
# File listing
# ---------------------------------------------------------------------------

def list_files(folder_id: str) -> list[str]:
    """Return file names (non-folder) inside *folder_id*, sorted by name."""
    service = get_service()
    q = (
        f"'{folder_id}' in parents "
        f"and mimeType!='{_FOLDER_MIME}' "
        "and trashed=false"
    )
    results: list[str] = []
    page_token: str | None = None
    while True:
        resp = (
            service.files()
            .list(q=q, fields="nextPageToken, files(name)", pageSize=200,
                  orderBy="name", pageToken=page_token)
            .execute()
        )
        results.extend(f["name"] for f in resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


# ---------------------------------------------------------------------------
# Folder creation
# ---------------------------------------------------------------------------

def create_folder(name: str, parent_id: str) -> str:
    """Create a subfolder and return its ID. Invalidates the folder cache."""
    service = get_service()
    metadata = {
        "name": name,
        "mimeType": _FOLDER_MIME,
        "parents": [parent_id],
    }
    folder = service.files().create(body=metadata, fields="id").execute()
    invalidate_cache()
    return folder["id"]


# ---------------------------------------------------------------------------
# Duplicate detection
# ---------------------------------------------------------------------------

def check_duplicate(name: str, folder_id: str) -> tuple[bool, bool, str | None]:
    """Check if *name* already exists in *folder_id*.

    Returns (exists, is_ours, file_id):
      - exists:  True if a file with this name is in the folder
      - is_ours: True if the file was uploaded by this bot (safe to overwrite)
      - file_id: the Drive file ID (or None)
    """
    service = get_service()
    q = (
        f"'{folder_id}' in parents "
        f"and name='{_escape(name)}' "
        f"and mimeType!='{_FOLDER_MIME}' "
        "and trashed=false"
    )
    resp = (
        service.files()
        .list(q=q, fields="files(id, appProperties)", pageSize=1)
        .execute()
    )
    files = resp.get("files", [])
    if not files:
        return False, False, None

    f = files[0]
    props = f.get("appProperties", {})
    is_ours = props.get(APP_PROPERTY_KEY) == APP_PROPERTY_VAL
    return True, is_ours, f["id"]


# ---------------------------------------------------------------------------
# File upload
# ---------------------------------------------------------------------------

def upload_file(
    file_bytes: bytes,
    name: str,
    folder_id: str,
    mime_type: str = "application/pdf",
    overwrite_id: str | None = None,
) -> str:
    """Upload a file and return its web-view link.

    If *overwrite_id* is provided, updates that file instead of creating new.
    """
    service = get_service()
    media = MediaInMemoryUpload(file_bytes, mimetype=mime_type, resumable=True)

    if overwrite_id:
        updated = (
            service.files()
            .update(fileId=overwrite_id, media_body=media, fields="webViewLink")
            .execute()
        )
        return updated["webViewLink"]

    metadata: dict[str, Any] = {
        "name": name,
        "parents": [folder_id],
        "appProperties": {APP_PROPERTY_KEY: APP_PROPERTY_VAL},
    }
    created = (
        service.files()
        .create(body=metadata, media_body=media, fields="webViewLink")
        .execute()
    )
    return created["webViewLink"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def folder_tree_to_text(tree: list[dict], indent: int = 0) -> str:
    """Render folder tree as indented text for Gemini prompts."""
    lines: list[str] = []
    for node in tree:
        lines.append("  " * indent + "- " + node["name"])
        if node.get("children"):
            lines.append(folder_tree_to_text(node["children"], indent + 1))
    return "\n".join(lines)


def resolve_path(path: list[str], tree: list[dict] | None = None) -> str | None:
    """Walk a path like ["Category", "Subcategory"] and return the folder ID."""
    if tree is None:
        tree = list_folder_tree()
    if not path:
        return get_state().root_folder_id

    name = path[0]
    for node in tree:
        if node["name"] == name:
            if len(path) == 1:
                return node["id"]
            return resolve_path(path[1:], node.get("children", []))
    return None


def _escape(s: str) -> str:
    """Escape single quotes for Drive API query strings."""
    return s.replace("\\", "\\\\").replace("'", "\\'")
