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
    """Return the registered site downloader classes.

    Args:
        include_parent: Include ``BaseChatDownloader`` before the concrete
            provider classes.
    """
    if include_parent:
        return [BaseChatDownloader, *_SITE_CLASSES]
    return list(_SITE_CLASSES)
