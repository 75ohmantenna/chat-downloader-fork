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
    """Main Twitch chat downloader class.

    This class provides methods for downloading chat messages from Twitch VODs,
    clips, and live streams. It handles GraphQL API requests, IRC connections,
    and badge information caching.
    """

    _NAME = "twitch.tv"

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
        """Return ``{'client_id': ...}`` when a custom client ID is configured.

        Returns an empty dict otherwise so callers can unconditionally spread
        ``**self._client_id_kwargs()`` into GQL and badge API calls.
        """
        client_id: str | None = getattr(self, "_twitch_client_id", None)
        return {"client_id": client_id} if client_id is not None else {}

    def _update_badge_info(self, channel: str, channel_id: str | None = None) -> None:
        """Fetch badge data from the Twitch API and update the instance cache.

        The instance-owned :attr:`badge_cache` is mutated in-place.  Parsing
        module globals are intentionally **not** written; all parsing calls
        receive badge data via the explicit ``badge_set`` parameter instead.

        Args:
            channel: Channel name to retrieve badges for.
            channel_id: Numeric channel ID used by the current badge operation.
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
        """Download GraphQL data using base query.

        Args:
            ops: List of GraphQL operations to execute

        Returns:
            JSON response from GraphQL API
        """
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
        """Download GraphQL data using persisted query hashes.

        Args:
            ops: List of GraphQL operations to execute
            record_optional_degradation: Content-free live diagnostic callback.

        Returns:
            JSON response from GraphQL API
        """
        auth_token: str | None = self.get_cookie_value(GQL_AUTH_COOKIE_NAME)
        client_id: str | None = getattr(self, "_twitch_client_id", None)
        if record_optional_degradation is None:
            if client_id is None:
                return _download_gql(
                    self._session_post,
                    ops,
                    auth_token,
                )
            return _download_gql(
                self._session_post,
                ops,
                auth_token,
                client_id=client_id,
            )
        if client_id is None:
            return _download_gql(
                self._session_post,
                ops,
                auth_token,
                record_optional_degradation=record_optional_degradation,
            )
        return _download_gql(
            self._session_post,
            ops,
            auth_token,
            client_id=client_id,
            record_optional_degradation=record_optional_degradation,
        )

    def generate_urls(  # type: ignore[override]  # test helper: signature intentionally diverges from base
        self,
        livestream_limit: int,
        vod_limit: int,
        clip_limit: int,
    ) -> Iterable[str]:
        """Generate test URLs from top livestreams and their VODs/clips.

        Args:
            livestream_limit: Number of top livestreams to retrieve.
            vod_limit: Number of VODs to fetch per livestream.
            clip_limit: Number of clips to fetch per livestream.

        Yields:
            URLs for livestreams, their VODs, and their clips.
        """
        yield from generate_twitch_urls(self, livestream_limit, vod_limit, clip_limit)

    def _get_chat_messages_by_vod_id(
        self,
        vod_id: str,
        params: ChatRequest | dict[str, Any],
        max_duration: float | None,
        offset: float | None = None,
    ) -> Generator[dict[str, Any], None, None]:
        """Get chat messages for a VOD or clip.

        Args:
            vod_id: VOD ID to retrieve messages for
            params: Parameters dictionary
            max_duration: Maximum duration of the video
            offset: Time offset for clips (None for VODs)

        Yields:
            Parsed chat message dictionaries
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
        )

    def _get_chat_by_vod_id(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Internal routing method for VOD chat retrieval.

        Args:
            match: Regex match object with 'id' group
            params: Parameters dictionary

        Returns:
            Chat object
        """
        return self.get_chat_by_vod_id(match.group("id"), params)

    def get_chat_by_vod_id(
        self,
        vod_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Get chat messages for a VOD by ID.

        This is a public API method for retrieving chat replay from a past
        broadcast.

        Args:
            vod_id: Twitch VOD ID (e.g., '87136772')
            params: Parameters dictionary with optional start_time,
                end_time, etc.

        Returns:
            Chat object with message generator

        Raises:
            VideoUnavailable: If the VOD does not exist or is unavailable
        """
        request = self._coerce_chat_request(params)
        return build_vod_chat(self, vod_id, request)

    def _get_chat_by_clip_id(
        self,
        match: re.Match[str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Internal routing method for clip chat retrieval.

        Args:
            match: Regex match object with 'id' group
            params: Parameters dictionary

        Returns:
            Chat object
        """
        return self.get_chat_by_clip_id(match.group("id"), params)

    def get_chat_by_clip_id(
        self,
        clip_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Get chat messages for a clip by ID.

        This is a public API method for retrieving chat replay from a clip.

        Args:
            clip_id: Twitch clip slug (e.g., 'TrappedFrigidSeemsGood')
            params: Parameters dictionary with optional start_time,
                end_time, etc.

        Returns:
            Chat object with message generator

        Raises:
            NoChatReplay: If the clip's VOD has expired and chat is
                unavailable
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
        """Get live chat messages for a stream via IRC.

        The transport connects anonymously, requests Twitch IRC tags and
        commands, and yields parsed frames in arrival order.

        Args:
            stream_id: Channel name
            params: Parameters dictionary
            diagnostics: Mutable counters for the owning live-chat run.

        Yields:
            Parsed IRC message dictionaries
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
        """Internal routing method for stream chat retrieval.

        Args:
            match: Regex match object with 'id' group
            params: Parameters dictionary

        Returns:
            Chat object
        """
        return self.get_chat_by_stream_id(match.group("id"), params)

    def get_chat_by_stream_id(
        self,
        stream_id: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Get live chat messages for a stream by channel name.

        This public API method builds an IRC-backed chat for the channel. An
        offline or upcoming channel remains open while it waits for messages.

        Args:
            stream_id: Twitch channel name (e.g., 'shroud')
            params: Parameters dictionary with optional message_groups,
                buffer_size, etc.

        Returns:
            Chat object with live message generator

        Raises:
            UserNotFound: If the channel does not exist
        """
        request = self._coerce_chat_request(params)
        return build_stream_chat(self, stream_id, request)
