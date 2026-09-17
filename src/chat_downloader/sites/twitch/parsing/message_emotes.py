# SPDX-License-Identifier: MIT

"""Twitch emote and author-image parsing helpers."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from chat_downloader.debugging import debug_log
from chat_downloader.sites.models import Image
from chat_downloader.sites.twitch.constants import EMOTE_REGEX

# Pre-compiled emote regex — compiled once at import time instead of on every
# _parse_emotes() call.  Using the compiled object avoids the re module's
# internal cache lookup overhead on each call.
_EMOTE_RE: re.Pattern[str] = re.compile(EMOTE_REGEX)
_EMOTE_IMAGE_THEMES = ("light", "dark")
_EMOTE_IMAGE_SIZES = (
    (28, "1.0"),
    (56, "2.0"),
    (112, "3.0"),
)


def _parse_author_images(original_url: str) -> list[dict[str, Any]]:
    """Parse author profile images from a Twitch profile image URL.

    Args:
        original_url: Original profile image URL (300x300)

    Returns:
        List of image dictionaries with different sizes
    """
    # e.g. https://static-cdn.jtvnw.net/jtv_user_pictures/
    # 3892c956-0616-4fc9-b2fe-527b1be0b623-profile_image-300x300.png
    smaller_icon = original_url.replace("300x300", "70x70")
    return [
        Image(original_url, 300, 300).json(),
        Image(smaller_icon, 70, 70).json(),
    ]


@lru_cache(maxsize=4096)
def _generate_emote_image_list(emote_id: str) -> tuple[dict[str, Any], ...]:
    """Generate the canonical, lru-cached image list for a Twitch emote ID.

    Returns an immutable tuple of six image dicts (three sizes by two
    themes); tuples serialize as JSON arrays so output is unchanged.
    Callers must not mutate the individual dicts.
    """
    images = []
    for theme in _EMOTE_IMAGE_THEMES:
        for size_pixels, size_scale in _EMOTE_IMAGE_SIZES:
            image = Image(
                "https://static-cdn.jtvnw.net/emoticons/v2/"
                f"{emote_id}/default/{theme}/{size_scale}",
                size_pixels,
                size_pixels,
                f"{size_pixels}x{size_pixels}-{theme}",
            ).json()
            images.append(image)
    return tuple(images)


def _parse_emotes(text: str) -> list[dict[str, Any]]:
    """Parse emote information from IRC message tag.

    Format: <emote ID>:<first index>-<last index>,<another first>-<another
    last>/...

    Args:
        text: Emote tag text

    Returns:
        List of emote dictionaries
    """
    emotes = []
    matches = _EMOTE_RE.findall(text)

    for match in matches:
        emote_id = match[0]
        emote = {
            "id": emote_id,
            "locations": match[1].split(","),
            "images": _generate_emote_image_list(emote_id),
        }
        emotes.append(emote)

    return emotes


def _add_text_for_emotes(message: str, emote_list: list[dict[str, Any]]) -> None:
    """Add emote text/name to emote dictionaries from message.

    Args:
        message: Message text
        emote_list: List of emote dictionaries to update
    """
    for emote in emote_list:
        try:
            first_location = [int(x) for x in emote["locations"][0].split("-")]
            emote["name"] = message[first_location[0] : first_location[1] + 1]
        except (KeyError, IndexError, ValueError, TypeError):
            debug_log(f"Invalid emote: {emote}", f"Message: {message}")
            continue
