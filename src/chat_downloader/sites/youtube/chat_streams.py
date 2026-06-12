# SPDX-License-Identifier: MIT

"""Chat stream retrieval mixin for YouTube video/clip chat fetching."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.sites.models import Chat
from chat_downloader.utils.time_utils import ensure_seconds

from .chat_streams_runtime_iteration import _get_chat_messages

if TYPE_CHECKING:
    import re
    from collections.abc import Generator

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict

    from ._protocols import YouTubeDownloaderProto


class YouTubeChatStreamsMixin:
    """Methods for fetching chat streams from videos and clips."""

    def _get_chat_messages(
        self,
        initial_info: dict[str, Any],
        ytcfg: JSONDict,
        params: ChatRequest,
    ) -> Generator[JSONDict, None, None]:
        """Yield chat messages from a YouTube continuation endpoint."""
        return _get_chat_messages(
            cast("YouTubeDownloaderProto", self), initial_info, ytcfg, params
        )

    def _get_chat_by_clip_id(self, match: re.Match[str], params: ChatRequest) -> Chat:
        """Get chat by clip ID from regex match."""
        return self.get_chat_by_clip_id(match.group("id"), params)

    def get_chat_by_clip_id(
        self, clip_id: str, params: ChatRequest | dict[str, Any]
    ) -> Chat:
        """Get chat messages for a YouTube clip."""
        proto = cast("YouTubeDownloaderProto", self)
        request = proto._coerce_chat_request(params)
        initial_info, ytcfg = proto._get_initial_video_info(
            clip_id,
            request,
            video_type="clip",
        )

        initial_info["offset"] = clip_start_time = initial_info.get("clip_start_time")
        clip_end_time = initial_info.get("clip_end_time")

        if clip_start_time is None or clip_end_time is None:
            from chat_downloader.errors import ParsingError

            msg = f"Could not determine clip time range for clip {clip_id!r}"
            raise ParsingError(msg)

        max_duration = clip_end_time - clip_start_time

        request = request.with_updates(
            start_time=ensure_seconds(request.start_time, 0) + clip_start_time,
            end_time=ensure_seconds(request.end_time, max_duration) + clip_start_time,
        )

        return Chat(
            self._get_chat_messages(initial_info, ytcfg, request),
            id=clip_id,
            **initial_info,
        )

    def get_chat_by_video_id(
        self, video_id: str, params: ChatRequest | dict[str, Any]
    ) -> Chat:
        """Get chat messages for a YouTube video, given its ID."""
        proto = cast("YouTubeDownloaderProto", self)
        request = proto._coerce_chat_request(params)
        initial_info, ytcfg = proto._get_initial_video_info(video_id, request)

        return Chat(
            self._get_chat_messages(initial_info, ytcfg, request),
            id=video_id,
            **initial_info,
        )

    def _get_chat_by_video_id(self, match: re.Match[str], params: ChatRequest) -> Chat:
        """Get chat by video ID from regex match."""
        return self.get_chat_by_video_id(match.group("id"), params)
