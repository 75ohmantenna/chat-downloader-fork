# SPDX-License-Identifier: MIT

"""Continuation response handling helpers for the YouTube chat loop."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import capture_debug_sample, debug_log, log
from chat_downloader.errors import ChatDownloaderError, NoChatReplay
from chat_downloader.utils.dict_utils import multi_get

from .client_auth import _generate_sapisidhash_header
from .constants_patterns import _YT_HOME
from .continuation_loop import extract_visitor_data

if TYPE_CHECKING:
    from chat_downloader.sites.youtube._protocols import YouTubeDownloaderProto


def _raise_if_api_error(yt_info: dict[str, Any]) -> None:
    """Raise a typed exception when the YouTube API returns an error payload."""
    if "error" not in yt_info:
        return

    error_info = yt_info.get("error")
    if isinstance(error_info, dict):
        error_message = error_info.get("message", "Unknown error")
        error_code = error_info.get("code", "")
    else:
        error_message = str(error_info) if error_info else "Unknown error"
        error_code = ""
    log("debug", f"API error response: {error_info}")

    if str(error_code) == "400":
        msg = f"Chat replay is not available for this video. {error_message}"
        raise NoChatReplay(
            msg,
        )
    msg = f"YouTube API error ({error_code}): {error_message}"
    raise ChatDownloaderError(msg)


def _log_continuation_debug_info(cont_result: Any) -> None:
    """Log parsed continuation details without cluttering the main loop."""
    if not cont_result.debug_info:
        return

    cont_debug = cont_result.debug_info
    if cont_debug.get("unknown"):
        cont_key = cont_debug.get("continuation_key")
        cont_entry = cont_debug.get("continuation_entry", {})
        payload_summary = cont_debug.get("payload_summary")
        capture_debug_sample(
            f"youtube-unknown-continuation-{cont_key or 'unknown'}",
            {
                "continuation_key": cont_key,
                "continuation_entry": cont_entry,
                "payload_summary": payload_summary,
            },
        )
        debug_log(
            f"Unknown continuation: {cont_key}",
            {cont_key: cont_entry},
            {"payload_summary": payload_summary},
        )
        return

    log(
        "debug",
        f"Continuation info: {cont_debug.get('continuation_entry')}",
    )


def _update_visitor_data(
    self: YouTubeDownloaderProto, yt_info: dict[str, Any]
) -> None:
    """Propagate visitor-data from the response into session headers."""
    visitor_data = extract_visitor_data(yt_info)
    if visitor_data:
        self.update_session_headers({"x-goog-visitor-id": visitor_data})
        log("debug", f"Updated visitor data: {visitor_data}")


def _apply_response_state_updates(
    self: YouTubeDownloaderProto,
    yt_info: dict[str, Any],
    auth: str | None = None,
) -> None:
    """Apply response-driven session/header state updates in one place."""
    _update_visitor_data(self, yt_info)
    if auth:
        self.update_session_headers({"authorization": auth})


def _log_request_context(
    self: YouTubeDownloaderProto,
    yt_info: dict[str, Any],
    continuation_params: dict[str, Any],
) -> None:
    """Log continuation parameters, session headers, and login state."""
    debug_info = {
        "click_tracking": multi_get(
            continuation_params,
            "context",
            "clickTracking",
        ),
        "continuation": multi_get(continuation_params, "continuation"),
    }
    log(
        "debug",
        [
            f"Continuation parameters: {debug_info}",
            f"Session headers: {', '.join(self.session.headers.keys())}",
        ],
    )

    logged_in_info = multi_get(
        yt_info,
        "responseContext",
        "serviceTrackingParams",
        1,
        "params",
        0,
    )
    log("debug", f"Logged-in info: {logged_in_info}")


def _handle_continuation_response(
    self: YouTubeDownloaderProto,
    yt_info: dict[str, Any],
    ytcfg: dict[str, Any],
    continuation_params: dict[str, Any],
) -> None:
    """Apply response-driven state, log request context, and raise errors."""
    auth = _generate_sapisidhash_header(self, _YT_HOME, ytcfg)
    _apply_response_state_updates(self, yt_info, auth)
    _log_request_context(self, yt_info, continuation_params)
    _raise_if_api_error(yt_info)
