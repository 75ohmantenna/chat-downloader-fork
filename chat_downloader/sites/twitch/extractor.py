# SPDX-License-Identifier: MIT

"""Twitch chat downloader extractor.

This module contains the main TwitchChatDownloader class that handles
downloading chat messages from Twitch VODs, clips, and live streams. It
provides public API methods for retrieving chat by VOD ID, clip ID, or
stream ID.

Logging Strategy:
- logger.debug() for development/API exploration (won't trigger EXIT_ON_DEBUG)
- debug_log() for diagnostic/quality control (will trigger EXIT_ON_DEBUG)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import logger
from chat_downloader.errors import NoChatReplay, SiteError, VideoUnavailable
from chat_downloader.sites.base import BaseChatDownloader

from .constants import VALID_URLS
from .graphql_client import (
    GQL_AUTH_COOKIE_NAME,
    _download_base_gql,
    _download_gql,
    update_badge_info,
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
    from collections.abc import Generator, Iterable

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.models import Chat


class TwitchError(SiteError):
    """Raised when an error occurs with a Twitch video."""


class TwitchChatDownloader(BaseChatDownloader):
    """Main Twitch chat downloader class.

    This class provides methods for downloading chat messages from Twitch VODs,
    clips, and live streams. It handles GraphQL API requests, IRC connections,
    and badge information caching.
    """

    _NAME = "twitch.tv"

    _SITE_DEFAULT_PARAMS = {
        "format": "twitch",
    }

    _VALID_URLS = VALID_URLS

    _TESTS = [
        # Live
        {
            "name": "Livestream",
            "params": {"url": "https://www.twitch.tv/xenova", "timeout": 5},
        },
        # Past broadcasts
        {
            "name": "Past broadcast with chat replay.",
            "params": {
                "url": "https://www.twitch.tv/videos/87136772",
                "max_messages": 30,
            },
            "expected_result": {
                # Exact message_types would be fragile; check useful invariants
                # instead.
                "messages_condition": lambda messages: (
                    len(messages) <= 30
                    and any(
                        m.get("message_type") == "text_message"
                        for m in messages
                    )
                ),
            },
        },
        # Clip
        {
            "name": "Clip with chat replay.",
            "params": {
                "url": "https://clips.twitch.tv/TrappedFrigidPenguinSeemsGood",
            },
            "expected_result": {
                "message_types": ["text_message"],
                "messages_condition": lambda messages: len(messages) > 0,
            },
        },
        {
            "name": (
                "This clip's past broadcast has expired and chat "
                "replay is no longer available."
            ),
            "params": {
                "url": (
                    "https://clips.twitch.tv/AverageSparklyTortoisePeoplesChamp"
                ),
            },
            "expected_result": {"error": NoChatReplay},
        },
        {
            "name": (
                "Sorry. Unless you've got a time machine, that content is "
                "unavailable."
            ),
            "params": {
                "url": "https://www.twitch.tv/videos/1",
            },
            "expected_result": {"error": VideoUnavailable},
        },
    ]

    def __init__(self, **kwargs: Any) -> None:
        """Initialize TwitchChatDownloader with an instance-owned badge
        cache.
        """
        super().__init__(**kwargs)
        self.badge_cache = BadgeCache()
        self._twitch_client_id: str | None = None

    def _update_badge_info(self, channel: str) -> None:
        """Fetch badge data from the Twitch API and update the instance cache.

        The instance-owned :attr:`badge_cache` is mutated in-place.  Parsing
        module globals are intentionally **not** written; all parsing calls
        receive badge data via the explicit ``badge_set`` parameter instead.

        Args:
            channel: Channel name to retrieve badges for
        """
        client_id = getattr(self, "_twitch_client_id", None)
        if client_id is None:
            update_badge_info(
                self._session_post,
                channel,
                _download_gql,
                self.badge_cache.global_badges,
                self.badge_cache.channel_badges,
            )
        else:
            update_badge_info(
                self._session_post,
                channel,
                _download_gql,
                self.badge_cache.global_badges,
                self.badge_cache.channel_badges,
                client_id=client_id,
            )

    def _download_base_gql(self, ops: Any) -> Any:
        """Download GraphQL data using base query.

        Args:
            ops: List of GraphQL operations to execute

        Returns:
            JSON response from GraphQL API
        """
        auth_token: str | None = self.get_cookie_value(GQL_AUTH_COOKIE_NAME)
        client_id = getattr(self, "_twitch_client_id", None)
        if client_id is None:
            return _download_base_gql(self._session_post, ops, auth_token)
        return _download_base_gql(
            self._session_post, ops, auth_token, client_id
        )

    def _download_gql(self, ops: list[dict[str, Any]]) -> Any:
        """Download GraphQL data using persisted query hashes.

        Args:
            ops: List of GraphQL operations to execute

        Returns:
            JSON response from GraphQL API
        """
        auth_token: str | None = self.get_cookie_value(GQL_AUTH_COOKIE_NAME)
        client_id = getattr(self, "_twitch_client_id", None)
        if client_id is None:
            return _download_gql(self._session_post, ops, auth_token)
        return _download_gql(self._session_post, ops, auth_token, client_id)

    def generate_urls(  # type: ignore[override]
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
        yield from generate_twitch_urls(
            self, livestream_limit, vod_limit, clip_limit
        )

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
        match: Any,
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
        match: Any,
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
    ) -> Generator[dict[str, Any], None, None]:
        """Get live chat messages for a stream via IRC.

        Uses Twitch's IRC interface for real-time chat messages. Note that IRC
        is considered a legacy method by Twitch (EventSub is preferred), but
        remains functional for read-only chat access.

        IRC Limitations (per Twitch documentation):
        - Message order is not guaranteed (messages may arrive out-of-sequence)
        - Channels with 1000+ users don't send JOIN/PART messages
        - Only /me command available (other commands require API calls)
        - Rate limits apply (see constants.py for current limits)

        Args:
            stream_id: Channel name
            params: Parameters dictionary

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
        )

    def _get_chat_by_stream_id(
        self,
        match: Any,
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

        This is a public API method for retrieving live chat from an active
        or upcoming stream using Twitch's IRC interface.

        Note: IRC is Twitch's legacy chat method (EventSub is now preferred),
        but remains functional. Messages may arrive out-of-order, and rate
        limits apply (see constants.py).

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
