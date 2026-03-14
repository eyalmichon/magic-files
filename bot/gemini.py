"""Gemini PDF analysis — two-step folder + name suggestion.

Step 1: Analyse the PDF and pick the best folder path from the Drive tree.
Step 2: Given sibling file names in the target folder, suggest a name
        that matches the existing naming pattern.
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

from bot import config
from bot.drive import folder_tree_to_text, list_files, list_folder_tree

logger = logging.getLogger(__name__)

_client: genai.Client | None = None

FOLDER_SYSTEM_PROMPT = """\
You are a document filing assistant. The user will send you a scanned PDF and \
a folder tree. Your job is to decide which folder the document belongs in.

Rules:
- Pick the most specific (deepest) folder that fits.
- Return ONLY valid JSON — no markdown, no explanation.
- The "path" must be an ordered list of folder names from root to target.
- "confidence" is "high" if you are sure, "low" if uncertain.
- If nothing fits well, pick the closest match and set confidence to "low".

Response schema:
{"path": ["FolderA", "SubfolderB", ...], "confidence": "high"|"low"}
"""

NAME_SYSTEM_PROMPT = """\
You are a file-naming assistant. Given a short document summary and a list \
of existing file names in the same folder, suggest a new file name that \
matches the existing naming pattern exactly.

Rules:
- Mimic the style, language, date format, and structure of the siblings.
- If siblings use abbreviated months like "Aug-Sep 2025", do the same.
- If siblings use full month names like "April 2024", do the same.
- If siblings are in Hebrew, name in Hebrew. If English, name in English.
- Include .pdf extension ONLY if the siblings include it.
- Return ONLY valid JSON — no markdown, no explanation.
- If there are no siblings or no clear pattern, use: "YYYY-MM-DD <Description>.pdf"

Response schema:
{"name": "the suggested file name"}
"""


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.get("gemini_api_key"))
    return _client


def _model() -> str:
    return config.get("gemini_model")


async def analyze_folder(pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    """Step 1: determine target folder path and confidence.

    Returns {"path": [...], "confidence": "high"|"low"}.
    """
    client = _get_client()

    tree = list_folder_tree()
    tree_text = folder_tree_to_text(tree)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        uploaded = client.files.upload(file=tmp_path)

        response = client.models.generate_content(
            model=_model(),
            contents=[
                uploaded,
                f"Here is the folder tree:\n{tree_text}\n\n"
                f"Original file name: {filename}\n\n"
                "Analyze this document and pick the best folder path.",
            ],
            config=types.GenerateContentConfig(
                system_instruction=FOLDER_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        result = json.loads(response.text)
        logger.info("Gemini folder suggestion: %s", result)
        return result

    finally:
        Path(tmp_path).unlink(missing_ok=True)
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass


async def suggest_name(
    doc_summary: str,
    sibling_names: list[str],
) -> str:
    """Step 2: suggest a file name that matches sibling patterns.

    *doc_summary* is a short description of the document (from step 1 or
    extracted separately).  *sibling_names* are existing file names in the
    target folder.
    """
    client = _get_client()

    siblings_text = "\n".join(f"- {n}" for n in sibling_names) if sibling_names else "(empty folder)"

    response = client.models.generate_content(
        model=_model(),
        contents=[
            f"Document summary: {doc_summary}\n\n"
            f"Existing files in the target folder:\n{siblings_text}\n\n"
            "Suggest a file name for this new document.",
        ],
        config=types.GenerateContentConfig(
            system_instruction=NAME_SYSTEM_PROMPT,
            response_mime_type="application/json",
            temperature=0.1,
        ),
    )

    result = json.loads(response.text)
    logger.info("Gemini name suggestion: %s", result)
    return result.get("name", doc_summary)


async def analyze_pdf(pdf_bytes: bytes, filename: str) -> dict[str, Any]:
    """Full two-step analysis: folder path + file name.

    Returns {
        "path": ["Category", "Subcategory"],
        "confidence": "high",
        "suggested_name": "Jan-Feb 2026.pdf",
        "doc_summary": "Utility bill for January-February 2026",
    }
    """
    client = _get_client()

    tree = list_folder_tree()
    tree_text = folder_tree_to_text(tree)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    try:
        uploaded = client.files.upload(file=tmp_path)

        # Step 1 + summary in one call to save quota
        step1_prompt = (
            f"Here is the folder tree:\n{tree_text}\n\n"
            f"Original file name: {filename}\n\n"
            "Analyze this document. Return:\n"
            '1. "path": the best folder path as a list\n'
            '2. "confidence": "high" or "low"\n'
            '3. "doc_summary": a short one-line description of the document '
            "(language should match the document)\n\n"
            "Return ONLY valid JSON."
        )

        response = client.models.generate_content(
            model=_model(),
            contents=[uploaded, step1_prompt],
            config=types.GenerateContentConfig(
                system_instruction=FOLDER_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )

        step1 = json.loads(response.text)
        logger.info("Gemini step 1: %s", step1)

    finally:
        Path(tmp_path).unlink(missing_ok=True)
        try:
            client.files.delete(name=uploaded.name)
        except Exception:
            pass

    # Resolve folder and fetch siblings for step 2
    from bot.drive import resolve_path
    folder_id = resolve_path(step1.get("path", []), tree)

    sibling_names: list[str] = []
    if folder_id:
        sibling_names = list_files(folder_id)

    doc_summary = step1.get("doc_summary", filename)

    suggested_name = await suggest_name(doc_summary, sibling_names)

    return {
        "path": step1.get("path", []),
        "confidence": step1.get("confidence", "low"),
        "suggested_name": suggested_name,
        "doc_summary": doc_summary,
    }
