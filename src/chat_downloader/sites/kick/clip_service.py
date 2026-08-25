# SPDX-License-Identifier: MIT

"""Kick clip metadata resolution and bounded source-VOD chat replay."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, cast

from chat_downloader.debugging import log
from chat_downloader.errors import NoChatReplay
from chat_downloader.sites.models import Chat
from chat_downloader.utils.json_types import JSONDict, get_dict, get_int, get_str
from chat_downloader.utils.time_utils import ensure_seconds

from .constants import is_clip_id, is_numeric_id, is_video_id
from .errors import KickError
from .replay_service import (
    _apply_request_window,
    _fetch_with_retry,
    _iter_vod_messages,
    _resolve_vod_window,
)

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest

    from .api_client import KickApiClient


@dataclass(frozen=True, slots=True)
class _ClipMetadata:
    """Validated fields needed to map a clip onto its source VOD."""

    video_id: str
    channel_id: str
    title: str
    start_offset: float
    duration: float


def _clip_number(
    clip: JSONDict,
    field: str,
    clip_id: str,
    *,
    positive: bool,
) -> float:
    """Return one finite clip number or raise an actionable provider error."""
    value = clip.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        qualifier = "positive" if positive else "non-negative"
        msg = f"Kick clip {clip_id!r} has no finite {qualifier} {field}."
        raise KickError(msg)
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        qualifier = "positive" if positive else "non-negative"
        msg = f"Kick clip {clip_id!r} has no finite {qualifier} {field}."
        raise KickError(msg)
    return float(value)


def _resolve_clip_metadata(data: JSONDict, clip_id: str) -> _ClipMetadata:
    """Validate and return the source-replay fields from clip metadata."""
    clip = get_dict(data, "clip")
    if not clip:
        msg = f"Kick clip {clip_id!r} metadata is missing its clip object."
        raise KickError(msg)

    returned_id = get_str(clip, "id")
    if returned_id != clip_id:
        msg = f"Kick clip {clip_id!r} metadata returned id {returned_id!r}."
        raise KickError(msg)

    vod = get_dict(clip, "vod")
    video_id = get_str(vod, "id")
    if not video_id:
        msg = f"Kick clip {clip_id!r} source VOD is unavailable; no chat replay exists."
        raise NoChatReplay(msg)
    if not is_video_id(video_id):
        msg = f"Kick clip {clip_id!r} returned an invalid source VOD id."
        raise KickError(msg)

    declared_channel_id = get_int(clip, "channel_id")
    nested_channel_id = get_int(get_dict(clip, "channel"), "id")
    if (
        declared_channel_id
        and nested_channel_id
        and declared_channel_id != nested_channel_id
    ):
        msg = f"Kick clip {clip_id!r} returned conflicting channel ids."
        raise KickError(msg)
    channel_id_value = declared_channel_id or nested_channel_id
    channel_id = str(channel_id_value)
    if channel_id_value <= 0 or not is_numeric_id(channel_id):
        msg = f"Kick clip {clip_id!r} is missing a valid channel id."
        raise KickError(msg)

    return _ClipMetadata(
        video_id=video_id,
        channel_id=channel_id,
        title=get_str(clip, "title"),
        start_offset=_clip_number(
            clip,
            "vod_starts_at",
            clip_id,
            positive=False,
        ),
        duration=_clip_number(clip, "duration", clip_id, positive=True),
    )


def _apply_clip_request_window(
    metadata: _ClipMetadata,
    request: ChatRequest,
) -> tuple[float, float]:
    """Resolve caller bounds relative to the clip and clamp to its duration."""
    start = cast("float", ensure_seconds(request.start_time, 0.0))
    end = cast("float", ensure_seconds(request.end_time, metadata.duration))
    return (
        min(max(start, 0.0), metadata.duration),
        min(max(end, 0.0), metadata.duration),
    )


def get_clip_chat(
    username: str,
    clip_id: str,
    request: ChatRequest,
    *,
    api_client: KickApiClient,
) -> Chat:
    """Build a bounded chat replay for a Kick clip."""
    if not is_clip_id(clip_id):
        msg = f"Invalid Kick clip id: {clip_id!r}."
        raise KickError(msg)

    clip_data = _fetch_with_retry(
        lambda: api_client.fetch_clip_metadata(clip_id),
        request,
    )
    metadata = _resolve_clip_metadata(clip_data, clip_id)
    video_data = _fetch_with_retry(
        lambda: api_client.fetch_video_metadata(metadata.video_id),
        request,
    )
    channel_id, _chatroom_id, source_title, vod_start, vod_end = _resolve_vod_window(
        video_data, username
    )
    if channel_id != metadata.channel_id:
        msg = f"Kick clip {clip_id!r} channel does not match its source VOD."
        raise KickError(msg)

    source_duration = max(0.0, (vod_end - vod_start).total_seconds())
    if metadata.start_offset > source_duration:
        msg = f"Kick clip {clip_id!r} starts outside its source VOD."
        raise KickError(msg)

    clip_start, clip_end = _apply_clip_request_window(metadata, request)
    source_request = request.with_updates(
        start_time=metadata.start_offset + clip_start,
        end_time=metadata.start_offset + clip_end,
    )
    start_dt, end_dt = _apply_request_window(vod_start, vod_end, source_request)
    clip_origin = vod_start + timedelta(seconds=metadata.start_offset)
    relative_start = max(0.0, (start_dt - clip_origin).total_seconds())

    log("info", f"Clip time window: {start_dt} to {end_dt}")
    return Chat(
        _iter_vod_messages(
            channel_id,
            start_dt,
            end_dt,
            source_request,
            api_client=api_client,
        ),
        title=metadata.title or source_title,
        duration=max(0.0, (end_dt - start_dt).total_seconds()),
        status="completed",
        video_type="clip",
        start_time=relative_start,
        id=clip_id,
    )
