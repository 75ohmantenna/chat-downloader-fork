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
    """Main class for downloading chat messages from streaming platforms.

    ChatDownloader orchestrates the retrieval of chat messages from
    various streaming services (YouTube, Twitch, Kick). It manages
    sessions for each site, handles URL routing, and provides a unified
    interface for chat retrieval with support for filtering, formatting,
    and output options.

    Thread-safety: Not thread-safe. Create separate instances for
    concurrent use.
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
        """Initialize a new ChatDownloader session.

        The provided parameters are applied to all subsequent site
        sessions.

        Args:
            headers: Custom HTTP headers for requests.
            cookies: Path to a Netscape-format cookies file.
            proxy: Proxy URL (HTTP/HTTPS/SOCKS); "" forces a direct
                connection. Defaults to None (system proxy settings).
            connect_timeout: TCP connect timeout in seconds (default 10).
            read_timeout: HTTP read timeout in seconds (default 30).
            request_profile: Optional preset request profile
                (youtube_web/youtube_android/youtube_ios/twitch_web).
            auto_profile_fallback: Automatically rotate YouTube request
                profiles after generic initial playability failures or
                repeated incomplete continuation responses (default True).
            twitch_client_id: Optional Twitch Client-ID override.
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
        """Set a cookie value on this ChatDownloader and all existing sessions.

        This mirrors BaseChatDownloader.set_cookie_value so callers can set
        cookies before any site session is created.
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
        """Get a cookie value from the ChatDownloader cookie jar.

        Falls back to checking existing sessions if the local jar doesn't have
        it.
        """
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
        """Retrieve chat messages from a stream, video, clip or broadcast.

        Main entry point: detects the platform from the URL, creates the
        appropriate site session, and returns a Chat object containing a
        message generator.

        Args:
            url: URL of the stream/video (required).
            start_time: Start time in seconds or hh:mm:ss (None = beginning).
            end_time: End time in seconds or hh:mm:ss (None = until end).
            timeout: Maximum duration to retrieve messages in seconds.
            inactivity_timeout: Stop after this many seconds without messages.
            max_attempts: Maximum retry attempts (default 15).
            retry_timeout: Seconds to wait before retry; None uses
                exponential backoff, negative waits for user input.
            interruptible_retry: Allow skipping the wait to retry
                immediately (default True).
            max_messages: Maximum number of messages (None = unlimited).
            message_groups: Predefined site-specific message groups to
                include.
            message_types: Specific message types (overrides message_groups).
            output: Output file path or list of paths (None = stdout); each
                extension selects its format (.jsonl/.txt only).
            overwrite: Overwrite an existing output file (default True).
            sort_keys: Sort JSON keys in output (default True).
            format: Message format template name (site-specific default).
            format_file: Path to a custom format definition file.
            chat_type: YouTube chat type ('live' or 'top', default 'live').
            ignore: List of YouTube video IDs to ignore.
            youtube_replay_poll_interval: Explicit YouTube replay polling
                interval (0.5-8 s); None respects the provider delay hint.
            message_receive_timeout: Live socket receive-poll timeout in
                seconds (default 1.0; Twitch and Kick enforce a minimum of 1).
            buffer_size: Twitch buffer size for message retrieval
                (default 4096).

        Raises:
            URLNotProvided: No URL provided.
            ChatGeneratorError: No valid generator found for the site.
            SiteNotSupported: The URL's site is not supported.
            InvalidURL: Invalid URL format.

        Returns:
            Chat object with the message generator.
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
        """Create or retrieve a session for a chat downloader class.

        Args:
            chat_downloader_class: The downloader class to create a session for.
            overwrite: Whether to overwrite an existing session.

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
    """Execute a complete chat download session with error handling.

    Creates a ChatDownloader, iterates through all chat messages (logging
    them unless quiet=True), handles and logs errors, and cleans up via
    downloader.close().

    Args:
        propagate_interrupt: Re-raise KeyboardInterrupt instead of catching
            it; useful when embedding in other applications (default False).
        **kwargs: Combined fields from DownloaderConfig, ChatRequest, and
            RunConfig; separated by the typed CLI bridge before execution.

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
