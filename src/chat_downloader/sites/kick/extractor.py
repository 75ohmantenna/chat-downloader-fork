# SPDX-License-Identifier: MIT

"""Kick chat downloader extractor.

Thin routing layer for Kick. URL matching and the public API live here; the
actual work is delegated to the live, VOD replay, and clip service modules.

Supports live chat (``kick.com/{username}``), VOD chat replay
(``kick.com/{username}/videos/{uuid}``), and bounded clip replay
(``kick.com/{username}/clips/{clip_id}``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from chat_downloader.sites.base import BaseChatDownloader

from .api_client import KickApiClient
from .clip_service import get_clip_chat as build_clip_chat
from .constants import VALID_URLS
from .errors import KickCountryBlocked, KickError
from .live_service import get_chat_by_channel as build_channel_chat
from .replay_service import get_vod_chat as build_vod_chat

if TYPE_CHECKING:
    import re

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.models import Chat

__all__ = ["KickChatDownloader", "KickCountryBlocked", "KickError"]


class KickChatDownloader(BaseChatDownloader):
    """Download unauthenticated Kick live, VOD, or clip chat.

    Supports:
    - Live: ``https://kick.com/{username}``
    - VOD:  ``https://kick.com/{username}/videos/{uuid}``
    - Clip: ``https://kick.com/{username}/clips/{clip_id}``
    """

    _NAME = "kick.com"

    _SITE_DEFAULT_PARAMS: ClassVar[dict[str, str]] = {
        "format": "kick",
    }

    _VALID_URLS: ClassVar[dict[str, str]] = VALID_URLS

    def __init__(self, **kwargs: object) -> None:
        """Initialize base HTTP state and an isolated Kick API session."""
        super().__init__(**kwargs)
        proxies = dict(self.session.proxies) if self.session.proxies else None
        self._kick_api_client: KickApiClient | None = None
        try:
            self._kick_api_client = KickApiClient(
                proxy=proxies,
                extra_headers=dict(self.session.headers),
                timeout=self._http_timeout,
                trust_env=self.session.trust_env,
            )
        except BaseException:
            super().close()
            raise

    @property
    def _kick_client(self) -> KickApiClient:
        """Return the owned client while the downloader is open."""
        if self._kick_api_client is None:
            msg = "Kick downloader is closed."
            raise RuntimeError(msg)
        return self._kick_api_client

    def close(self) -> None:
        """Close the dedicated Kick API session and base HTTP session."""
        kick_client = getattr(self, "_kick_api_client", None)
        self._kick_api_client = None
        try:
            if kick_client is not None:
                kick_client.close()
        finally:
            super().close()

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
        return self.get_chat_by_video(  # pragma: no cover — network-dependent VOD API
            match.group("id"), match.group("video_id"), params
        )

    def _get_chat_by_clip(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Route a clip URL match to the bounded replay builder."""
        return self.get_chat_by_clip(
            match.group("id"),
            match.group("clip_id"),
            params,
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
            KickCountryBlocked: If Kick blocks the request's country or region.
            KickError: If required channel metadata is incomplete.
        """
        request = self._coerce_chat_request(params)
        return build_channel_chat(self, username, request)

    def get_chat_by_video(  # pragma: no cover — network-dependent VOD API
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
            KickCountryBlocked: If Kick blocks the request's country or region.
            KickError: If the video is not found or metadata is incomplete.
        """
        request = self._coerce_chat_request(params)
        return build_vod_chat(
            username,
            video_id,
            request,
            api_client=self._kick_client,
        )  # pragma: no cover

    def get_chat_by_clip(
        self,
        username: str,
        clip_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Get chat replay for a Kick clip.

        ``start_time`` and ``end_time`` remain relative to the clip. The web
        metadata path maps the bounded interval onto the source VOD; the
        mobile fallback uses the clip's validated absolute timestamp window.

        Raises:
            KickCountryBlocked: If Kick blocks the request's country or region.
            KickError: If validated web and mobile metadata cannot establish
                a replay interval.
        """
        request = self._coerce_chat_request(params)
        return build_clip_chat(
            username,
            clip_id,
            request,
            api_client=self._kick_client,
        )
