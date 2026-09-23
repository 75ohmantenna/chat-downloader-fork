# SPDX-License-Identifier: MIT

"""Assemble Twitch live, VOD, and clip chat behind the site API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from chat_downloader.debugging import logger
from chat_downloader.errors import SiteError
from chat_downloader.sites.base import BaseChatDownloader

from .badge_client import update_badge_info
from .constants import VALID_URLS
from .graphql_client import (
    GQL_AUTH_COOKIE_NAME,
    _download_base_gql,
    _download_gql,
)
from .irc_transport import TwitchChatIRC, get_chat_messages_by_stream_id
from .live_service import get_chat_by_stream_id as build_stream_chat
from .live_service import iter_stream_chat_messages
from .replay_service import get_chat_by_clip_id as build_clip_chat
from .replay_service import get_chat_by_vod_id as build_vod_chat
from .replay_service import iter_vod_chat_messages
from .replay_transport import get_chat_messages_by_vod_id
from .types import BadgeCache
from .url_generation import generate_urls as generate_twitch_urls

if TYPE_CHECKING:
    import re
    from collections.abc import Callable, Generator, Iterable

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.models import Chat

    from .irc_diagnostics import _TwitchLiveDiagnostics


class TwitchError(SiteError):
    """Raised when an error occurs with a Twitch video."""


class TwitchChatDownloader(BaseChatDownloader):
    """Download Twitch VOD, clip, and live chat via GraphQL/IRC with badge caching."""

    _NAME = "twitch.tv"

    _COMPLETED_REPLAY_STATUSES: ClassVar[frozenset[str]] = frozenset({"past"})

    _SITE_DEFAULT_PARAMS: ClassVar[dict[str, Any]] = {
        "format": "twitch",
    }

    _VALID_URLS: ClassVar[dict[str, str]] = VALID_URLS

    def __init__(self, **kwargs: Any) -> None:
        """Initialize TwitchChatDownloader with an owned badge cache."""
        super().__init__(**kwargs)
        self.badge_cache = BadgeCache()
        self._channel_ids: dict[str, str] = {}

    def _client_id_kwargs(self) -> dict[str, str]:
        """Return custom ``client_id`` kwargs for GQL/badge calls, or an empty dict."""
        client_id: str | None = getattr(self, "_twitch_client_id", None)
        return {"client_id": client_id} if client_id is not None else {}

    def _update_badge_info(self, channel: str, channel_id: str | None = None) -> None:
        """Fetch badges into the instance cache, never parsing-module globals.

        Parsers receive cached data through ``badge_set``.

        Args:
            channel: Channel name.
            channel_id: Numeric channel ID for this badge operation.
        """
        channel_ids: dict[str, str] = getattr(self, "_channel_ids", {})
        self._channel_ids = channel_ids
        if channel_id:
            channel_ids[channel.lower()] = channel_id
        effective_channel_id = channel_id or channel_ids.get(channel.lower())
        badge_kwargs = self._client_id_kwargs()
        if effective_channel_id:
            badge_kwargs["channel_id"] = effective_channel_id
        update_badge_info(
            self._session_post,
            channel,
            _download_gql,
            self.badge_cache.global_badges,
            self.badge_cache.channel_badges,
            **badge_kwargs,
        )

    def _download_base_gql(self, ops: Any) -> Any:
        """Execute a list of base GraphQL operations and return the JSON response."""
        auth_token: str | None = self.get_cookie_value(GQL_AUTH_COOKIE_NAME)
        return _download_base_gql(
            self._session_post, ops, auth_token, **self._client_id_kwargs()
        )

    def _download_gql(
        self,
        ops: Any,
        *,
        record_optional_degradation: Callable[[], None] | None = None,
    ) -> Any:
        """Execute a list of persisted GraphQL operations and return JSON.

        Args:
            ops: GraphQL operations with names and variables.
            record_optional_degradation: Content-free live diagnostic callback.
        """
        kwargs: dict[str, Any] = self._client_id_kwargs()
        if record_optional_degradation is not None:
            kwargs["record_optional_degradation"] = record_optional_degradation
        return _download_gql(
            self._session_post,
            ops,
            self.get_cookie_value(GQL_AUTH_COOKIE_NAME),
            **kwargs,
        )

    def generate_urls(  # type: ignore[override]  # test helper: signature intentionally diverges from base
        self,
        livestream_limit: int,
        vod_limit: int,
        clip_limit: int,
    ) -> Iterable[str]:
        """Yield test URLs for top livestreams and their VODs/clips.

        Args:
            livestream_limit: Number of top livestreams.
            vod_limit: VODs per livestream.
            clip_limit: Clips per livestream.
        """
        yield from generate_twitch_urls(self, livestream_limit, vod_limit, clip_limit)

    def _get_chat_messages_by_vod_id(
        self,
        vod_id: str,
        params: ChatRequest | dict[str, Any],
        max_duration: float | None,
        offset: float | None = None,
        *,
        diagnostics: dict[str, object] | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield parsed VOD or clip chat messages.

        Args:
            vod_id: Twitch VOD ID containing the replay chat.
            params: Replay request options, including time bounds and filters.
            max_duration: Maximum video duration.
            offset: Clip time offset; None for VODs.
            diagnostics: Mutable replay completion and record-loss counters.
        """
        request = self._coerce_chat_request(params)

        yield from iter_vod_chat_messages(
            self,
            vod_id,
            request,
            max_duration,
            offset,
            fetch_messages=get_chat_messages_by_vod_id,
            logger_obj=logger,
            diagnostics=diagnostics,
        )

    def _get_chat_by_vod_id(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Route VOD chat retrieval using the match's ``id`` group."""
        return self.get_chat_by_vod_id(match.group("id"), params)

    def get_chat_by_vod_id(
        self,
        vod_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Return a past broadcast's replay chat and message generator.

        Args:
            vod_id: Twitch VOD ID (e.g., '87136772').
            params: Request options including start_time and end_time.

        Raises:
            VideoUnavailable: The VOD does not exist or is unavailable.
        """
        request = self._coerce_chat_request(params)
        return build_vod_chat(self, vod_id, request)

    def _get_chat_by_clip_id(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Route clip chat retrieval using the match's ``id`` group."""
        return self.get_chat_by_clip_id(match.group("id"), params)

    def get_chat_by_clip_id(
        self,
        clip_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Return a clip's replay chat and message generator.

        Args:
            clip_id: Twitch clip slug (e.g., 'TrappedFrigidSeemsGood').
            params: Request options including start_time and end_time.

        Raises:
            NoChatReplay: The clip's VOD has expired and chat is unavailable.
        """
        request = self._coerce_chat_request(params)
        return build_clip_chat(self, clip_id, request)

    def _get_chat_messages_by_stream_id(
        self,
        stream_id: str,
        params: ChatRequest | dict[str, Any],
        *,
        diagnostics: _TwitchLiveDiagnostics | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Yield parsed IRC frames in arrival order via anonymous live chat.

        Requests Twitch IRC tags and commands.

        Args:
            stream_id: Channel name.
            params: Live request options, including filters and buffer size.
            diagnostics: Mutable counters for the owning live-chat run.
        """
        request = self._coerce_chat_request(params)
        yield from iter_stream_chat_messages(
            self,
            stream_id,
            request,
            irc_factory=TwitchChatIRC,
            message_generator=get_chat_messages_by_stream_id,
            diagnostics=diagnostics,
        )

    def _get_chat_by_stream_id(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Route stream chat retrieval using the match's ``id`` group."""
        return self.get_chat_by_stream_id(match.group("id"), params)

    def get_chat_by_stream_id(
        self,
        stream_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Return IRC-backed live chat and its message generator.

        Offline/upcoming channels remain open waiting for messages.

        Args:
            stream_id: Twitch channel name (e.g., 'shroud').
            params: Request options including message_groups and buffer_size.

        Raises:
            UserNotFound: The channel does not exist.
        """
        request = self._coerce_chat_request(params)
        return build_stream_chat(self, stream_id, request)
