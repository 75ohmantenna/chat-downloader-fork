# SPDX-License-Identifier: MIT

"""YouTube chat user/channel routing helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.errors import VideoUnavailable
from chat_downloader.utils.json_types import get_dict, get_str

from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _YT_CFG_RE,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.models import Chat

    from ._protocols import YouTubeDownloaderProto


class YouTubeChatUsersRouterMixin:
    """Resolve user/channel route parameters to chat lookup calls."""

    def _get_chat_by_live_user(
        self,
        match: re.Match[str],
        params: ChatRequest,
    ) -> Chat:
        """Resolve a channel live shortcut to its canonical video chat."""
        proto = cast("YouTubeDownloaderProto", self)
        _, _, player_response = _get_initial_info(
            params.url,
            proto._session_get,
            params,
            _YT_INITIAL_DATA_RE,
            _YT_CFG_RE,
            _YT_INITIAL_PLAYER_RESPONSE_RE,
        )
        video_id = get_str(get_dict(player_response, "videoDetails"), "videoId")
        if not video_id or re.fullmatch(r"[0-9A-Za-z_-]{11}", video_id) is None:
            route = match.group(0)
            msg = f"Unable to resolve an active livestream from {route!r}."
            raise VideoUnavailable(msg)

        log("debug", f"Resolved YouTube live URL to video ID: {video_id}")
        return proto.get_chat_by_video_id(video_id, params)

    def _get_chat_by_user(self, match: re.Match[str], params: ChatRequest) -> Chat:
        """Get chat by user from regex match."""
        match_id = match.group("id")
        user_type = match.group("type") or ""
        user_type = user_type.rstrip("/")  # channel|c|user|@|

        match user_type:
            case "channel":
                return self.get_chat_by_channel_id(match_id, params)
            case "user":
                return self.get_chat_by_user_id(match_id, params)
            case "c" | "":
                return self.get_chat_by_custom_username(match_id, params)
            case "@":
                return self.get_chat_by_handle(match_id, params)
            case _:
                msg = f"Invalid user_type: {user_type}"
                raise ValueError(msg)

    def get_chat_by_channel_id(
        self, channel_id: str, params: ChatRequest | dict[str, Any]
    ) -> Chat:
        """Get chat by channel ID."""
        return cast("YouTubeDownloaderProto", self)._get_chat_by_user_args(
            {"channel_id": channel_id}, params
        )

    def get_chat_by_user_id(
        self, user_id: str, params: ChatRequest | dict[str, Any]
    ) -> Chat:
        """Get chat by user ID.

        Such as NASAtelevision in
        https://www.youtube.com/user/NASAtelevision
        """
        return cast("YouTubeDownloaderProto", self)._get_chat_by_user_args(
            {"user_id": user_id}, params
        )

    def get_chat_by_custom_username(
        self,
        custom_username: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Get chat by custom username."""
        return cast("YouTubeDownloaderProto", self)._get_chat_by_user_args(
            {"custom_username": custom_username}, params
        )

    def get_chat_by_handle(
        self, handle: str, params: ChatRequest | dict[str, Any]
    ) -> Chat:
        """Get chat by handle."""
        return cast("YouTubeDownloaderProto", self)._get_chat_by_user_args(
            {"handle": handle}, params
        )
