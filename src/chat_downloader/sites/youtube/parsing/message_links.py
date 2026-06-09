# SPDX-License-Identifier: MIT

"""YouTube link helpers for message parsing."""

from __future__ import annotations

from typing import Any
from urllib import parse


def _get_source_image_url(url: str) -> str:
    """Extract the source image URL by removing query parameters.

    :param url: Image URL potentially with query parameters
    :type url: str
    :return: URL without query parameters
    :rtype: str
    """
    index = url.find("=")
    if index >= 0:
        return url[0 : url.index("=")]
    return url


def _parse_youtube_link(text: str) -> str:
    """Parse and normalize YouTube link formats.

    Handles redirect links, protocol-relative URLs, and YouTube-internal links.

    :param text: Raw link text from YouTube data
    :type text: str
    :return: Normalized URL
    :rtype: str
    """
    from chat_downloader.sites.youtube.constants_patterns import (
        _YT_HOME,
        _YT_REDIRECT_PATH,
    )

    # is a redirect link
    if text.startswith((_YT_REDIRECT_PATH, _YT_HOME + _YT_REDIRECT_PATH)):
        info = dict(parse.parse_qsl(parse.urlsplit(text).query))
        return info.get("q") or ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("/"):  # is a youtube link
        return _YT_HOME + text
    # is a normal link
    return text


def _parse_navigation_endpoint(
    navigation_endpoint: dict[str, Any],
    default_text: str = "",
) -> str:
    """Extract URL from a YouTube navigation endpoint.

    :param navigation_endpoint: Navigation endpoint object from YouTube data
    :type navigation_endpoint: dict
    :param default_text: Text to return if parsing fails, defaults to ''
    :type default_text: str
    :return: Parsed URL or default text
    :rtype: str
    """
    try:
        return _parse_youtube_link(
            navigation_endpoint["commandMetadata"]["webCommandMetadata"]["url"],
        )
    except (KeyError, TypeError, IndexError):
        return default_text
