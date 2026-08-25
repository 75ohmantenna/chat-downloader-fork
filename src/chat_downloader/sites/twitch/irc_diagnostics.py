# SPDX-License-Identifier: MIT

"""Twitch IRC traffic classification and clean-run sample capture."""

from __future__ import annotations

import os

from chat_downloader.redaction import capture_debug_sample

_SUCCESSFUL_FRAME_CAPTURE_ENV = "CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES"
_SUCCESSFUL_FRAME_CAPTURE_LIMIT = 3
_TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


class _SuccessfulIrcFrameCapture:
    """Bound sanitized capture attempts across one Twitch live-chat run."""

    def __init__(self) -> None:
        self._enabled = (
            os.environ.get(_SUCCESSFUL_FRAME_CAPTURE_ENV, "").strip().lower()
            in _TRUTHY_ENV_VALUES
        )
        self._attempts = 0

    def capture(self, raw_frame: str) -> None:
        """Capture one of the first explicitly requested valid IRC frames."""
        if not self._enabled or self._attempts >= _SUCCESSFUL_FRAME_CAPTURE_LIMIT:
            return
        self._attempts += 1
        capture_debug_sample(
            "twitch-irc-frame",
            {"raw": raw_frame},
            sample_limit=_SUCCESSFUL_FRAME_CAPTURE_LIMIT,
        )


def _is_benign_unmatched_irc_buffer(readbuffer: str) -> bool:
    """Return True for unmatched IRC traffic that is expected and noisy."""
    lines = [line.strip() for line in readbuffer.splitlines() if line.strip()]
    if not lines:
        return True

    for line in lines:
        if line.startswith(("PING :", "PONG :")):
            continue

        if " JOIN #" in line or " PART #" in line:
            continue

        if "tmi.twitch.tv" not in line:
            return False

        parts = line.split()
        if len(parts) >= 3 and parts[1].isdigit():
            continue

        if (
            len(parts) >= 4
            and parts[1] == "CAP"
            and parts[2] == "*"
            and parts[3] == "ACK"
        ):
            continue

        return False

    return True
