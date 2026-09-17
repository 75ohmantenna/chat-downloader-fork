# SPDX-License-Identifier: MIT

"""Public ChatDownloader facade and run convenience wrapper."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, Literal

from .debugging import log
from .metadata import __version__
from .models import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    ChatRequest,
    DownloaderConfig,
    SiteDefault,
)
from .redaction import sanitize_for_log
from .runtime.config_guards import check_proxy_cookie_safety
from .runtime.runner import RunResult, execute_run
from .runtime.session_lifecycle import _SiteSessionPool
from .runtime.site_dispatch import dispatch_chat

# Module-level sentinel defaults for get_chat() keyword arguments.
# Using module-level singletons avoids the B008 lint warning about
# calling SiteDefault() inside a function-argument default expression.
_DEFAULT_MESSAGE_GROUPS = SiteDefault("message_groups")
_DEFAULT_FORMAT = SiteDefault("format")

if TYPE_CHECKING:
    from .sites.base import BaseChatDownloader
    from .sites.models import Chat


# ===== Main ChatDownloader Class =====


class ChatDownloader:
    """Retrieve YouTube, Twitch, and Kick chat through a unified interface.

    Manages site sessions, URL routing, filtering, formatting, and output.
    Not thread-safe: create separate instances for concurrent use.
    """

    def __init__(
        self,
        *,
        headers: dict[str, str] | None = None,
        cookies: str | None = None,
        proxy: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        request_profile: str | None = None,
        auto_profile_fallback: bool = True,
        twitch_client_id: str | None = None,
    ) -> None:
        """Initialize options shared by all subsequent site sessions.

        Args:
            headers: Custom HTTP request headers.
            cookies: Path to a Netscape-format cookies file.
            proxy: HTTP/HTTPS/SOCKS URL; "" forces direct, None uses system settings.
            connect_timeout: TCP connect timeout in seconds (default 10).
            read_timeout: HTTP read timeout in seconds (default 30).
            request_profile: Preset: youtube_web/youtube_android/youtube_ios/twitch_web.
            auto_profile_fallback: Rotate YouTube profiles after generic initial
                playability failures or repeated incomplete continuation responses.
            twitch_client_id: Twitch Client-ID override.
        """
        check_proxy_cookie_safety(proxy, cookies)

        self.config = DownloaderConfig(
            headers=headers,
            cookies=cookies,
            proxy=proxy,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            request_profile=request_profile,
            auto_profile_fallback=auto_profile_fallback,
            twitch_client_id=twitch_client_id,
        )

        log("debug", f"Python version: {sys.version}")
        log("debug", f"Program version: {__version__}")
        log(
            "debug",
            f"Initialization parameters: {sanitize_for_log(self.config.as_dict())}",
        )

        self._session_pool = _SiteSessionPool(self.config)

    @property
    def sessions(self) -> dict[str, BaseChatDownloader]:
        """Expose cached site sessions while the pool retains ownership."""
        return self._session_pool.sessions

    @sessions.setter
    def sessions(self, value: dict[str, BaseChatDownloader]) -> None:
        """Replace compatibility cache state through the owning pool."""
        self._session_pool.sessions = value

    def clear_cookies(self) -> None:
        """Clear cookies for this downloader and all its site sessions."""
        self._session_pool.clear_cookies()

    def set_cookie_value(
        self,
        domain: str,
        name: str,
        value: str,
        *,
        expire_time: int | None = None,
        port: str | None = None,
        path: str = "/",
        secure: bool = False,
        discard: bool = False,
        rest: dict[str, Any] | None = None,
    ) -> None:
        """Set a cookie on this downloader and all existing sessions.

        Mirrors BaseChatDownloader.set_cookie_value, including before session creation.
        """
        self._session_pool.set_cookie(
            domain=domain,
            name=name,
            value=value,
            expire_time=expire_time,
            port=port,
            path=path,
            secure=secure,
            discard=discard,
            rest=rest,
        )

    def get_cookie_value(self, name: str, default: Any = None) -> Any:
        """Get a cookie from the local jar, falling back to existing sessions."""
        return self._session_pool.get_cookie(name, default)

    def get_chat(
        self,
        url: str | None = None,
        *,
        start_time: float | str | None = None,
        end_time: float | str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        retry_timeout: float | None = None,
        interruptible_retry: bool = True,
        timeout: float | None = None,
        inactivity_timeout: float | None = None,
        max_messages: int | None = None,
        message_groups: SiteDefault | list[str] = _DEFAULT_MESSAGE_GROUPS,
        message_types: list[str] | None = None,
        # Output
        output: str | list[str] | None = None,
        overwrite: bool = True,
        sort_keys: bool = True,
        # Formatting
        format: SiteDefault | str = _DEFAULT_FORMAT,  # noqa: A002 — public get_chat() API; renaming would break callers
        format_file: str | None = None,
        # YouTube
        chat_type: Literal["live", "top"] = "live",
        ignore: list[str] | None = None,
        youtube_replay_poll_interval: float | None = None,
        # Live transport
        message_receive_timeout: float = DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
        # Twitch
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> Chat:
        """Detect the URL's platform, create a session, and return a Chat.

        Args mirror :class:`ChatRequest` fields (seconds or ``hh:mm:ss`` times,
        output paths, formatting, provider options); see that class for
        defaults and semantics.

        Raises:
            URLNotProvided: No URL provided.
            ChatGeneratorError: No valid generator found for the site.
            SiteNotSupported: The URL's site is not supported.
            InvalidURL: Invalid URL format.
        """
        params = locals()
        params = {k: v for k, v in params.items() if k != "self"}
        params["url"] = "" if url is None else url
        return self.get_chat_request(ChatRequest.from_kwargs(strict=True, **params))

    def get_chat_request(self, request: ChatRequest) -> Chat:
        """Typed entry point for chat retrieval via :class:`ChatRequest`."""
        return dispatch_chat(self, request)

    def create_session(
        self,
        chat_downloader_class: type[BaseChatDownloader],
        *,
        overwrite: bool = False,
    ) -> BaseChatDownloader:
        """Create a downloader-class session, reusing it unless overwrite=True.

        Raises:
            TypeError: The class is not a valid downloader class.
        """
        return self._session_pool.create(chat_downloader_class, overwrite=overwrite)

    def get_session(
        self,
        chat_downloader_class: type[BaseChatDownloader],
    ) -> BaseChatDownloader | None:
        """Get an existing session for a downloader class, or None."""
        return self._session_pool.get(chat_downloader_class)

    def close(self) -> None:
        """Close all sessions associated with the object."""
        self._session_pool.close()


# ===== Module-Level Functions =====


def run(*, propagate_interrupt: bool = False, **kwargs: Any) -> RunResult:
    """Run a ChatDownloader session, handling/logging errors and closing it afterward.

    Iterates all chat messages, logging them unless quiet=True.

    Args:
        propagate_interrupt: Re-raise KeyboardInterrupt for embedding applications.
        **kwargs: Combined DownloaderConfig, ChatRequest, and RunConfig fields;
            separated by the typed CLI bridge before execution.

    Returns:
        Structured execution summary.

    Example:
        >>> run(url='https://www.youtube.com/watch?v=...', max_messages=100)
    """
    return execute_run(
        ChatDownloader,
        propagate_interrupt=propagate_interrupt,
        **kwargs,
    )
