# SPDX-License-Identifier: MIT

"""Playability classification and error handling for YouTube internals."""

from __future__ import annotations

from typing import TYPE_CHECKING

from chat_downloader.debugging import debug_log, log
from chat_downloader.errors import (
    ChatDisabled,
    LoginRequired,
    NoChatReplay,
    VideoUnavailable,
    VideoUnplayable,
)
from chat_downloader.utils.dict_utils import multi_get, try_get_first_value
from chat_downloader.utils.json_types import get_dict, get_str

from .parsing.messages import _parse_runs

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict


def is_age_gated(player_response_info: JSONDict) -> bool:
    """Return ``True`` when the video appears to be age-gated."""
    playability_status = get_dict(player_response_info, "playabilityStatus")

    if playability_status.get("desktopLegacyAgeGateReason"):
        return True

    status = get_str(playability_status, "status")
    reason = get_str(playability_status, "reason")

    age_gate_keywords = (
        "confirm your age",
        "age-restricted",
        "inappropriate",
        "age_verification_required",
        "age_check_required",
    )

    return any(
        keyword in str(field).lower()
        for keyword in age_gate_keywords
        for field in (status, reason)
    )


def is_unplayable(player_response_info: JSONDict) -> bool:
    """Return ``True`` when the playability status is ``UNPLAYABLE``."""
    playability_status = get_dict(player_response_info, "playabilityStatus")
    return get_str(playability_status, "status") == "UNPLAYABLE"


def _build_error_message(
    error_info: JSONDict,
    playability_status: JSONDict,
) -> str:
    """Build a human-readable error message from an ``errorScreen`` entry."""
    error_reasons: JSONDict = {"reason": "", "subreason": ""}

    for error_reason in error_reasons:
        text = get_dict(error_info, error_reason)
        error_reasons[error_reason] = (
            text.get("simpleText")
            or _parse_runs(text, parse_links=False)["message"]
            or error_info.pop("itemTitle", "")
            or error_info.pop("offerDescription", "")
            or playability_status.get(error_reason)
            or ""
        )

    message = ""
    for value in error_reasons.values():
        if value:
            if isinstance(value, str):
                message += f" {value.rstrip('.')}."
            else:
                message += str(value)

    return message.strip()


def _raise_for_early_playability(
    playability_status: JSONDict,
    player_response_info: JSONDict,
    error_screen: JSONDict,
) -> None:
    """Raise for age-gate, unplayable, and CAPTCHA before error-screen parse."""
    if is_age_gated(player_response_info):
        debug_log("Video detected as age-gated")
        msg = (
            "This video is age-restricted. "
            "Age-restricted videos may not have accessible chat."
        )
        raise VideoUnavailable(msg)

    if is_unplayable(player_response_info):
        debug_log("Video detected as unplayable")
        reason = playability_status.get("reason", "Video is unplayable")
        raise VideoUnplayable(reason)

    if "playerCaptchaViewModel" in error_screen:
        msg = (
            "YouTube requires CAPTCHA verification before playback. "
            "Please verify the CAPTCHA in your browser and try again with "
            "fresh cookies."
        )
        raise VideoUnavailable(msg)


def _raise_for_status(
    status: str | None,
    error_message: str,
    playability_status: JSONDict,
) -> None:
    """Dispatch to the correct exception based on playability *status*."""
    match status:
        case "ERROR":
            raise VideoUnavailable(error_message)
        case "LOGIN_REQUIRED":
            raise LoginRequired(error_message)
        case "UNPLAYABLE":
            raise VideoUnplayable(error_message)
        case "LIVE_STREAM_OFFLINE":
            raise ChatDisabled(error_message)
        case _:
            log(
                "debug",
                f"Unknown playability status: {status}. {playability_status}",
            )
            msg = f"{status}: {error_message}"
            raise VideoUnavailable(msg)


def _raise_for_error_screen(
    playability_status: JSONDict,
    player_response_info: JSONDict,
) -> None:
    """Raise for age-gated or status-based error-screen cases."""
    error_screen = get_dict(playability_status, "errorScreen")

    _raise_for_early_playability(playability_status, player_response_info, error_screen)

    if not error_screen:
        return

    error_info = try_get_first_value(error_screen)
    error_message = _build_error_message(error_info, playability_status)
    if "This content isn't available, try again later" in error_message:
        error_message = (
            f"{error_message} "
            "This video has been rate-limited by YouTube for up to an hour. "
            "It is recommended to add delays between requests or try again "
            "later."
        )

    _raise_for_status(
        get_str(playability_status, "status") or None,
        error_message,
        playability_status,
    )


def _raise_for_popup(yt_initial_data: JSONDict) -> None:
    popup_info = multi_get(
        yt_initial_data,
        "onResponseReceivedActions",
        0,
        "openPopupAction",
        "popup",
        "confirmDialogRenderer",
    )
    if not popup_info:
        return

    error_message = multi_get(popup_info, "title", "simpleText") or ""
    dialog_messages = multi_get(popup_info, "dialogMessages") or []
    error_message += ". " + " ".join(x.get("simpleText") or "" for x in dialog_messages)
    raise VideoUnavailable(error_message)


def _raise_for_replay_unavailable(yt_initial_data: JSONDict) -> None:
    if not yt_initial_data.get("contents"):
        log("debug", f"Initial YouTube data: {yt_initial_data}")
        msg = "Unable to find initial video contents."
        raise VideoUnavailable(msg)

    error_runs = multi_get(
        yt_initial_data,
        "contents",
        "twoColumnWatchNextResults",
        "conversationBar",
        "conversationBarRenderer",
        "availabilityMessage",
        "messageRenderer",
        "text",
    )
    error_message = (
        _parse_runs(error_runs, parse_links=False)["message"]
        if error_runs
        else "Video does not have a chat replay."
    )

    lowered = error_message.lower()
    if "members" in lowered or "membership" in lowered:
        # Per errors.py, VideoUnplayable is the documented exception for
        # member-only chats. Fast-fail instead of letting the continuation
        # loop spin against an inaccessible chat surface.
        raise VideoUnplayable(error_message)
    if "disabled" in error_message:
        raise ChatDisabled(error_message)
    raise NoChatReplay(error_message)


def raise_if_playability_error(
    player_response_info: JSONDict,
    yt_initial_data: JSONDict,
) -> None:
    """Inspect playability state and raise the mapped domain exception."""
    playability_status = get_dict(player_response_info, "playabilityStatus")

    _raise_for_error_screen(playability_status, player_response_info)
    _raise_for_popup(yt_initial_data)
    _raise_for_replay_unavailable(yt_initial_data)


__all__ = ["is_age_gated", "is_unplayable", "raise_if_playability_error"]
