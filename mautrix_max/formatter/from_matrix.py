"""Convert Matrix messages to Max message format."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Optional

from mautrix.types import (
    MediaMessageEventContent,
    MessageType,
    TextMessageEventContent,
)

if TYPE_CHECKING:
    from ..max.base_client import BaseMaxClient

logger = logging.getLogger("mau.fmt.from_matrix")


class MatrixMessageConverter:
    """Convert Matrix events to Max message parameters."""

    @staticmethod
    async def convert(
        content: Any,
        max_client: Optional[BaseMaxClient] = None,
        download_fn=None,
    ) -> dict[str, Any]:
        """Convert a Matrix message content to Max send parameters.

        Returns a dict with keys: text, attachments (list), etc.
        """
        result: dict[str, Any] = {"text": "", "attachments": []}

        if isinstance(content, TextMessageEventContent):
            if content.msgtype == MessageType.TEXT:
                result["text"] = MatrixMessageConverter._html_to_text(content)
            elif content.msgtype == MessageType.NOTICE:
                result["text"] = content.body or ""
            elif content.msgtype == MessageType.EMOTE:
                result["text"] = f"* {content.body}" if content.body else ""
            elif content.msgtype == MessageType.LOCATION:
                result["text"] = content.body or "Shared a location"

        elif isinstance(content, MediaMessageEventContent):
            # Download from Matrix and upload to Max
            if content.url and max_client and download_fn:
                try:
                    data = await download_fn(content.url)
                    filename = content.body or "file"
                    mime = "application/octet-stream"
                    if content.info:
                        mime = content.info.mimetype or mime
                    token = await max_client.upload_media(data, filename, mime)
                    from ..max.media import get_max_attachment_type, make_file_attachment, make_photo_attachment
                    att_type = get_max_attachment_type(mime)
                    if att_type == "photo":
                        result["attachments"].append(make_photo_attachment(token))
                    else:
                        result["attachments"].append(make_file_attachment(token, filename))
                except Exception:
                    logger.exception("Failed to bridge media from Matrix to Max")
                    result["text"] = f"[Media: {content.body}]"
            else:
                result["text"] = f"[Media: {content.body}]"

        return result

    @staticmethod
    def _html_to_text(content: TextMessageEventContent) -> str:
        """Convert HTML formatted message to plain text for Max."""
        if not content.formatted_body:
            return content.body or ""

        text = content.formatted_body
        # Convert common HTML to plain text
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"<b>(.*?)</b>", r"*\1*", text)
        text = re.sub(r"<strong>(.*?)</strong>", r"*\1*", text)
        text = re.sub(r"<i>(.*?)</i>", r"_\1_", text)
        text = re.sub(r"<em>(.*?)</em>", r"_\1_", text)
        text = re.sub(r"<code>(.*?)</code>", r"`\1`", text)
        text = re.sub(r"<pre>(.*?)</pre>", r"```\n\1\n```", text, flags=re.DOTALL)
        text = re.sub(r'<a href="(.*?)">(.*?)</a>', r"\2 (\1)", text)
        # Strip remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)
        # Decode HTML entities
        text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"')
        return text
