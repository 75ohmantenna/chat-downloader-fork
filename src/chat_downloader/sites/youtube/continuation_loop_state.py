# SPDX-License-Identifier: MIT

"""Continuation loop state model."""

from dataclasses import dataclass


@dataclass(slots=True)
class ContinuationLoopState:
    """Mutable state carried between continuation-loop iterations."""

    continuation: str
    click_tracking_params: str | None = None
    offset_milliseconds: float | None = None
