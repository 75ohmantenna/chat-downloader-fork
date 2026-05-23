# SPDX-License-Identifier: MIT

"""Structural type protocols for the sites layer.

These protocols describe the duck-typed interfaces that session helper
functions depend on, avoiding circular imports with ``BaseChatDownloader``
while giving mypy something concrete to check against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import requests


class SessionOwnerProto(Protocol):
    """Structural type for objects that own an HTTP session.

    This is the minimal interface that ``chat_downloader.sites.session``
    helper functions access on their ``owner`` argument.  Any class that
    satisfies these attribute/method requirements is accepted without
    needing to inherit from ``BaseChatDownloader``.
    """

    session: requests.Session
    _http_timeout: tuple[float, float]
    _has_initial_auth_cookies: bool
    _cookie_rotation_warned: bool
    _request_profile: str | None
    _auto_profile_fallback: bool
    _twitch_client_id: str | None

    @property
    def _has_auth_cookies(self) -> bool:
        """Return whether authentication cookies are currently present."""
        ...
