# SPDX-License-Identifier: MIT

"""Video initialization mixin for YouTube continuation bootstrap."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.request_profiles import get_next_request_profile
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict, get_str
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
    from chat_downloader.utils.json_types import JSONDict

    from ._protocols import YouTubeDownloaderProto


_GENERIC_UNPLAYABLE_REASONS = frozenset(
    {"", "video unavailable", "video is unavailable", "video is unplayable"}
)


def _has_generic_unplayable_reason(player_response: JSONDict) -> bool:
    """Return whether a profile retry could clarify a generic failure."""
    playability = get_dict(player_response, "playabilityStatus")
    if get_str(playability, "status") != "UNPLAYABLE":
        return False
    reason = (get_str(playability, "reason") or "").strip().rstrip(".").casefold()
    return reason in _GENERIC_UNPLAYABLE_REASONS


class YouTubeVideoInitializationMixin:
    """Enrich video details with continuation bootstrap metadata."""

    def _get_initial_video_info(
        self,
        video_id: str,
        params: ChatRequest | None = None,
        video_type: str = "video",
    ) -> tuple[dict[str, Any], Any]:
        """Get initial YouTube video information and continuation metadata."""
        proto = cast("YouTubeDownloaderProto", self)
        attempted_profiles: set[str] = set()

        while True:
            details, player_response_info, yt_initial_data, ytcfg = (
                proto._parse_video_data(video_id, params, video_type)
            )

            if not yt_initial_data.get("_chat_downloader_continuation_info"):
                # Refresh submenu tokens through the chat-page bootstrap.
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
                    is_replay = details["status"] in REPLAY_STATUSES
                    chat_url = (
                        _YT_LIVE_CHAT_REPLAY_URL if is_replay else _YT_LIVE_CHAT_URL
                    )
                    response = proto._session_get(
                        f"{chat_url}?continuation={client_continuation}",
                    )
                    dict_live_chats = try_parse_json(
                        regex_search(response.text, _YT_INITIAL_DATA_RE)
                    )
                    canonical_labels = {
                        label: f"{label} replay" if is_replay else label
                        for label in ("Top chat", "Live chat")
                    }

                    continuation_info = extract_chat_submenu_continuations(
                        dict_live_chats,
                        fallback_labels=list(canonical_labels.values()),
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

            if details["continuation_info"]:
                return details, ytcfg

            next_profile = (
                get_next_request_profile(proto._request_profile, site="youtube")
                if getattr(proto, "_auto_profile_fallback", False)
                and _has_generic_unplayable_reason(player_response_info)
                else None
            )
            if (
                next_profile is None
                or next_profile in attempted_profiles
                or not proto.apply_request_profile(next_profile)
            ):
                raise_if_playability_error(player_response_info, yt_initial_data)
                return details, ytcfg

            attempted_profiles.add(next_profile)
            log(
                "warning",
                "Switching YouTube request profile after a generic initial "
                f"playability response: {next_profile}",
            )
