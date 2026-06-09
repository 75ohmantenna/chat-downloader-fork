# SPDX-License-Identifier: MIT

"""Video metadata parsing mixin for YouTube."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log

from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from .playability import is_age_gated as _mapper_is_age_gated
from .playability import is_unplayable as _mapper_is_unplayable
from .video_status import parse_video_details, video_details_to_dict

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest

    from ._protocols import YouTubeDownloaderProto


class YouTubeVideoMetadataCoreMixin:
    """Methods for parsing and exposing base YouTube video metadata."""

    @staticmethod
    def _is_age_gated(player_response_info: dict[str, Any]) -> bool:
        """Check if video is age-gated based on yt-dlp implementation."""
        return _mapper_is_age_gated(player_response_info)

    @staticmethod
    def _is_unplayable(player_response_info: dict[str, Any]) -> bool:
        """Check if video is marked as unplayable based on yt-dlp mapping."""
        return _mapper_is_unplayable(player_response_info)

    def _parse_video_data(
        self,
        video_id: str,
        params: ChatRequest | None = None,
        video_type: str = "video",
    ) -> tuple[dict[str, Any], Any, Any, Any]:
        """Parse video metadata from YouTube by initial page fetch."""
        if video_type == "clip":
            original_url = f"{_YT_HOME}/clip/{video_id}"
        else:
            original_url = f"{_YT_HOME}/watch?v={video_id}"

        yt_initial_data, ytcfg, player_response_info = _get_initial_info(
            original_url,
            cast("YouTubeDownloaderProto", self)._session_get,
            params,
            _YT_INITIAL_DATA_RE,
            _YT_CFG_RE,
            _YT_INITIAL_PLAYER_RESPONSE_RE,
        )

        if not player_response_info:
            log("debug", yt_initial_data)
            log(
                "warning",
                "Unable to parse player response, proceeding with caution",
            )

        video_details_obj = parse_video_details(
            player_response_info,
            yt_initial_data,
            video_id,
            video_type,
        )
        details = video_details_to_dict(video_details_obj)

        return details, player_response_info, yt_initial_data, ytcfg

    def get_video_data(
        self,
        video_id: str,
        params: ChatRequest | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get video data for a YouTube video."""
        from chat_downloader.models import ChatRequest as ChatRequestModel

        if params is None:
            request = None
        elif isinstance(params, ChatRequestModel):
            request = params
        else:
            request = ChatRequestModel.from_kwargs(**params)
        return self._parse_video_data(video_id, request)[0]
