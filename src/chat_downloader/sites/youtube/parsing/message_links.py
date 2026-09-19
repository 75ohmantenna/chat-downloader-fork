# SPDX-License-Identifier: MIT

"""YouTube link helpers for message parsing."""

from __future__ import annotations

from typing import Any
from urllib import parse


def _get_source_image_url(url: str) -> str:
    """Return an image URL without its provider resize suffix.

    Args:
        url: Image URL that may contain an equals-delimited resize suffix.
    """
    index = url.find("=")
    if index >= 0:
        return url[0 : url.index("=")]
    return url


def _parse_youtube_link(text: str) -> str:
    """Parse and normalize YouTube link formats.

    Handles redirect links, protocol-relative URLs, and YouTube-internal links.

    Args:
        text: Raw link text from a YouTube payload.
    """
    from chat_downloader.sites.youtube.constants_patterns import (
        _YT_HOME,
        _YT_REDIRECT_PATH,
    )

    if text.startswith((_YT_REDIRECT_PATH, _YT_HOME + _YT_REDIRECT_PATH)):
        info = dict(parse.parse_qsl(parse.urlsplit(text).query))
        return info.get("q") or ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):  # is a youtube link
        return _YT_HOME + text
    return text


def _parse_navigation_endpoint(
    navigation_endpoint: dict[str, Any],
    default_text: str = "",
) -> str:
    """Extract URL from a YouTube navigation endpoint.

    Args:
        navigation_endpoint: Navigation endpoint from a YouTube payload.
        default_text: Text returned when the endpoint has no usable URL.
    """
    try:
        return _parse_youtube_link(
            navigation_endpoint["commandMetadata"]["webCommandMetadata"]["url"],
        )
    except (KeyError, TypeError, IndexError):
        return default_text
