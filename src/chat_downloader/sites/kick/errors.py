# SPDX-License-Identifier: MIT

"""Kick-specific error types.

Defined in their own module so low-level helpers (``api_client``,
``websocket_transport``) can raise them without importing the extractor, which
would create an import cycle.
"""

from __future__ import annotations

from chat_downloader.errors import SiteError


class KickError(SiteError):
    """Raised when an error occurs with a Kick channel or its chat."""


class KickCountryBlocked(KickError):
    """Raised when Kick reports that the request's country is blocked."""


class KickRealtimeRejected(KickError):
    """A permanent anonymous protocol rejection; renegotiation cannot repair it."""


class KickResourceLimit(KickError):
    """A terminal resource-budget rejection leaving the capture incomplete."""


class KickServerError(KickError):
    """Raised for transient Kick server problems (HTTP 429/5xx).

    Callers in the retry loop catch this type to trigger a back-off and
    another attempt, distinguishing it from terminal :class:`KickError`
    conditions such as a missing chatroom id.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        """Retain only the parsed server delay needed by retry callers."""
        super().__init__(message)
        self.retry_after = retry_after


class KickForwardHistoryRejected(KickError):
    """Raised for a validated rejection of forward history's start field."""


class KickVideoNotFound(KickError):
    """The requested video ID was absent from a metadata endpoint."""


class KickVideoRestricted(KickError):
    """The requested source VOD requires a channel subscription."""
