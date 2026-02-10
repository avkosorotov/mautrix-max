"""Media handling utilities for Max Messenger bridge."""

from __future__ import annotations

import mimetypes
from typing import Optional


# Max supported media types and size limits
MAX_PHOTO_SIZE = 50 * 1024 * 1024  # 50 MB
MAX_FILE_SIZE = 256 * 1024 * 1024  # 256 MB
MAX_VIDEO_SIZE = 256 * 1024 * 1024  # 256 MB

SUPPORTED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
SUPPORTED_VIDEO_TYPES = {"video/mp4", "video/quicktime", "video/webm"}
SUPPORTED_AUDIO_TYPES = {"audio/mpeg", "audio/ogg", "audio/opus", "audio/aac"}


def guess_mime_type(filename: str) -> str:
    """Guess MIME type from filename."""
    mime, _ = mimetypes.guess_type(filename)
    return mime or "application/octet-stream"


def get_max_attachment_type(mime_type: str, *, bot_api: bool = False) -> str:
    """Map a MIME type to a Max attachment type string.

    Bot API uses "image" instead of "photo" for image attachments.
    """
    if mime_type in SUPPORTED_IMAGE_TYPES:
        return "image" if bot_api else "photo"
    if mime_type in SUPPORTED_VIDEO_TYPES:
        return "video"
    if mime_type in SUPPORTED_AUDIO_TYPES:
        return "audio"
    return "file"


def check_file_size(data: bytes, mime_type: str) -> Optional[str]:
    """Check if file size is within Max limits. Returns error message or None."""
    size = len(data)
    if mime_type in SUPPORTED_IMAGE_TYPES and size > MAX_PHOTO_SIZE:
        return f"Photo too large: {size} bytes (max {MAX_PHOTO_SIZE})"
    if mime_type in SUPPORTED_VIDEO_TYPES and size > MAX_VIDEO_SIZE:
        return f"Video too large: {size} bytes (max {MAX_VIDEO_SIZE})"
    if size > MAX_FILE_SIZE:
        return f"File too large: {size} bytes (max {MAX_FILE_SIZE})"
    return None


def make_attachment(token: str, mime_type: str, filename: str = "", *, bot_api: bool = False) -> dict:
    """Create an attachment payload for sending.

    Bot API uses "image" for photos; User API uses "photo".
    """
    att_type = get_max_attachment_type(mime_type, bot_api=bot_api)
    if att_type in ("image", "photo"):
        return {"type": att_type, "payload": {"token": token}}
    if att_type == "video":
        return {"type": "video", "payload": {"token": token}}
    # file, audio, etc.
    result: dict = {"type": att_type, "payload": {"token": token}}
    if filename:
        result["filename"] = filename
    return result


def make_photo_attachment(token: str) -> dict:
    """Create a photo attachment payload for sending (Bot API format)."""
    return {"type": "image", "payload": {"token": token}}


def make_file_attachment(token: str, filename: str) -> dict:
    """Create a file attachment payload for sending."""
    return {
        "type": "file",
        "payload": {"token": token},
        "filename": filename,
    }
