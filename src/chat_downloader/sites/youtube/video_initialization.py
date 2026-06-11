# SPDX-License-Identifier: MIT

"""Video initialization mixin for YouTube continuation bootstrap."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_utils import try_parse_json
from chat_downloader.utils.string_utils import regex_search

from .constants_patterns import (
    _YT_INITIAL_DATA_RE,
    _YT_LIVE_CHAT_REPLAY_URL,
    _YT_LIVE_CHAT_URL,
)
from .helpers import extract_chat_submenu_continuations
from .playability import raise_if_playability_error
from .video_status_models import REPLAY_STATUSES

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest

    from ._protocols import YouTubeDownloaderProto


class YouTubeVideoInitializationMixin:
    """Enrich video details with continuation bootstrap metadata."""

    def _get_initial_video_info(
        self,
        video_id: str,
        params: ChatRequest | None = None,
        video_type: str = "video",
    ) -> tuple[dict[str, Any], Any]:
        """Get initial YouTube video information and continuation metadata."""
        details, player_response_info, yt_initial_data, ytcfg = cast(
            "YouTubeDownloaderProto", self
        )._parse_video_data(video_id, params, video_type)

        if not yt_initial_data.get("_chat_downloader_continuation_info"):
            # Indigo128 03.11.2025 >>
            # Continuation changes mid October 2025
            try:
                client_continuation = multi_get(
                    yt_initial_data,
                    "contents",
                    "twoColumnWatchNextResults",
                    "conversationBar",
                    "liveChatRenderer",
                    "continuations",
                    0,
                    "reloadContinuationData",
                    "continuation",
                )
                if not client_continuation:
                    msg = "liveChat reload continuation token missing"
                    raise KeyError(msg)  # noqa: TRY301 — intentionally caught by the enclosing except to trigger the fallback-path warning
                proto = cast("YouTubeDownloaderProto", self)
                if details["status"] in REPLAY_STATUSES:
                    response = proto._session_get(
                        f"{_YT_LIVE_CHAT_REPLAY_URL}?continuation={client_continuation}",
                    )
                else:
                    response = proto._session_get(
                        f"{_YT_LIVE_CHAT_URL}?continuation={client_continuation}",
                    )
                html = response.text
                yt = regex_search(html, _YT_INITIAL_DATA_RE)
                dict_live_chats = try_parse_json(yt)
                if details["status"] in REPLAY_STATUSES:
                    fallback_labels = ["Top chat replay", "Live chat replay"]
                    canonical_labels = {
                        "Top chat": "Top chat replay",
                        "Live chat": "Live chat replay",
                    }
                else:
                    fallback_labels = ["Top chat", "Live chat"]
                    canonical_labels = {}

                continuation_info = extract_chat_submenu_continuations(
                    dict_live_chats,
                    fallback_labels=fallback_labels,
                )
                for source_label, token in continuation_info.items():
                    details["continuation_info"][
                        canonical_labels.get(source_label, source_label)
                    ] = token
            except (KeyError, TypeError, IndexError) as exc:
                log(
                    "warning",
                    "Unable to enrich chat submenu continuation tokens from "
                    "chat-page bootstrap "
                    f"({type(exc).__name__}). Falling back to playability "
                    "checks when required.",
                )
            # Indigo128 03.11.2025 <<

        # Error checking — only when there is no continuation info.
        # Some streams can expose chat without video metadata.
        if not details["continuation_info"]:
            raise_if_playability_error(player_response_info, yt_initial_data)

        return details, ytcfg
