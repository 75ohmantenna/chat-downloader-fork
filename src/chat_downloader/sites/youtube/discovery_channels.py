# SPDX-License-Identifier: MIT

"""Channel/user discovery mixin for YouTube."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .discovery_channels_runtime_iteration import get_user_videos

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.models import ChatRequest


class YouTubeChannelDiscoveryMixin:
    """Methods for channel/user discovery and pagination."""

    def get_user_videos(
        self,
        channel_id: str | None = None,
        user_id: str | None = None,
        custom_username: str | None = None,
        handle: str | None = None,
        video_type: str = "videos",
        params: ChatRequest | dict[str, Any] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Retrieve all videos listed on the user's channel."""
        if params is None:
            request = None
        else:
            coerce = self._coerce_chat_request  # type: ignore[attr-defined]
            request = coerce(params)
        yield from get_user_videos(
            self,
            channel_id=channel_id,
            user_id=user_id,
            custom_username=custom_username,
            handle=handle,
            video_type=video_type,
            params=request,
        )


__all__ = ["YouTubeChannelDiscoveryMixin"]
