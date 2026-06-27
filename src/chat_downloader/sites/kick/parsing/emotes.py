# SPDX-License-Identifier: MIT

"""Kick inline-emote parsing.

Kick chat content embeds emotes as markers such as ``[emote:37233:PogU]`` or
``[emote:37233:]`` (name omitted). This module converts that content into:

* readable plain text, where each marker becomes ``:NAME:`` (e.g. ``:PogU:``)
  or a stable ``:emote_<id>:`` placeholder when no name is present, and
* structured emote metadata for JSON/JSONL outputs.

Emote image files are never downloaded; only the derivable full-size image URL
is recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.sites.kick.constants import (
    EMOTE_IMAGE_TEMPLATE,
    EMOTE_REGEX,
    EMOTE_SOURCE,
)
from chat_downloader.sites.models import Image

if TYPE_CHECKING:
    import re


def _readable_emote_text(emote_id: str, name: str) -> str:
    """Return the readable text used to replace an emote marker.

    Args:
        emote_id: The numeric emote id.
        name: The emote name from the marker (may be empty).

    Returns:
        ``:name:`` if present, otherwise a stable ``:emote_<id>:`` placeholder.
    """
    return f":{name}:" if name else f":emote_{emote_id}:"


def _build_emote(
    emote_id: str, name: str, marker: str, location: str
) -> dict[str, Any]:
    """Build a structured emote metadata entry.

    Args:
        emote_id: The numeric emote id.
        name: Readable emote name (may be empty).
        marker: The original marker text, preserved for debugging.
        location: ``"start-end"`` character span in the readable text.

    Returns:
        A structured emote dictionary.
    """
    return {
        "id": emote_id,
        "name": name or None,
        "images": [Image(EMOTE_IMAGE_TEMPLATE.format(emote_id=emote_id)).json()],
        "source": EMOTE_SOURCE,
        "original_marker": marker,
        "locations": [location],
    }


def parse_emotes(content: str) -> tuple[str, list[dict[str, Any]]]:
    """Convert Kick chat content to readable text plus structured emotes.

    Repeated occurrences of the same emote id are merged into a single entry
    with multiple ``locations``, mirroring the project's emote convention.

    Args:
        content: Raw Kick message content with inline emote markers.

    Returns:
        A 2-tuple ``(readable_text, emotes)`` where ``emotes`` is empty when the
        content contains no emote markers.
    """
    emotes_by_id: dict[str, dict[str, Any]] = {}
    parts: list[str] = []
    cursor = 0
    position = 0

    match: re.Match[str]
    for match in EMOTE_REGEX.finditer(content):
        literal = content[cursor : match.start()]
        parts.append(literal)
        position += len(literal)

        emote_id, name = match.group(1), match.group(2)
        readable = _readable_emote_text(emote_id, name)
        location = f"{position}-{position + len(readable) - 1}"

        existing = emotes_by_id.get(emote_id)
        if existing is None:
            emotes_by_id[emote_id] = _build_emote(
                emote_id, name, match.group(0), location
            )
        else:
            existing["locations"].append(location)
            if existing["name"] is None and name:
                existing["name"] = name

        parts.append(readable)
        position += len(readable)
        cursor = match.end()

    parts.append(content[cursor:])
    return "".join(parts), list(emotes_by_id.values())
