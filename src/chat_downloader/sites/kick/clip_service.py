# SPDX-License-Identifier: MIT

"""Kick web/mobile clip metadata resolution and bounded chat replay."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from chat_downloader.debugging import log
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    NoChatReplay,
    RetriesExceeded,
)
from chat_downloader.sites.models import Chat
from chat_downloader.utils.json_types import JSONDict, get_dict, get_int, get_str
from chat_downloader.utils.time_utils import ensure_seconds

from .constants import (
    MOBILE_CLIP_MAX_DURATION_SECONDS,
    is_clip_id,
    is_numeric_id,
    is_video_id,
)
from .errors import KickError
from .replay_service import (
    _apply_request_window,
    _iter_vod_messages,
    _resolve_vod_window,
)
from .request_retry import fetch_with_retry

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


@dataclass(frozen=True, slots=True)
class _MobileClipMetadata:
    """Validated mobile fields needed for direct timestamp replay."""

    channel_id: str
    title: str
    started_at: datetime
    duration: float


class _WebClipMetadataUnavailable(KickError):
    """Signal that mobile metadata may replace an incomplete web contract."""

    def __init__(
        self,
        message: str,
        *,
        channel_id: str | None = None,
        duration: float | None = None,
    ) -> None:
        super().__init__(message)
        self.channel_id = channel_id
        self.duration = duration


_MOBILE_CLIP_INVALID_STARTS = frozenset(
    {
        datetime.min.replace(tzinfo=UTC),
        datetime(1970, 1, 1, tzinfo=UTC),
    }
)


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


def _web_clip_channel_id(clip: JSONDict, clip_id: str) -> str | None:
    """Return a valid web channel id, preserving disagreement as terminal."""
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
        return None
    return channel_id


def _web_clip_duration(clip: JSONDict, clip_id: str) -> float | None:
    """Return a valid web duration when partial metadata still provides one."""
    try:
        return _clip_number(clip, "duration", clip_id, positive=True)
    except KickError:
        return None


def _resolve_clip_metadata(data: JSONDict, clip_id: str) -> _ClipMetadata:
    """Validate and return the source-replay fields from clip metadata."""
    clip = get_dict(data, "clip")
    if not clip:
        msg = f"Kick clip {clip_id!r} metadata is missing its clip object."
        raise _WebClipMetadataUnavailable(msg)

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

    channel_id = _web_clip_channel_id(clip, clip_id)
    if channel_id is None:
        msg = f"Kick clip {clip_id!r} is missing a valid channel id."
        raise _WebClipMetadataUnavailable(
            msg,
            duration=_web_clip_duration(clip, clip_id),
        )

    try:
        duration = _clip_number(clip, "duration", clip_id, positive=True)
    except KickError as error:
        raise _WebClipMetadataUnavailable(
            str(error),
            channel_id=channel_id,
        ) from error
    try:
        start_offset = _clip_number(
            clip,
            "vod_starts_at",
            clip_id,
            positive=False,
        )
    except KickError as error:
        raise _WebClipMetadataUnavailable(
            str(error),
            channel_id=channel_id,
            duration=duration,
        ) from error

    return _ClipMetadata(
        video_id=video_id,
        channel_id=channel_id,
        title=get_str(clip, "title"),
        start_offset=start_offset,
        duration=duration,
    )


def _resolve_mobile_clip_metadata(
    data: JSONDict,
    clip_id: str,
) -> _MobileClipMetadata:
    """Validate the mobile clip shape used when web metadata is unavailable."""
    clip = get_dict(data, "data")
    if not clip:
        msg = f"Kick mobile clip {clip_id!r} metadata is missing its data object."
        raise KickError(msg)

    returned_id = get_str(clip, "id")
    if returned_id != clip_id:
        msg = f"Kick mobile clip {clip_id!r} metadata returned id {returned_id!r}."
        raise KickError(msg)

    channel_id_value = get_int(get_dict(clip, "channel"), "id")
    channel_id = str(channel_id_value)
    if channel_id_value <= 0 or not is_numeric_id(channel_id):
        msg = f"Kick mobile clip {clip_id!r} is missing a valid channel id."
        raise KickError(msg)

    started_at_raw = get_str(clip, "started_at")
    try:
        started_at = datetime.fromisoformat(started_at_raw)
    except (ValueError, OverflowError) as error:
        msg = f"Kick mobile clip {clip_id!r} has an invalid started_at."
        raise KickError(msg) from error
    if started_at.tzinfo is None:
        msg = f"Kick mobile clip {clip_id!r} has an invalid started_at."
        raise KickError(msg)
    try:
        started_at = started_at.astimezone(UTC)
    except (ValueError, OverflowError) as error:
        msg = f"Kick mobile clip {clip_id!r} has an invalid started_at."
        raise KickError(msg) from error
    if started_at in _MOBILE_CLIP_INVALID_STARTS:
        msg = f"Kick mobile clip {clip_id!r} has an unusable started_at sentinel."
        raise KickError(msg)

    duration = _clip_number(clip, "duration", clip_id, positive=True)
    if duration > MOBILE_CLIP_MAX_DURATION_SECONDS:
        msg = (
            f"Kick mobile clip {clip_id!r} exceeds the provider's "
            f"{MOBILE_CLIP_MAX_DURATION_SECONDS}-second duration limit."
        )
        raise KickError(msg)
    try:
        started_at + timedelta(seconds=duration)
    except OverflowError as error:
        msg = f"Kick mobile clip {clip_id!r} has an unusable time window."
        raise KickError(msg) from error

    return _MobileClipMetadata(
        channel_id=channel_id,
        title=get_str(clip, "title"),
        started_at=started_at,
        duration=duration,
    )


def _fetch_mobile_clip_metadata(
    api_client: KickApiClient,
    clip_id: str,
    request: ChatRequest,
    *,
    primary_error: Exception,
    expected_channel_id: str | None,
    expected_duration: float | None,
) -> _MobileClipMetadata:
    """Fetch mobile metadata, retaining and reconciling primary evidence."""
    log(
        "debug",
        "Kick web clip replay metadata was unavailable; trying the mobile "
        f"endpoint ({type(primary_error).__name__}).",
    )
    try:
        mobile_data = fetch_with_retry(
            lambda: api_client.fetch_mobile_clip_metadata(clip_id),
            request,
        )
        metadata = _resolve_mobile_clip_metadata(mobile_data, clip_id)
    except (CaptchaChallengeRequired, KickError, RetriesExceeded) as error:
        raise error from primary_error
    if expected_channel_id and metadata.channel_id != expected_channel_id:
        msg = (
            f"Kick clip {clip_id!r} web and mobile metadata returned "
            "different channel ids."
        )
        raise KickError(msg) from primary_error
    if expected_duration is not None and metadata.duration != expected_duration:
        msg = (
            f"Kick clip {clip_id!r} web and mobile metadata returned "
            "different durations."
        )
        raise KickError(msg) from primary_error
    return metadata


def _fetch_clip_metadata(
    api_client: KickApiClient,
    clip_id: str,
    request: ChatRequest,
) -> _ClipMetadata | _MobileClipMetadata:
    """Prefer web metadata and fall back to the anonymous mobile contract."""
    try:
        clip_data = fetch_with_retry(
            lambda: api_client.fetch_clip_metadata(clip_id),
            request,
        )
    except (KickError, RetriesExceeded) as error:
        return _fetch_mobile_clip_metadata(
            api_client,
            clip_id,
            request,
            primary_error=error,
            expected_channel_id=None,
            expected_duration=None,
        )

    try:
        return _resolve_clip_metadata(clip_data, clip_id)
    except _WebClipMetadataUnavailable as error:
        return _fetch_mobile_clip_metadata(
            api_client,
            clip_id,
            request,
            primary_error=error,
            expected_channel_id=error.channel_id,
            expected_duration=error.duration,
        )
    except NoChatReplay as error:
        clip = get_dict(clip_data, "clip")
        return _fetch_mobile_clip_metadata(
            api_client,
            clip_id,
            request,
            primary_error=error,
            expected_channel_id=_web_clip_channel_id(clip, clip_id),
            expected_duration=_web_clip_duration(clip, clip_id),
        )


def _apply_clip_request_window(
    metadata: _ClipMetadata | _MobileClipMetadata,
    request: ChatRequest,
) -> tuple[float, float]:
    """Resolve caller bounds relative to the clip and clamp to its duration."""
    start = cast("float", ensure_seconds(request.start_time, 0.0))
    end = cast("float", ensure_seconds(request.end_time, metadata.duration))
    return (
        min(max(start, 0.0), metadata.duration),
        min(max(end, 0.0), metadata.duration),
    )


def _resolve_clip_source_vod_window(
    data: JSONDict,
    username: str,
) -> tuple[str, str, str, datetime, datetime]:
    """Validate the source duration needed to bound clip fallback decisions."""
    livestream = get_dict(data, "livestream")
    if livestream:
        duration_ms = livestream.get("duration")
        if (
            isinstance(duration_ms, bool)
            or not isinstance(duration_ms, (int, float))
            or not math.isfinite(duration_ms)
            or duration_ms <= 0
        ):
            msg = f"Kick video for {username!r} has no finite positive duration."
            raise KickError(msg)
    return _resolve_vod_window(data, username)


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

    metadata = _fetch_clip_metadata(api_client, clip_id, request)
    vod_window: tuple[str, str, str, datetime, datetime] | None = None
    if isinstance(metadata, _ClipMetadata):
        video_id = metadata.video_id
        web_channel_id = metadata.channel_id
        try:
            video_data = fetch_with_retry(
                lambda: api_client.fetch_video_metadata(video_id),
                request,
            )
            vod_window = _resolve_clip_source_vod_window(video_data, username)
        except (KickError, RetriesExceeded) as error:
            metadata = _fetch_mobile_clip_metadata(
                api_client,
                clip_id,
                request,
                primary_error=error,
                expected_channel_id=web_channel_id,
                expected_duration=metadata.duration,
            )

    clip_start, clip_end = _apply_clip_request_window(metadata, request)
    if isinstance(metadata, _MobileClipMetadata):
        start_dt = metadata.started_at + timedelta(seconds=clip_start)
        end_dt = metadata.started_at + timedelta(seconds=clip_end)
        log("info", f"Clip time window: {start_dt} to {end_dt}")
        return Chat(
            _iter_vod_messages(
                metadata.channel_id,
                start_dt,
                end_dt,
                request,
                api_client=api_client,
            ),
            title=metadata.title or username,
            duration=max(0.0, (end_dt - start_dt).total_seconds()),
            status="completed",
            video_type="clip",
            start_time=clip_start,
            id=clip_id,
        )

    channel_id, _chatroom_id, source_title, vod_start, vod_end = cast(
        "tuple[str, str, str, datetime, datetime]",
        vod_window,
    )
    if channel_id != metadata.channel_id:
        msg = f"Kick clip {clip_id!r} channel does not match its source VOD."
        raise KickError(msg)

    source_duration = max(0.0, (vod_end - vod_start).total_seconds())
    if metadata.start_offset > source_duration:
        msg = f"Kick clip {clip_id!r} starts outside its source VOD."
        raise KickError(msg)

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
