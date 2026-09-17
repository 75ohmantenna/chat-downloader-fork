# SPDX-License-Identifier: MIT

"""Route Kick live, VOD replay, and bounded clip URLs to their service modules.

Supports ``kick.com/{username}``, ``kick.com/{username}/videos/{uuid}``, and
``kick.com/{username}/clips/{clip_id}``; URL matching and the public API live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import unquote

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
    """Download unauthenticated Kick live, VOD, or clip chat (URLs above)."""

    _NAME = "kick.com"

    _SITE_DEFAULT_PARAMS: ClassVar[dict[str, str]] = {
        "format": "kick",
    }

    _VALID_URLS: ClassVar[dict[str, str]] = VALID_URLS

    def __init__(self, **kwargs: object) -> None:
        """Initialize base HTTP state and an isolated Kick API session."""
        super().__init__(**kwargs)
        proxies = dict(self.session.proxies) if self.session.proxies else None
        configured_headers = kwargs.get("headers")
        extra_headers = (
            dict(configured_headers) if isinstance(configured_headers, dict) else None
        )
        self._kick_api_client: KickApiClient | None = None
        try:
            self._kick_api_client = KickApiClient(
                proxy=proxies,
                extra_headers=extra_headers,
                timeout=self._http_timeout,
                trust_env=self.session.trust_env,
                bearer_token_provider=self._get_kick_bearer_token,
            )
        except BaseException:
            super().close()
            raise

    def _get_kick_bearer_token(self) -> str | None:
        """Return a safe, current bearer token from Kick's session cookie."""
        for cookie in self.session.cookies:
            if (
                cookie.name != "session_token"
                or cookie.domain.lstrip(".").casefold() != "kick.com"
                or cookie.path != "/"
                or cookie.is_expired()
            ):
                continue
            cookie_value = cookie.value
            if not cookie_value:
                continue
            return unquote(cookie_value)
        return None

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
        """Stream live chat for the channel username in match group ``id``."""
        return self.get_chat_by_channel(match.group("id"), params)

    def _get_chat_by_video(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Replay VOD chat for match groups ``id`` (username) and ``video_id``."""
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
        r"""Stream live chat for a Kick username/slug (e.g. ``"xqc"``).

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
        r"""Replay historical chat for a channel username/slug and VOD UUID.

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
