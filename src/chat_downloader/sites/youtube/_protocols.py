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
    from collections.abc import Iterator

    import requests

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.models import Chat


class YouTubeDownloaderProto(Protocol):
    """Minimal interface of ``YouTubeChatDownloader`` seen by iteration helpers.

    Covers all attributes and methods accessed by standalone functions and
    mixin methods that cannot see the concrete class at type-check time.
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

    def _session_get(self, url: str, **kwargs: Any) -> Any: ...

    def _coerce_chat_request(
        self, params_or_request: ChatRequest | dict[str, Any]
    ) -> ChatRequest: ...

    def _parse_video_data(
        self,
        video_id: str,
        params: ChatRequest | None = ...,
        video_type: str = ...,
    ) -> tuple[dict[str, Any], Any, Any, Any]: ...

    def _get_initial_video_info(
        self,
        video_id: str,
        params: ChatRequest | None = ...,
        video_type: str = ...,
    ) -> tuple[dict[str, Any], Any]: ...

    def _get_rendered_content(
        self, yt_info: dict[str, Any], tab_index: int = ...
    ) -> Any: ...

    def _get_chat_by_user_args(
        self,
        user_video_args: dict[str, str],
        params: ChatRequest | dict[str, Any],
    ) -> Chat: ...

    def get_playlist_items(
        self,
        playlist_url: str,
        params: ChatRequest | dict[str, Any] | None = ...,
    ) -> Iterator[dict[str, Any]]: ...

    def get_user_videos(self, **kwargs: Any) -> Iterator[dict[str, Any]]: ...

    def get_chat_by_video_id(
        self, video_id: str, params: ChatRequest | dict[str, Any]
    ) -> Chat: ...
