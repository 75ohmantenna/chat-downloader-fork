# SPDX-License-Identifier: MIT

"""Own cooperative cancellation of a live iterator and its current transport."""

from __future__ import annotations

from threading import Event
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from chat_downloader.utils.json_types import JSONDict

    from .websocket_transport import _KickTransport


class KickLiveIterator:
    """Keep deadline cancellation independent of a generator's execution lock."""

    def __init__(self) -> None:
        """Initialize cancellation before any network operation begins."""
        self.stopped = Event()
        self.source: Iterator[JSONDict] = iter(())
        self.transport: _KickTransport | None = None

    def __iter__(self) -> KickLiveIterator:
        """Return the cancellation-aware source iterator."""
        return self

    def __next__(self) -> JSONDict:
        """Advance the live source unless cancellation was requested."""
        if self.stopped.is_set():
            raise StopIteration
        return next(self.source)

    def request_stop(self) -> None:
        """Interrupt the active receive without closing an executing generator."""
        self.stopped.set()
        if self.transport is not None:
            self.transport.request_stop()

    def close(self) -> None:
        """Stop the socket and close the generator when the caller owns advancement."""
        self.request_stop()
        close = getattr(self.source, "close", None)
        if callable(close):
            close()
