# SPDX-License-Identifier: MIT

"""Text-related message content parsing implementation."""

from __future__ import annotations

from typing import Any

from chat_downloader.sites.models import Image
from chat_downloader.utils.dict_utils import multi_get

from .message_links import _get_source_image_url, _parse_navigation_endpoint


def _get_simple_text(item: dict[str, Any]) -> str | None:
    """Extract simple text from a YouTube text object."""
    return item.get("simpleText") or item.get("content")


def _parse_text(info: dict[str, Any]) -> str | None:
    """Parse text from YouTube data, supporting both runs and simple text."""
    return _parse_runs(info)["message"] or _get_simple_text(info)


def _append_run(
    message_info: dict[str, Any],
    run: dict[str, Any],
    message_emotes: dict[str, dict[str, Any]],
    parse_links: bool,
) -> None:
    """Append one run's contribution to *message_info* and *message_emotes*."""
    if "text" in run:
        if parse_links and "navigationEndpoint" in run:
            message_info["message"] += _parse_navigation_endpoint(
                run["navigationEndpoint"],
                run["text"],
            )
        else:
            message_info["message"] += run["text"]
    elif "emoji" in run:
        emoji = run["emoji"]
        emoji_id = emoji.get("emojiId")
        name = multi_get(emoji, "shortcuts", 0) or emoji_id
        if emoji_id and emoji_id not in message_emotes:
            message_emotes[emoji_id] = {
                "id": emoji_id,
                "name": name,
                "shortcuts": emoji.get("shortcuts"),
                "search_terms": emoji.get("searchTerms"),
                "images": _parse_thumbnails(emoji.get("image", {})),
                "is_custom_emoji": emoji.get("isCustomEmoji", False),
            }
        message_info["message"] += name
    else:
        message_info["message"] += str(run)


def _parse_runs(run_info: Any, parse_links: bool = True) -> dict[str, Any]:
    """Parse YouTube formatted messages (runs) with text, links, and emojis."""
    message_info: dict[str, Any] = {"message": ""}

    if not isinstance(run_info, dict):
        return message_info

    if "content" in run_info and not run_info.get("runs"):
        message_info["message"] = run_info.get("content", "")
        return message_info

    message_emotes: dict[str, dict[str, Any]] = {}
    for run in run_info.get("runs") or []:
        _append_run(message_info, run, message_emotes, parse_links)

    if message_emotes:
        message_info["emotes"] = list(message_emotes.values())

    return message_info


def _parse_thumbnails(item: list[Any] | dict[str, Any]) -> list[dict[str, Any]]:
    """Parse thumbnail/image data from YouTube objects."""
    # sometimes thumbnails come as a list
    if isinstance(item, list):
        if not item:
            return []
        item = item[0]  # rebase

    if not isinstance(item, dict):
        return []

    thumbnails = item.get("thumbnails") or []
    final = [Image(**x).json() for x in thumbnails]

    if final:
        final.insert(
            0,
            Image(_get_source_image_url(final[0]["url"]), image_id="source").json(),
        )

    return final


def _parse_action_button(item: dict[str, Any]) -> dict[str, str]:
    """Parse action button data from YouTube objects."""
    endpoint = multi_get(item, "buttonRenderer", "navigationEndpoint")

    return {
        "url": _parse_navigation_endpoint(endpoint) if endpoint else "",
        "text": multi_get(item, "buttonRenderer", "text", "simpleText") or "",
    }
