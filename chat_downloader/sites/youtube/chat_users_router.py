# SPDX-License-Identifier: MIT

"""YouTube chat user/channel routing helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.models import Chat


class YouTubeChatUsersRouterMixin:
    """Methods for resolving user/channel route parameters to chat lookup
    calls.
    """

    def _get_chat_by_user(
        self, match: re.Match[str], params: ChatRequest
    ) -> Chat:
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
        return self._get_chat_by_user_args(  # type: ignore[attr-defined]
            {"channel_id": channel_id}, params
        )

    def get_chat_by_user_id(
        self, user_id: str, params: ChatRequest | dict[str, Any]
    ) -> Chat:
        """Get chat by user ID.

        Such as NASAtelevision in
        https://www.youtube.com/user/NASAtelevision
        """
        return self._get_chat_by_user_args(  # type: ignore[attr-defined]
            {"user_id": user_id}, params
        )

    def get_chat_by_custom_username(
        self,
        custom_username: str,
        params: ChatRequest | dict[str, Any],
    ) -> Chat:
        """Get chat by custom username."""
        return self._get_chat_by_user_args(  # type: ignore[attr-defined]
            {"custom_username": custom_username}, params
        )

    def get_chat_by_handle(
        self, handle: str, params: ChatRequest | dict[str, Any]
    ) -> Chat:
        """Get chat by handle."""
        return self._get_chat_by_user_args(  # type: ignore[attr-defined]
            {"handle": handle}, params
        )
