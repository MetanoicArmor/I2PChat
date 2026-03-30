"""
Pure validation/routing module for drag-and-drop file/image handling.

Qt layer in main_qt.py wires dragEnterEvent / dropEvent; this module is
intentionally UI-free so tests can run without PyQt6.
"""

from __future__ import annotations

import os
from typing import Sequence

# DropAction literals
SEND_FILE = "send_file"
SEND_IMAGE = "send_image"
REJECT = "reject"

DropAction = str  # one of the three constants above

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})

# 100 MB default limit
_MAX_FILE_SIZE = 100 * 1024 * 1024


def _is_image_path(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in _IMAGE_EXTENSIONS


def classify_drop(mime_types: Sequence[str], urls: Sequence[str]) -> DropAction:
    """Return the appropriate DropAction for a drop event.

    *mime_types* are the MIME type strings from the drop event's mimeData.
    *urls* are the local file paths (already decoded from QUrl).

    Rules:
    - If no local file URLs are present → reject.
    - If the single dropped file has an image extension → send_image.
    - Otherwise → send_file.
    - Multiple files → reject (not supported).
    """
    local_paths = [u for u in urls if u]
    if not local_paths:
        return REJECT
    if len(local_paths) > 1:
        return REJECT
    path = local_paths[0]
    if _is_image_path(path):
        return SEND_IMAGE
    return SEND_FILE


def validate_drop_file(path: str) -> tuple[bool, str]:
    """Return (ok, reason) for a candidate drop file path.

    Checks: existence, read permission, size limit.
    """
    if not path:
        return False, "No file path provided"
    if not os.path.exists(path):
        return False, f"File not found: {path}"
    if not os.path.isfile(path):
        return False, "Only regular files are supported"
    if not os.access(path, os.R_OK):
        return False, "File is not readable"
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        return False, f"Cannot read file size: {exc}"
    if size > _MAX_FILE_SIZE:
        mb = _MAX_FILE_SIZE // (1024 * 1024)
        return False, f"File exceeds {mb} MB limit"
    if size == 0:
        return False, "File is empty"
    return True, ""


def _validate_image_with_pillow(path: str) -> tuple[bool, str]:
    """Optional Pillow-based image validation (best-effort)."""
    try:
        from PIL import Image, UnidentifiedImageError  # type: ignore
        try:
            with Image.open(path) as img:
                img.verify()
            return True, ""
        except (UnidentifiedImageError, Exception) as exc:
            return False, f"Invalid image: {exc}"
    except ImportError:
        return True, ""  # Pillow not available — skip deep check


def validate_drop_image(path: str) -> tuple[bool, str]:
    """Validate a dropped image file (extension check + optional Pillow check)."""
    ok, reason = validate_drop_file(path)
    if not ok:
        return ok, reason
    if not _is_image_path(path):
        _, ext = os.path.splitext(path)
        supported = ", ".join(sorted(_IMAGE_EXTENSIONS))
        return False, f"Unsupported image type '{ext}'. Supported: {supported}"
    return _validate_image_with_pillow(path)
