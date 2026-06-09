# SPDX-License-Identifier: MIT

"""Structural type protocols for the runtime layer.

These protocols describe the duck-typed interfaces that the runtime helper
modules depend on, avoiding circular imports while giving mypy something
concrete to check against.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from http.cookiejar import MozillaCookieJar

    from chat_downloader.models import ChatRequest, DownloaderConfig
    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.sites.models import Chat


class ChatDownloaderProto(Protocol):
    """Structural type for ``ChatDownloader`` as seen by the runtime layer."""

    config: DownloaderConfig
    sessions: dict[str, BaseChatDownloader]
    _cookie_jar: MozillaCookieJar

    def create_session(
        self,
        chat_downloader_class: type[BaseChatDownloader],
        overwrite: bool = ...,
    ) -> BaseChatDownloader: ...

    def get_chat_request(self, request: ChatRequest) -> Chat: ...
