"""Convert Max messages to Matrix message format."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from mautrix.types import (
    EventType,
    Format,
    ImageInfo,
    MediaMessageEventContent,
    MessageType,
    TextMessageEventContent,
)

if TYPE_CHECKING:
    from ..max.base_client import BaseMaxClient
    from ..max.types import MaxAttachment, MaxMessage

logger = logging.getLogger("mau.fmt.from_max")


class MaxMessageConverter:
    """Convert Max messages to Matrix events."""

    @staticmethod
    async def convert(
        message: MaxMessage,
        max_client: Optional[BaseMaxClient] = None,
        upload_fn=None,
    ) -> list[dict]:
        """Convert a Max message to one or more Matrix event contents.

        Returns a list of event content dicts ready for sending via intent.
        A single Max message can produce multiple Matrix events (text + attachments).
        """
        events = []

        # Text content
        text = message.text
        if text:
            content = TextMessageEventContent(
                msgtype=MessageType.TEXT,
                body=text,
            )
            # Simple HTML conversion: newlines to <br>
            if "\n" in text:
                content.format = Format.HTML
                content.formatted_body = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
            events.append({"type": "m.room.message", "content": content})

        # Attachments
        for attachment in message.attachments:
            event = await MaxMessageConverter._convert_attachment(
                attachment, max_client, upload_fn
            )
            if event:
                events.append(event)

        return events

    @staticmethod
    async def _convert_attachment(
        attachment: MaxAttachment,
        max_client: Optional[BaseMaxClient],
        upload_fn,
    ) -> Optional[dict]:
        """Convert a single Max attachment to a Matrix event content."""
        from ..max.types import AttachmentType

        if attachment.type == AttachmentType.PHOTO:
            url = attachment.best_photo_url
            if not url:
                return None
            if max_client and upload_fn:
                try:
                    data = await max_client.download_media(url)
                    mxc_url = await upload_fn(data, "photo.jpg", "image/jpeg")
                    content = MediaMessageEventContent(
                        msgtype=MessageType.IMAGE,
                        body="photo.jpg",
                        url=mxc_url,
                        info=ImageInfo(
                            mimetype="image/jpeg",
                            size=len(data),
                        ),
                    )
                    return {"type": "m.room.message", "content": content}
                except Exception:
                    logger.exception("Failed to bridge photo")
            # Fallback: send URL as text
            return {
                "type": "m.room.message",
                "content": TextMessageEventContent(
                    msgtype=MessageType.TEXT,
                    body=f"[Photo: {url}]",
                ),
            }

        elif attachment.type == AttachmentType.FILE:
            url = attachment.url
            if not url:
                return None
            filename = attachment.filename or "file"
            if max_client and upload_fn:
                try:
                    data = await max_client.download_media(url)
                    mxc_url = await upload_fn(
                        data, filename, attachment.mime_type or "application/octet-stream"
                    )
                    content = MediaMessageEventContent(
                        msgtype=MessageType.FILE,
                        body=filename,
                        url=mxc_url,
                    )
                    return {"type": "m.room.message", "content": content}
                except Exception:
                    logger.exception("Failed to bridge file")
            return {
                "type": "m.room.message",
                "content": TextMessageEventContent(
                    msgtype=MessageType.TEXT,
                    body=f"[File: {filename}]",
                ),
            }

        elif attachment.type == AttachmentType.STICKER:
            # Stickers map to m.sticker event type
            url = attachment.url or (attachment.best_photo_url if hasattr(attachment, 'best_photo_url') else None)
            if url and max_client and upload_fn:
                try:
                    data = await max_client.download_media(url)
                    mxc_url = await upload_fn(data, "sticker.webp", "image/webp")
                    content = MediaMessageEventContent(
                        msgtype=MessageType.IMAGE,
                        body="sticker",
                        url=mxc_url,
                        info=ImageInfo(mimetype="image/webp", size=len(data)),
                    )
                    return {"type": "m.sticker", "content": content}
                except Exception:
                    logger.exception("Failed to bridge sticker")
            return None

        elif attachment.type in (AttachmentType.VIDEO, AttachmentType.VOICE, AttachmentType.AUDIO):
            url = attachment.url
            if not url:
                return None
            msgtype = MessageType.VIDEO if attachment.type == AttachmentType.VIDEO else MessageType.AUDIO
            filename = attachment.filename or f"{attachment.type.value}.bin"
            if max_client and upload_fn:
                try:
                    data = await max_client.download_media(url)
                    mxc_url = await upload_fn(
                        data, filename, attachment.mime_type or "application/octet-stream"
                    )
                    content = MediaMessageEventContent(
                        msgtype=msgtype,
                        body=filename,
                        url=mxc_url,
                    )
                    return {"type": "m.room.message", "content": content}
                except Exception:
                    logger.exception("Failed to bridge media")
            return {
                "type": "m.room.message",
                "content": TextMessageEventContent(
                    msgtype=MessageType.TEXT,
                    body=f"[{attachment.type.value}: {url}]",
                ),
            }

        elif attachment.type == AttachmentType.LOCATION:
            geo_uri = f"geo:{attachment.latitude},{attachment.longitude}"
            content = TextMessageEventContent(
                msgtype=MessageType.LOCATION,
                body=f"Location: {geo_uri}",
                geo_uri=geo_uri,
            )
            return {"type": "m.room.message", "content": content}

        logger.debug("Unsupported attachment type: %s", attachment.type)
        return None
