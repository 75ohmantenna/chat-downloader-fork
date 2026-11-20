# SPDX-License-Identifier: MIT

"""Video initialization mixin for YouTube continuation bootstrap."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.debugging import log
from chat_downloader.errors import ParsingError
from chat_downloader.request_profiles import get_next_request_profile
from chat_downloader.sites.base import request_diagnostics_scope
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict, get_str

from .bootstrap_diagnostics import (
    BootstrapRequests,
    merge_bootstrap_diagnostics,
    publish_bootstrap_snapshot,
)
from .client_requests_bootstrap import request_bootstrap_json
from .client_requests_initial import _BootstrapScriptParser
from .constants_patterns import (
    _YT_INITIAL_DATA_RE,
    _YT_LIVE_CHAT_REPLAY_URL,
    _YT_LIVE_CHAT_URL,
)
from .helpers import extract_chat_submenu_continuations
from .playability import _player_captcha_error, raise_if_playability_error
from .video_status_models import REPLAY_STATUSES

if TYPE_CHECKING:
    import requests

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.base import CaptureDiagnostics
    from chat_downloader.utils.json_types import JSONDict

    from ._protocols import YouTubeDownloaderProto


_VideoDetails = dict[str, Any]


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


def _parse_chat_bootstrap(response: requests.Response) -> JSONDict:
    data = _BootstrapScriptParser(response.text).assignment(_YT_INITIAL_DATA_RE)
    if not extract_chat_submenu_continuations(
        data, fallback_labels=["Top chat", "Live chat"]
    ):
        msg = "YouTube chat-page bootstrap did not supply submenu tokens"
        raise ParsingError(msg)
    return data


def bootstrap_video(
    site: YouTubeDownloaderProto,
    video_id: str,
    request: ChatRequest,
    capture: CaptureDiagnostics,
    *,
    video_type: str = "video",
) -> tuple[_VideoDetails, JSONDict]:
    """Bootstrap with eager counters and fold in any untracked snapshot data."""
    before = capture.data.copy()
    with request_diagnostics_scope(site, request, capture):
        if video_type == "clip":
            details, config = site._get_initial_video_info(
                video_id, request, video_type="clip"
            )
        else:
            details, config = site._get_initial_video_info(video_id, request)
    publish_bootstrap_snapshot(
        capture, before, get_dict(config, "_chat_downloader_bootstrap_diagnostics")
    )
    return details, config


class YouTubeVideoInitializationMixin:
    """Enrich video details with continuation bootstrap metadata."""

    def _get_initial_video_info(
        self,
        video_id: str,
        params: ChatRequest | None = None,
        video_type: str = "video",
    ) -> tuple[_VideoDetails, JSONDict]:
        """Get initial YouTube video information and continuation metadata."""
        proto = cast("YouTubeDownloaderProto", self)
        attempted_profiles: set[str] = set()
        bootstrap = BootstrapRequests.for_request(self, params)
        player_captcha = None

        while True:
            bootstrap.profile(getattr(proto, "_request_profile", None) or "youtube_web")
            details, player_response_info, yt_initial_data, ytcfg = (
                proto._parse_video_data(video_id, params, video_type)
            )
            merge_bootstrap_diagnostics(
                bootstrap.diagnostics,
                get_dict(ytcfg, "_chat_downloader_bootstrap_diagnostics"),
            )
            bootstrap.diagnostics["active_request_profile"] = (
                getattr(proto, "_request_profile", None) or "youtube_web"
            )
            ytcfg = {
                **ytcfg,
                "_chat_downloader_bootstrap_diagnostics": cast(
                    "JSONDict", bootstrap.diagnostics
                ),
            }

            playability = get_dict(player_response_info, "playabilityStatus")
            player_captcha = (
                _player_captcha_error(get_dict(playability, "errorScreen"))
                or player_captcha
            )
            unknown_status = details["status"] == "unknown"
            if (
                unknown_status
                and get_str(playability, "status") not in {"", "OK"}
                and not _has_generic_unplayable_reason(player_response_info)
            ):
                raise_if_playability_error(
                    player_response_info, yt_initial_data, player_captcha=player_captcha
                )
            if not unknown_status and not yt_initial_data.get(
                "_chat_downloader_continuation_info"
            ):
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
                    dict_live_chats = request_bootstrap_json(
                        partial(bootstrap.request, proto._session_get),
                        f"{chat_url}?continuation={client_continuation}",
                        _parse_chat_bootstrap,
                        params,
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
                except (KeyError, TypeError, IndexError, ParsingError) as exc:
                    log(
                        "warning",
                        "Unable to enrich chat submenu continuation tokens from "
                        "chat-page bootstrap "
                        f"({type(exc).__name__}). Falling back to playability "
                        "checks when required.",
                    )

            bootstrap_unplayable = bool(
                yt_initial_data.get("_chat_downloader_continuation_info")
            ) and _has_generic_unplayable_reason(player_response_info)
            if (
                details["continuation_info"]
                and not bootstrap_unplayable
                and not unknown_status
            ):
                return details, ytcfg

            next_profile = (
                get_next_request_profile(proto._request_profile, site="youtube")
                if getattr(proto, "_auto_profile_fallback", False)
                and (
                    unknown_status
                    or _has_generic_unplayable_reason(player_response_info)
                )
                else None
            )
            if (
                next_profile is None
                or next_profile in attempted_profiles
                or not proto.apply_request_profile(next_profile)
            ):
                if unknown_status:
                    msg = (
                        "YouTube bootstrap did not establish live or replay status. "
                        "Try a fresh retrieval with --request_profile youtube_android "
                        "or youtube_ios, or another proxy."
                    )
                    raise player_captcha or ParsingError(msg)
                raise_if_playability_error(
                    player_response_info, yt_initial_data, player_captcha=player_captcha
                )
                return details, ytcfg

            attempted_profiles.add(next_profile)
            bootstrap.increment("bootstrap_profile_switch_count")
            log(
                "warning",
                "Switching YouTube request profile after an incomplete or generic "
                f"initial playability response: {next_profile}",
            )
