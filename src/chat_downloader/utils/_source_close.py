# SPDX-License-Identifier: MIT

"""Close optional iterator sources during in-process cleanup."""

from __future__ import annotations

from operator import call
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class _SourceClose:
    """Guard synchronous close reentry and allow a retry after an interrupt."""

    def __init__(self) -> None:
        self.started = False

    def close(
        self,
        get_source: Callable[[], object],
        *,
        invoke: Callable[[Callable[[], object]], object] = call,
    ) -> None:
        """Look up the current source's optional close method and invoke it."""
        if self.started:
            return
        try:
            self.started = True
            close = getattr(get_source(), "close", None)
            if callable(close):
                invoke(close)
        except KeyboardInterrupt:
            self.started = False
            raise
