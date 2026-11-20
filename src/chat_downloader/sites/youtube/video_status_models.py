# SPDX-License-Identifier: MIT

"""Model objects for YouTube video metadata parsing."""

from __future__ import annotations

from dataclasses import dataclass, field

# Use replay continuations for these broadcast statuses.
REPLAY_STATUSES: frozenset[str] = frozenset({"past", "was_live", "post_live"})

# ``post_live`` can expose the replay endpoint while YouTube is still finalizing
# the archive, so strict completion and resume accept only stable replay states.
COMPLETED_REPLAY_STATUSES: frozenset[str] = frozenset({"past", "was_live"})


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
