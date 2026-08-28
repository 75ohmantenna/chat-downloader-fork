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


class KickServerError(KickError):
    """Raised for transient Kick server problems (HTTP 429/5xx).

    Callers in the retry loop catch this type to trigger a back-off and
    another attempt, distinguishing it from terminal :class:`KickError`
    conditions such as a missing chatroom id.
    """


class KickForwardHistoryRejected(KickError):
    """Raised for a validated rejection of forward history's start field."""
