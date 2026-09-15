# SPDX-License-Identifier: MIT

"""Adapt mobile attributed text and images to classic YouTube run payloads."""

from __future__ import annotations

from typing import cast

from chat_downloader.debugging import debug_log
from chat_downloader.utils.json_types import (
    JSONDict,
    JSONList,
    dig,
    get_dict,
    get_int,
    get_list,
    get_str,
)


def image_thumbnails(image: JSONDict) -> JSONDict:
    """Keep usable image sources for the shared thumbnail parser."""
    sources: JSONList = [
        source
        for source in get_list(image, "sources")
        if isinstance(source, dict) and get_str(source, "url").strip()
    ]
    return {"thumbnails": sources} if sources else {}


def _attachment_emoji(attachment: JSONDict) -> JSONDict:
    element = get_dict(attachment, "element")
    image = get_dict(get_dict(get_dict(element, "type"), "imageType"), "image")
    thumbnails = image_thumbnails(image)
    if not thumbnails:
        return {}
    label = dig(element, "properties", "accessibilityProperties", "label")
    name = f":{label}:" if isinstance(label, str) and label else ":emoji:"
    first = cast("JSONDict", get_list(thumbnails, "thumbnails")[0])
    return {
        "emoji": {
            "emojiId": get_str(first, "url"),
            "shortcuts": [name],
            "image": thumbnails,
            "isCustomEmoji": True,
        },
    }


def attributed_text(value: JSONDict) -> JSONDict:
    """Replace inline images using validated UTF-16 attachment boundaries.

    Invalid, overlapping, or unsupported attachments leave their source text
    intact. A logging identifier is never interpreted as a message timestamp.
    """
    text = get_str(value, "content")
    boundaries = {0: 0}
    units = 0
    for index, char in enumerate(text, 1):
        units += 2 if ord(char) > 0xFFFF else 1
        boundaries[units] = index

    attachments = [a for a in get_list(value, "attachmentRuns") if isinstance(a, dict)]
    attachments.sort(key=lambda a: get_int(a, "startIndex", -1))
    runs: JSONList = []
    cursor = 0
    for attachment in attachments:
        start = get_int(attachment, "startIndex", -1)
        length = get_int(attachment, "length", -1)
        end = start + length
        if start not in boundaries or end not in boundaries or length <= 0:
            debug_log("Invalid mobile text attachment range")
            continue
        first, last = boundaries[start], boundaries[end]
        emoji = _attachment_emoji(attachment)
        if first < cursor or not emoji:
            debug_log("Overlapping or unsupported mobile text attachment")
            continue
        if first > cursor:
            runs.append({"text": text[cursor:first]})
        runs.append(emoji)
        cursor = last
    if cursor < len(text):
        runs.append({"text": text[cursor:]})
    return {"runs": runs} if runs else {}


def author_badges(attributed: JSONDict) -> JSONList:
    """Adapt the observed inline mobile membership badges."""
    badges: JSONList = []
    author = get_dict(attributed, "authorName")
    for attachment in get_list(author, "attachmentRuns"):
        if not isinstance(attachment, dict):
            continue
        image = dig(attachment, "element", "type", "imageType", "image")
        thumbnails = image_thumbnails(image) if isinstance(image, dict) else {}
        if thumbnails:
            badges.append(
                {
                    "liveChatAuthorBadgeRenderer": {
                        "customThumbnail": thumbnails,
                        "tooltip": "Member",
                    }
                },
            )
    return badges
