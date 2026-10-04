# SPDX-License-Identifier: MIT

"""Video metadata parsing mixin for YouTube."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, cast

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import CaptchaChallengeRequired, ParsingError
from chat_downloader.utils.json_types import get_dict, get_int, get_str

from .client_requests_bootstrap import BootstrapRequests, get_innertube_video_bootstrap
from .client_requests_initial import _get_initial_info
from .constants_patterns import (
    _YT_CFG_RE,
    _YT_HOME,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from .video_status import parse_video_details, video_details_to_dict

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict

    from ._protocols import YouTubeDownloaderProto


class YouTubeVideoMetadataCoreMixin:
    """Methods for parsing and exposing base YouTube video metadata."""

    def _parse_video_data(
        self,
        video_id: str,
        params: ChatRequest | None = None,
        video_type: str = "video",
    ) -> tuple[dict[str, Any], JSONDict, JSONDict, JSONDict]:
        """Parse video metadata from YouTube by initial page fetch."""
        if video_type == "clip":
            original_url = f"{_YT_HOME}/clip/{video_id}"
        else:
            original_url = f"{_YT_HOME}/watch?v={video_id}"

        proto = cast("YouTubeDownloaderProto", self)
        bootstrap = BootstrapRequests()
        initial: tuple[JSONDict, JSONDict, JSONDict] | None = None
        if (
            video_type != "clip"
            and getattr(self, "_request_profile", None)
            in {"youtube_android", "youtube_ios"}
            and not getattr(self, "_has_auth_cookies", False)
        ):
            try:
                initial = get_innertube_video_bootstrap(
                    video_id,
                    partial(bootstrap.request, proto._session_post),
                    getattr(self, "_request_profile", None),
                )
                if (
                    not get_str(get_dict(initial[2], "videoDetails"), "videoId")
                    or not initial[0]
                    or "error" in initial[0]
                    or "error" in initial[2]
                ):
                    msg = "Mobile bootstrap did not supply video details"
                    raise ParsingError(msg)  # noqa: TRY301 - recover through the page
            except (RequestException, OSError, ValueError, ParsingError) as error:
                initial = None
                bootstrap.diagnostics["bootstrap_fallback_count"] = 1
                log(
                    "warning",
                    "Falling back to YouTube watch-page bootstrap after the "
                    f"mobile InnerTube bootstrap failed ({type(error).__name__}).",
                )

        if initial is None:
            try:
                initial = _get_initial_info(
                    original_url,
                    partial(bootstrap.request, proto._session_get),
                    params,
                    _YT_INITIAL_DATA_RE,
                    _YT_CFG_RE,
                    _YT_INITIAL_PLAYER_RESPONSE_RE,
                )
            except (CaptchaChallengeRequired, ParsingError) as error:
                if video_type == "clip":
                    raise
                log(
                    "warning",
                    "Falling back to YouTube InnerTube bootstrap after the "
                    f"watch-page bootstrap failed ({type(error).__name__}).",
                )
                bootstrap.diagnostics["bootstrap_fallback_count"] = (
                    get_int(bootstrap.diagnostics, "bootstrap_fallback_count") + 1
                )
                initial = get_innertube_video_bootstrap(
                    video_id,
                    partial(bootstrap.request, proto._session_post),
                    getattr(self, "_request_profile", None),
                )

        yt_initial_data, ytcfg, player_response_info = initial

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
        ytcfg = {
            **ytcfg,
            "_chat_downloader_bootstrap_diagnostics": cast(
                "JSONDict", bootstrap.diagnostics
            ),
        }

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
