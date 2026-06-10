# SPDX-License-Identifier: MIT

"""Main module for ChatDownloader.

Orchestrates chat retrieval from streaming platforms.
"""

from __future__ import annotations

import ipaddress
import sys
from http.cookiejar import MozillaCookieJar
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse

from .debugging import log
from .errors import InvalidParameter
from .metadata import __version__
from .models import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
    DEFAULT_READ_TIMEOUT,
    ChatRequest,
    DownloaderConfig,
)
from .redaction import sanitize_for_log
from .runtime import (
    RunResult,
    clear_all_cookies,
    close_sessions,
    execute_run,
    handle_unsupported_url,
    propagate_cookie,
    try_create_chat_from_sites,
    validate_url,
)
from .runtime import (
    create_session as create_runtime_session,
)
from .runtime import (
    get_cookie_value as get_runtime_cookie_value,
)
from .sites.models import Chat, SiteDefault

# Module-level sentinel defaults for get_chat() keyword arguments.
# Using module-level singletons avoids the B008 lint warning about
# calling SiteDefault() inside a function-argument default expression.
_DEFAULT_MESSAGE_GROUPS = SiteDefault("message_groups")
_DEFAULT_FORMAT = SiteDefault("format")

if TYPE_CHECKING:
    from .sites.base import BaseChatDownloader


def _is_loopback_host(host: str) -> bool:
    """Return True only for genuine loopback hosts.

    Uses :mod:`ipaddress` so the whole ``127.0.0.0/8`` range and ``::1`` are
    recognised, while spoofed names like ``127.0.0.1.attacker.com`` (which a
    naive ``startswith("127.")`` check would wrongly accept) are rejected.
    ``urlparse`` already strips IPv6 brackets, so ``::1`` arrives bare here.
    """
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ===== Main ChatDownloader Class =====


class ChatDownloader:
    """Main class for downloading chat messages from streaming platforms.

    ChatDownloader orchestrates the retrieval of chat messages from
    various streaming services (YouTube, Twitch). It manages
    sessions for each site, handles URL routing, and provides a unified
    interface for chat retrieval with support for filtering, formatting,
    and output options.

    Thread-safety: Not thread-safe. Create separate instances for
    concurrent use.
    """

    def __init__(
        self,
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

        Creates a session manager that can instantiate site-specific
        downloaders on demand. The provided parameters are applied to
        all subsequent site sessions.

        :param headers: Custom HTTP headers for requests, defaults to
            None
        :type headers: dict, optional
        :param cookies: Path to Netscape-format cookies file, defaults
            to None
        :type cookies: str, optional
        :param proxy: Proxy URL (supports HTTP/HTTPS/SOCKS). Examples:
            - HTTP: "http://proxy.example.com:8080"
            - SOCKS5: "socks5://127.0.0.1:1080"
            - Direct connection: "" (empty string)
            Defaults to None (system proxy settings)
        :type proxy: str, optional
        :param connect_timeout: TCP connect timeout in seconds,
            defaults to 10
        :type connect_timeout: float, optional
        :param read_timeout: HTTP read timeout in seconds, defaults
            to 30
        :type read_timeout: float, optional
        :param request_profile: Optional preset request profile
            (youtube_web/youtube_android/youtube_ios/twitch_web)
        :type request_profile: str, optional
        :param auto_profile_fallback: Whether to automatically rotate
            YouTube request profiles after repeated incomplete continuation
            responses. Defaults to True.
        :type auto_profile_fallback: bool, optional
        :param twitch_client_id: Optional Twitch Client-ID override.
        :type twitch_client_id: str, optional
        """
        if proxy and cookies is not None:
            host = urlparse(proxy).hostname or ""
            if _is_loopback_host(host):
                log(
                    "warning",
                    "Using a local proxy with cookie authentication; "
                    "credentials will be visible to the local proxy process.",
                )
            else:
                raise InvalidParameter(
                    "A proxy must not be used with cookie authentication: "
                    "credentials would be exposed to the proxy."
                )

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
            "Initialisation parameters: "
            f"{sanitize_for_log(self.config.as_dict())}",
        )

        # Session cache: {site_class_name: site_instance}
        self.sessions: dict[str, BaseChatDownloader] = {}
        # Local cookie jar to support setting cookies before any site session
        # exists.
        self._cookie_jar = MozillaCookieJar()

    def clear_cookies(self) -> None:
        """Clear cookies for this downloader and all its site sessions."""
        clear_all_cookies(self)

    def set_cookie_value(
        self,
        domain: str,
        name: str,
        value: str,
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
        propagate_cookie(
            self,
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
        return get_runtime_cookie_value(self, name, default)

    def get_chat(
        self,
        url: str | None = None,
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
        format: SiteDefault | str = _DEFAULT_FORMAT,
        format_file: str | None = None,
        # YouTube
        chat_type: Literal["live", "top"] = "live",
        ignore: list[str] | None = None,
        # Twitch
        message_receive_timeout: float = DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
        buffer_size: int = DEFAULT_BUFFER_SIZE,
    ) -> Chat:
        """Retrieve chat messages from a stream, video, clip or broadcast.

        This is the main entry point for chat retrieval. It automatically
        detects the streaming platform from the URL, creates the
        appropriate site session, and returns a Chat object containing
        a generator of messages.

        :param url: URL of the stream/video (required)
        :type url: str

        Time filtering:
        :param start_time: Start time in seconds or hh:mm:ss
            (None = from beginning)
        :type start_time: float, optional
        :param end_time: End time in seconds or hh:mm:ss
            (None = until end)
        :type end_time: float, optional
        :param timeout: Maximum duration to retrieve messages in
            seconds
        :type timeout: float, optional
        :param inactivity_timeout: Stop if no messages received for
            this many seconds
        :type inactivity_timeout: float, optional

        Retry behavior:
        :param max_attempts: Maximum retry attempts (default: 15)
        :type max_attempts: int, optional
        :param retry_timeout: Seconds to wait before retry
            (None = exponential backoff, negative = wait for user
            input)
        :type retry_timeout: float, optional
        :param interruptible_retry: Allow skipping wait to retry
            immediately (default: True)
        :type interruptible_retry: bool, optional

        Message filtering:
        :param max_messages: Maximum number of messages to retrieve
            (None = unlimited)
        :type max_messages: int, optional
        :param message_groups: Predefined message groups to include
            (site-specific)
        :type message_groups: SiteDefault, optional
        :param message_types: Specific message types to include
            (overrides message_groups)
        :type message_types: list, optional

        Output options:
        :param output: Output file path (None = print to stdout). Extension
            determines format (.jsonl/.csv/.txt). JSON-array `.json` output
            is not supported; use `.jsonl` for structured output.
        :type output: str, optional
        :param overwrite: Overwrite existing output file
            (default: True)
        :type overwrite: bool, optional
        :param sort_keys: Sort JSON keys in output (default: True)
        :type sort_keys: bool, optional

        Formatting:
        :param format: Message format template name
            (site-specific default)
        :type format: SiteDefault, optional
        :param format_file: Path to custom format definition file
        :type format_file: str, optional

        Site-specific (YouTube):
        :param chat_type: Chat type ('live', 'top', etc.)
            (default: 'live')
        :type chat_type: str, optional
        :param ignore: List of video IDs to ignore
        :type ignore: list, optional

        Site-specific (Twitch):
        :param message_receive_timeout: Seconds between message
            requests (default: 0.1)
        :type message_receive_timeout: float, optional
        :param buffer_size: Buffer size for message retrieval
            (default: 4096)
        :type buffer_size: int, optional

        :raises URLNotProvided: No URL provided
        :raises ChatGeneratorError: No valid generator found for site
        :raises SiteNotSupported: URL's site not supported
        :raises InvalidURL: Invalid URL format

        :return: Chat object with message generator
        :rtype: Chat
        """
        params = locals()
        params = {k: v for k, v in params.items() if k != "self"}
        params["url"] = "" if url is None else url
        return self.get_chat_request(
            ChatRequest.from_kwargs(strict=True, **params)
        )

    def get_chat_request(self, request: ChatRequest) -> Chat:
        """Typed entry point for chat retrieval via :class:`ChatRequest`."""
        validate_url(request.url)

        # URL dispatch flow:
        # 1. Try to match URL against all registered sites
        # 2. If match found, create site session and get chat
        # 3. If no match, attempt URL correction (add https://) or raise error
        chat = try_create_chat_from_sites(self, request.url, request)
        if chat:
            return chat

        return handle_unsupported_url(self, request.url, request)

    def create_session(
        self,
        chat_downloader_class: type[BaseChatDownloader],
        overwrite: bool = False,
    ) -> BaseChatDownloader:
        """Create or retrieve a session for a chat downloader class.

        :param chat_downloader_class: The ChatDownloader class to create
            session for
        :param overwrite: Whether to overwrite existing session
        :return: The session instance
        :raises TypeError: if class is invalid
        """
        return create_runtime_session(self, chat_downloader_class, overwrite)

    def get_session(
        self,
        chat_downloader_class: type[BaseChatDownloader],
    ) -> BaseChatDownloader | None:
        """Get existing session for a chat downloader class.

        :param chat_downloader_class: The ChatDownloader class
        :return: The session instance or None if not found
        """
        return self.sessions.get(chat_downloader_class.__name__)

    def close(self) -> None:
        """Close all sessions associated with the object."""
        close_sessions(self)


# ===== Module-Level Functions =====


def run(propagate_interrupt: bool = False, **kwargs: Any) -> RunResult:
    """Execute a complete chat download session with error handling.

    This is a convenience function that creates a ChatDownloader
    instance, retrieves chat messages, and handles common errors. It
    automatically:
    - Separates init parameters from get_chat parameters
    - Iterates through all chat messages
    - Logs messages unless quiet=True
    - Handles and logs errors appropriately
    - Cleans up resources via downloader.close()

    :param propagate_interrupt: If True, re-raise KeyboardInterrupt
        instead of catching it. Useful when embedding in other
        applications. (default: False)
    :type propagate_interrupt: bool, optional
    :param kwargs: Combined parameters for ChatDownloader.__init__()
        and get_chat(). Will be automatically separated based on
        method signatures.
    :type kwargs: dict

    :return: Structured execution summary.
    :rtype: RunResult

    Example:
        >>> run(url='https://www.youtube.com/watch?v=...',
        ...     max_messages=100)
    """
    return execute_run(
        ChatDownloader,
        propagate_interrupt=propagate_interrupt,
        **kwargs,
    )
