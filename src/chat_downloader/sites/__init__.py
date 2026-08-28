# SPDX-License-Identifier: MIT

"""Supported-site registry for chat-downloader."""

from __future__ import annotations

from .base import BaseChatDownloader
from .kick import KickChatDownloader, KickCountryBlocked, KickError
from .models import Chat, Image
from .remap import Remapper
from .twitch import TwitchChatDownloader, TwitchError
from .youtube import YouTubeChatDownloader

__all__ = [
    "BaseChatDownloader",
    "Chat",
    "Image",
    "KickChatDownloader",
    "KickCountryBlocked",
    "KickError",
    "Remapper",
    "TwitchChatDownloader",
    "TwitchError",
    "YouTubeChatDownloader",
    "get_all_sites",
]

_SITE_CLASSES: tuple[type[BaseChatDownloader], ...] = (
    TwitchChatDownloader,
    YouTubeChatDownloader,
    KickChatDownloader,
)


def get_all_sites(
    *,
    include_parent: bool = False,
) -> list[type[BaseChatDownloader]]:
    """Get all supported sites.

    :param include_parent: Whether to include the BaseChatDownloader, defaults
        to False
    :type include_parent: bool, optional
    :return: A list of all supported ChatDownloader classes
    :rtype: list
    """
    if include_parent:
        return [BaseChatDownloader, *_SITE_CLASSES]
    return list(_SITE_CLASSES)
