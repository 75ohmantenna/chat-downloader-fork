# SPDX-License-Identifier: MIT

"""Base downloader implementation and shared low-level utilities."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, ClassVar

from chat_downloader.errors import (
    InvalidURL,
    SiteNotSupported,
    URLNotProvided,
)
from chat_downloader.models import SiteDefault

from .retry import retry as perform_retry
from .session import (
    apply_request_profile as apply_session_request_profile,
)
from .session import (
    check_cookie_rotation,
    close_session,
    get_cookies_dict,
    init_session_state,
    session_get,
    session_get_json,
    session_post,
)
from .session import (
    clear_cookies as clear_session_cookies,
)
from .session import (
    get_cookie_value as get_session_cookie_value,
)
from .session import (
    get_session_headers as session_header,
)
from .session import (
    set_cookie_value as set_session_cookie_value,
)
from .session import (
    update_session_headers as update_headers,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    import requests

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONAny


class BaseChatDownloader:
    """Base class for site-specific chat downloaders."""

    session: requests.Session
    _http_timeout: tuple[float, float]
    _has_initial_auth_cookies: bool
    _cookie_rotation_warned: bool
    _request_profile: str | None
    _auto_profile_fallback: bool
    _twitch_client_id: str | None
    _session_closed: bool

    _NAME: str | None = None

    # Status values that mark an ongoing or recently-ended live broadcast.
    # Sites that distinguish live from replay override this; the base default is
    # empty so the generic runtime never needs site-specific knowledge.
    _LIVE_STATUSES: ClassVar[frozenset[str]] = frozenset()

    _SITE_DEFAULT_PARAMS: ClassVar[dict[str, Any]] = {
        "message_groups": ["messages"],
        "format": "default",
    }

    _TESTS: ClassVar[list[dict[str, Any]]] = [
        {
            "name": "Inactivity timeout",
            "params": {
                "url": "https://twitch.tv/xenova",
                "inactivity_timeout": 5,
                "timeout": 20,
            },
        },
        {
            "name": "Get a certain number of messages from a livestream.",
            "params": {
                "url": "https://www.youtube.com/watch?v=wXspodtIxYU",
                "max_messages": 10,
                "timeout": 60,
            },
            "expected_result": {
                "messages_condition": lambda messages: len(messages) <= 10,
            },
        },
        {
            "name": "Scheme not supplied",
            "params": {
                "url": "www.youtube.com/watch?v=wXspodtIxYU",
                "max_messages": 10,
                "timeout": 60,
            },
            "expected_result": {
                "messages_condition": lambda messages: len(messages) <= 10,
            },
        },
        {
            "name": "No URL provided.",
            "params": {"url": ""},
            "expected_result": {"error": URLNotProvided},
        },
        {
            "name": "Site not supported",
            "params": {"url": "https://www.example.com"},
            "expected_result": {"error": SiteNotSupported},
        },
        {
            "name": "Invalid URL",
            "params": {"url": "#"},
            "expected_result": {"error": InvalidURL},
        },
    ]

    def __init__(self, **kwargs: Any) -> None:
        """Initialise session state for the downloader instance."""
        init_session_state(self, **kwargs)

    @property
    def _has_auth_cookies(self) -> bool:
        """Return whether authentication cookies are currently present."""
        return False

    def _check_cookie_rotation(self) -> None:
        """Warn once if the auth cookies have rotated away."""
        check_cookie_rotation(self)

    def get_session_headers(self, key: str) -> str | None:
        """Return the current value of the named HTTP session header."""
        return session_header(self, key)

    def update_session_headers(self, new_headers: dict[str, str]) -> None:
        """Merge ``new_headers`` into the active HTTP session headers."""
        update_headers(self, new_headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        """Apply a named request profile to this session's headers."""
        return apply_session_request_profile(self, profile_name)

    def clear_cookies(self) -> None:
        """Remove all cookies from the active HTTP session."""
        clear_session_cookies(self)

    def _get_cookies_dict(self) -> dict[str, str]:
        return get_cookies_dict(self)

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
        rest: dict[str, str] | None = None,
    ) -> None:
        """Set a cookie on the HTTP session.

        Args:
            domain: Cookie domain.
            name: Cookie name.
            value: Cookie value.
            expire_time: Unix timestamp for expiry, or None.
            port: Port restriction, or None.
            path: Cookie path scope.
            secure: Whether the cookie is secure-only.
            discard: Whether the cookie is a session cookie.
            rest: Additional cookie attributes.
        """
        set_session_cookie_value(
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

    def get_cookie_value(self, name: str, default: str | None = None) -> str | None:
        """Return the value of cookie ``name``, or ``default`` if absent."""
        return get_session_cookie_value(self, name, default)

    def close(self) -> None:
        """Close the HTTP session and release associated resources."""
        close_session(self)

    def _session_post(self, url: str, **kwargs: Any) -> requests.Response:
        return session_post(self, url, **kwargs)

    def _session_get(self, url: str, **kwargs: Any) -> requests.Response:
        return session_get(self, url, **kwargs)

    def _session_get_json(self, url: str, **kwargs: Any) -> JSONAny:
        return session_get_json(self, url, **kwargs)

    def get_site_value(self, value: Any) -> Any:
        """Resolve a ``SiteDefault`` marker to its concrete value.

        Args:
            value: A ``SiteDefault`` marker or a concrete value.

        Returns:
            The site-specific default if ``value`` is a ``SiteDefault``,
            otherwise ``value`` unchanged.
        """
        if isinstance(value, SiteDefault):
            return self._SITE_DEFAULT_PARAMS.get(
                value.name,
                BaseChatDownloader._SITE_DEFAULT_PARAMS.get(value.name),
            )
        return value

    def is_live_status(self, status: str | None) -> bool:
        """Return whether ``status`` marks an ongoing/recent live broadcast.

        Provider-neutral hook: the generic runtime calls this instead of
        inspecting site-specific status vocabularies. Sites that distinguish
        live from replay override ``_LIVE_STATUSES``.
        """
        return status in self._LIVE_STATUSES

    def resolve_live_format(self, format_name: str) -> str:
        """Map a requested format name to a live-stream variant, if any.

        Provider-neutral hook: the generic runtime calls this so site-specific
        live-format overrides live with the site. The base implementation
        returns ``format_name`` unchanged.
        """
        return format_name

    @staticmethod
    def _coerce_chat_request(
        params_or_request: ChatRequest | dict[str, Any],
    ) -> ChatRequest:
        from chat_downloader.models import coerce_chat_request

        return coerce_chat_request(params_or_request)

    _VALID_URLS: ClassVar[dict[str, str]] = {}

    @classmethod
    def matches(cls, url: str) -> tuple[str, re.Match[str]] | None:
        """Return ``(function_name, match)`` if ``url`` matches a pattern.

        Args:
            url: The URL to test.

        Returns:
            A tuple of the handler function name and the regex match object,
            or ``None`` if no pattern matches.
        """
        for function_name, regex in cls._VALID_URLS.items():
            if isinstance(regex, str):
                match = re.match(regex, url)
                if match and cls._has_valid_match_suffix(url, match.end()):
                    return function_name, match
        return None

    @staticmethod
    def _has_valid_match_suffix(url: str, match_end: int) -> bool:
        """Return whether unmatched text is only a URL suffix delimiter."""
        remainder = url[match_end:]
        if not remainder or remainder == "/":
            return True
        if remainder.startswith(("?", "#", "/?", "/#")):
            return True
        if remainder.startswith(("&", ";")):
            return "?" in url[:match_end]
        return False

    def generate_urls(self, **kwargs: Any) -> Iterator[str]:
        """Yield URLs supported by this site for testing or enumeration."""
        raise NotImplementedError

    @staticmethod
    def retry(
        attempt_number: int,
        *,
        max_attempts: int = 1,
        error: Exception | None = None,
        retry_timeout: float | None = None,
        text: Any = None,
        interruptible_retry: bool = True,
        request: ChatRequest | None = None,
    ) -> None:
        """Enforce the retry policy, sleeping or raising as appropriate.

        Args:
            attempt_number: Current 1-indexed attempt number.
            max_attempts: Total allowed attempts before giving up.
            error: The exception that triggered the retry, if any.
            retry_timeout: Fixed sleep duration in seconds, or None for
                exponential back-off.
            text: Extra context to include in log messages.
            interruptible_retry: Allow the user to skip the sleep by pressing
                Enter.
            request: The active ``ChatRequest``, used for per-request overrides.

        Raises:
            RetriesExceeded: When ``attempt_number >= max_attempts``.
        """
        perform_retry(
            attempt_number=attempt_number,
            max_attempts=max_attempts,
            error=error,
            retry_timeout=retry_timeout,
            text=text,
            interruptible_retry=interruptible_retry,
            request=request,
        )
