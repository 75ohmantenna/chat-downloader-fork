# SPDX-License-Identifier: MIT

"""Kick chat downloader extractor.

Thin routing layer for Kick. URL matching and the public API live here; the
actual work is delegated to :mod:`chat_downloader.sites.kick.live_service`
(live chat) and :mod:`chat_downloader.sites.kick.replay_service` (VOD replay).

Supports live chat (``kick.com/{username}``) and VOD chat replay
(``kick.com/{username}/videos/{uuid}``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from chat_downloader.sites.base import BaseChatDownloader

from .constants import VALID_URLS
from .errors import KickError
from .live_service import get_chat_by_channel as build_channel_chat
from .replay_service import get_vod_chat as build_vod_chat

if TYPE_CHECKING:
    import re

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.models import Chat

__all__ = ["KickChatDownloader", "KickError"]


class KickChatDownloader(BaseChatDownloader):
    """Download public, live chat from Kick channels or VOD replay.

    Supports:
    - Live: ``https://kick.com/{username}``
    - VOD:  ``https://kick.com/{username}/videos/{uuid}``
    """

    _NAME = "kick.com"

    _SITE_DEFAULT_PARAMS: ClassVar[dict[str, Any]] = {
        "format": "default",
    }

    _VALID_URLS: ClassVar[dict[str, str]] = VALID_URLS

    _TESTS: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "Kick live channel chat.",
            "params": {"url": "https://kick.com/xqc", "timeout": 5},
        },
        {
            "name": "Offline Kick channels fail clearly.",
            "params": {"url": "https://kick.com/somelikelyofflinechannel"},
            "expected_result": {"error": KickError},
        },
    ]

    def _get_chat_by_channel(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Route a channel URL match to the live chat builder.

        Args:
            match: Regex match with an ``id`` group (the channel username).
            params: Chat request parameters.

        Returns:
            A :class:`Chat` streaming the channel's live chat.
        """
        return self.get_chat_by_channel(match.group("id"), params)

    def _get_chat_by_video(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Route a VOD URL match to the replay chat builder.

        Args:
            match: Regex match with ``id`` (username) and ``video_id`` groups.
            params: Chat request parameters.

        Returns:
            A :class:`Chat` that yields historical chat messages for the VOD.
        """
        return self.get_chat_by_video(
            match.group("id"), match.group("video_id"), params
        )

    def get_chat_by_channel(
        self,
        username: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        r"""Get live chat for a Kick channel by username.

        Args:
            username: Kick channel username/slug (e.g. ``"xqc"``).
            params: Chat request parameters.

        Returns:
            A :class:`Chat` with a live message generator.

        Raises:
            UserNotFound: If the channel does not exist.
            CaptchaChallengeRequired: If Kick returns a challenge page.
            KickError: If metadata is incomplete or the channel is offline.
        """
        request = self._coerce_chat_request(params)
        return build_channel_chat(self, username, request)

    def get_chat_by_video(
        self,
        username: str,
        video_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        r"""Get chat replay for a Kick VOD by video UUID.

        Args:
            username: Kick channel username/slug.
            video_id: VOD UUID (e.g. ``"4ef9b5aa-89f2-4aee-96c2-e72c1b5a8b4b"``).
            params: Chat request parameters.

        Returns:
            A :class:`Chat` that yields historical chat messages.

        Raises:
            KickError: If the video is not found or metadata is incomplete.
        """
        request = self._coerce_chat_request(params)
        return build_vod_chat(username, video_id, request)
