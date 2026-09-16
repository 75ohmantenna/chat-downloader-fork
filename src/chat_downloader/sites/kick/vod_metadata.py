# SPDX-License-Identifier: MIT

"""Resolve legacy and current Kick VOD identities without guessing aliases."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from chat_downloader.debugging import log
from chat_downloader.utils.json_types import JSONDict, get_dict, get_str

from .constants import is_numeric_id, is_video_id
from .errors import KickError, KickVideoNotFound
from .request_retry import fetch_with_retry

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest

    from .api_client import KickApiClient


def fetch_vod_metadata(
    api_client: KickApiClient,
    username: str,
    video_id: str,
    request: ChatRequest,
) -> JSONDict:
    """Prefer legacy metadata; only a video 404 activates the web fallback.

    The web gateway uses different video identities and seconds for duration.
    Normalize only after checking identity, ownership, and public replay state.
    Challenges, region restrictions, and transient errors retain their policy.
    """
    try:
        legacy = fetch_with_retry(
            lambda: api_client.fetch_video_metadata(video_id), request
        )
    except KickVideoNotFound:
        log("info", "Kick legacy video metadata returned 404; trying website metadata.")
    else:
        returned_id = get_str(legacy, "uuid")
        owner = get_dict(get_dict(legacy, "livestream"), "channel")
        slug = get_str(owner, "slug")
        if (returned_id and returned_id.casefold() != video_id.casefold()) or (
            slug and slug.casefold() != username.casefold()
        ):
            msg = "Kick legacy metadata returned a different video or channel."
            raise KickError(msg)
        return legacy
    if not is_video_id(video_id):
        msg = "Kick web video requires a canonical video ID."
        raise KickError(msg)
    channel = fetch_with_retry(lambda: api_client.fetch_channel(username), request)
    channel_id = str(channel.get("id", ""))
    if (
        not is_numeric_id(channel_id)
        or get_str(channel, "slug").casefold() != username.casefold()
    ):
        msg = "Kick channel metadata did not match the requested channel."
        raise KickError(msg)
    payload = fetch_with_retry(
        lambda: api_client.fetch_web_video_metadata(channel_id, video_id), request
    )
    video = get_dict(payload, "data")
    owner = get_dict(video, "channel")
    if (
        get_str(video, "id").casefold() != video_id.casefold()
        or str(owner.get("id", "")) != channel_id
        or get_str(owner, "slug").casefold() != username.casefold()
    ):
        msg = "Kick web video metadata returned a different video or channel."
        raise KickError(msg)
    if video.get("status") != "public" or video.get("is_live") is not False:
        msg = "Kick web video is not a public completed recording."
        raise KickError(msg)
    duration = video.get("duration")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        msg = "Kick web video has no finite positive duration."
        raise KickError(msg)
    _log_end_disagreement(video, duration)
    return {
        "uuid": video_id,
        "livestream": {
            "channel": owner,
            "session_title": video.get("title"),
            "start_time": video.get("start_time"),
            "duration": duration * 1000,
        },
    }


def _parse_vod_start(livestream: JSONDict, username: str) -> datetime:
    """Parse one provider VOD start and normalize it to UTC."""
    start_time_raw = livestream.get("start_time")
    if not isinstance(start_time_raw, str):
        msg = f"Kick video for {username!r} is missing a start_time."
        raise KickError(msg)
    try:
        start_dt = datetime.fromisoformat(start_time_raw)
    except (ValueError, TypeError, OverflowError) as error:
        msg = f"Kick video for {username!r} has an unparsable start_time: {error}"
        raise KickError(msg) from error

    if start_dt.tzinfo is None:
        return start_dt.replace(tzinfo=UTC)
    try:
        return start_dt.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        msg = f"Kick video for {username!r} has an unusable start_time."
        raise KickError(msg) from error


def _resolve_vod_window(
    data: JSONDict, username: str
) -> tuple[str, str, str, datetime, datetime]:
    """Resolve the channel id, title, and VOD time window from video metadata.

    Args:
        data: Video metadata object.
        username: Channel username/slug.

    Returns:
        A ``(channel_id, chatroom_id, title, start_dt, end_dt)`` tuple.

    Raises:
        KickError: If required fields are missing.
    """
    livestream = data.get("livestream")
    if not isinstance(livestream, dict):
        msg = f"Kick video for {username!r} has no associated livestream data."
        raise KickError(msg)

    channel = livestream.get("channel")
    channel_id = str(channel.get("id")) if isinstance(channel, dict) else None
    if not channel_id:
        msg = f"Kick video for {username!r} is missing a channel id."
        raise KickError(msg)
    if not is_numeric_id(channel_id):
        msg = f"Kick video for {username!r} returned a non-numeric channel id."
        raise KickError(msg)

    chatroom_id = ""
    if isinstance(channel, dict):
        chatroom_data = channel.get("chatroom")
        if isinstance(chatroom_data, dict):
            chatroom_id = str(chatroom_data.get("id", ""))

    title = str(livestream.get("session_title", username))

    start_dt = _parse_vod_start(livestream, username)

    duration_ms = livestream.get("duration", 0)
    if (
        isinstance(duration_ms, bool)
        or not isinstance(duration_ms, (int, float))
        or not math.isfinite(duration_ms)
        or duration_ms <= 0
    ):
        msg_0 = f"Kick video for {username!r} has no finite positive duration."
        raise KickError(msg_0)
    duration_seconds = duration_ms / 1000
    try:
        end_dt = start_dt + timedelta(seconds=duration_seconds)
    except (ValueError, OverflowError) as error:
        msg = f"Kick video for {username!r} has an unusable duration."
        raise KickError(msg) from error

    return channel_id, chatroom_id, title, start_dt, end_dt


def _log_end_disagreement(video: JSONDict, duration: float) -> None:
    """Explain inconsistent optional end metadata without changing the window."""
    try:
        start = datetime.fromisoformat(get_str(video, "start_time"))
        end = datetime.fromisoformat(get_str(video, "end_time"))
        difference = (end - start).total_seconds() - duration
    except (ValueError, TypeError):
        return
    if abs(difference) > 1:
        log(
            "info",
            "Kick metadata end_time differs from start + duration "
            f"by {difference:.1f}s; "
            "using duration for the replay cutoff.",
        )
