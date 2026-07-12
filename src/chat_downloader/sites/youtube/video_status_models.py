# SPDX-License-Identifier: MIT

"""Model objects for YouTube video metadata parsing."""

from __future__ import annotations

from dataclasses import dataclass, field

# Statuses that indicate a finished broadcast with a chat replay available.
# Owned by the YouTube site package (the runtime layer no longer knows YouTube
# status vocabularies — see BaseChatDownloader.is_live_status).
REPLAY_STATUSES: frozenset[str] = frozenset({"past", "was_live", "post_live"})


@dataclass(slots=True)
class VideoDetails:
    """Parsed YouTube video metadata from raw player-response payloads."""

    title: str | None
    author: str | None
    author_id: str | None
    original_video_id: str | None
    video_type: str
    status: str
    start_time: float | None
    end_time: float | None
    duration: float | None
    continuation_info: dict[str, str] = field(default_factory=dict)
    clip_start_time: float | None = None
    clip_end_time: float | None = None
