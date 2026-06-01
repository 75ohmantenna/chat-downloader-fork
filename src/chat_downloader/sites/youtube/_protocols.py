# SPDX-License-Identifier: MIT

"""Structural type protocols for the YouTube site layer.

These protocols describe the duck-typed interfaces that YouTube module
helper functions depend on, avoiding circular imports with the concrete
``YouTubeChatDownloader`` class while giving mypy something concrete to
check against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import requests


class YouTubeDownloaderProto(Protocol):
    """Minimal interface of ``YouTubeChatDownloader`` seen by iteration helpers.

    Only the attributes and methods actually accessed by functions in
    ``chat_streams_runtime_iteration`` are declared here.
    """

    session: requests.Session
    _auto_profile_fallback: bool
    _request_profile: str | None

    def apply_request_profile(self, profile_name: str) -> bool: ...

    def update_session_headers(self, new_headers: dict[str, str]) -> None: ...

    def check_for_invalid_types(
        self,
        messages_types_to_add: list[str],
        allowed_message_types: list[str],
    ) -> None: ...

    def _session_post(self, url: str, **kwargs: Any) -> Any: ...
