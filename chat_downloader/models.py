# SPDX-License-Identifier: MIT

"""Typed configuration and request objects for chat-downloader.

Replaces the parameter-bag-via-locals() pattern in
``ChatDownloader.__init__`` and ``ChatDownloader.get_chat()``.

Public surface
--------------
- :class:`DownloaderConfig` — session-level options (headers/cookies/proxy).
- :class:`ChatRequest`       — a single chat retrieval request.

Both dataclasses expose an ``as_dict()`` bridge so that existing internal
helpers that still expect plain dicts continue to work unchanged during the
transition.

CLI metadata
------------
Fields tagged with ``field(metadata={"cli": {...}})`` are auto-wired into the
argparse CLI by :mod:`chat_downloader.cli`.  Fields without a ``"cli"`` key
are internal-only and not exposed as CLI arguments.

CLI metadata keys:

- ``help``   (str, required) — argparse ``help=`` text.
- ``group``  (str, default ``"general"``) — argument group name; must match
  a group registered in :func:`chat_downloader.cli.main`.
- ``flags``  (list[str], optional) — additional short flags, e.g. ``["-s"]``.
"""

import dataclasses
import math
from dataclasses import dataclass, field, replace
from dataclasses import fields as dc_fields
from typing import Any, Literal, Self

from ._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from ._timeout_defaults import DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT
from .sites.models import SiteDefault

# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------
# Defined here so that ChatRequest field defaults don't require importing from
# chat_downloader.py (which would create a circular dependency).
# chat_downloader.py re-imports these to keep its module-level names stable.
# DEFAULT_CONNECT_TIMEOUT and DEFAULT_READ_TIMEOUT are imported from
# _timeout_defaults (a leaf module) and re-exported here for convenience.

DEFAULT_MAX_ATTEMPTS: int = 15
DEFAULT_MESSAGE_RECEIVE_TIMEOUT: float = 0.1
DEFAULT_BUFFER_SIZE: int = 4096
# ---------------------------------------------------------------------------
# CLI metadata helper
# ---------------------------------------------------------------------------


def _cli(
    help: str, group: str = "general", flags: list[str] | None = None
) -> dict:
    """Build the ``"cli"`` metadata dict for a dataclass field.

    :param help: Help text shown in ``--help`` output.
    :param group: Argument group name (must match a group in ``cli.main``).
    :param flags: Additional short-form flags, e.g. ``["-s"]``.
    """
    m: dict[str, Any] = {"help": help, "group": group}
    if flags:
        m["flags"] = flags
    return m


def get_field_default(f: dataclasses.Field[Any]) -> Any:
    """Return the default value for a dataclass field.

    Calls ``default_factory`` if present; returns ``None`` for fields with no
    default (which should not appear on public dataclasses here).
    """
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return None


# ---------------------------------------------------------------------------
# DownloaderConfig
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DownloaderConfig:
    """Session-level configuration for
    :class:`~chat_downloader.ChatDownloader`.

    Maps 1:1 to :meth:`ChatDownloader.__init__` parameters.  This is
    **stable public surface**; add new session-scoped options here.
    """

    # headers is handled specially in the CLI (--user-agent / --header flags)
    # so it has no "cli" metadata here.
    headers: dict[str, str] | None = None

    cookies: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Path to Netscape-format cookies file",
                group="init",
                flags=["-c"],
            ),
        },
    )
    proxy: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Proxy URL (HTTP/HTTPS/SOCKS), e.g. socks5://127.0.0.1:1080."
                " Defaults to None (system proxy settings)",
                group="init",
                flags=["-p"],
            ),
        },
    )
    connect_timeout: float = field(
        default=DEFAULT_CONNECT_TIMEOUT,
        metadata={"cli": _cli("TCP connect timeout in seconds", group="init")},
    )
    read_timeout: float = field(
        default=DEFAULT_READ_TIMEOUT,
        metadata={"cli": _cli("HTTP read timeout in seconds", group="init")},
    )
    request_profile: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Preset request profile "
                "(youtube_web/youtube_android/youtube_ios/twitch_web)",
                group="init",
            ),
        },
    )
    auto_profile_fallback: bool = field(
        default=True,
        metadata={
            "cli": _cli(
                "Auto-switch request profile on repeated incomplete "
                "YouTube continuation responses",
                group="init",
            ),
        },
    )
    twitch_client_id: str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Override the public Twitch Client-ID used for GraphQL and "
                "VOD comment requests",
                group="init",
            ),
        },
    )

    def __post_init__(self) -> None:
        """Validate timeout fields."""
        for name, value in (
            ("connect_timeout", self.connect_timeout),
            ("read_timeout", self.read_timeout),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(
                    f"{name} must be a finite positive number, got {value!r}"
                )

    def as_dict(self) -> dict[str, Any]:
        """Return all fields as a plain ``dict``.

        The returned dict is used for session creation
        (``site_class(**config.as_dict())``) and as the supported replacement
        for the removed ``ChatDownloader.init_params`` attribute.
        """
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}


# ---------------------------------------------------------------------------
# ChatRequest
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ChatRequest:
    """A single chat retrieval request.

    Maps 1:1 to :meth:`ChatDownloader.get_chat` parameters.

    **Stable public fields** (user-facing API):
        ``url``, ``start_time``, ``end_time``, ``max_messages``,
        ``message_groups``, ``message_types``, ``output``, ``format``,
        ``format_file``.

    **Internal / site-specific fields**:
        ``chat_type``, ``ignore``, ``message_receive_timeout``,
        ``buffer_size``, ``max_attempts``, ``retry_timeout``,
        ``interruptible_retry``, ``timeout``, ``inactivity_timeout``,
        ``overwrite``, ``sort_keys``.
    """

    # ── Core ──────────────────────────────────────────────────────────────────
    url: str = field(
        default="",
        metadata={"cli": _cli("URL of the stream/video", group="core")},
    )

    # ── Time bounds ───────────────────────────────────────────────────────────
    start_time: float | str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Start time in seconds or hh:mm:ss (None = from beginning)",
                group="time",
                flags=["-s"],
            ),
        },
    )
    end_time: float | str | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "End time in seconds or hh:mm:ss (None = until end)",
                group="time",
                flags=["-e"],
            ),
        },
    )

    # ── Retry ─────────────────────────────────────────────────────────────────
    max_attempts: int = field(
        default=DEFAULT_MAX_ATTEMPTS,
        metadata={"cli": _cli("Maximum retry attempts", group="retry")},
    )
    retry_timeout: float | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Seconds to wait before retry"
                " (None = exponential backoff, negative = wait for user input)",
                group="retry",
            ),
        },
    )
    interruptible_retry: bool = field(
        default=True,
        metadata={
            "cli": _cli(
                "Allow skipping wait to retry immediately",
                group="retry",
            ),
        },
    )

    # ── Timeouts ──────────────────────────────────────────────────────────────
    timeout: float | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Maximum duration to retrieve messages in seconds",
                group="termination",
            ),
        },
    )
    inactivity_timeout: float | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Stop if no messages received for this many seconds",
                group="termination",
            ),
        },
    )

    # ── Filtering ─────────────────────────────────────────────────────────────
    max_messages: int | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Maximum number of messages to retrieve (None = unlimited)",
                group="termination",
            ),
        },
    )
    message_groups: SiteDefault | list[str] = field(
        default_factory=lambda: SiteDefault("message_groups"),
        metadata={
            "cli": _cli(
                "Predefined message groups to include (site-specific)",
                group="type",
            ),
        },
    )
    message_types: list[str] | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Specific message types to include (overrides message_groups)",
                group="type",
            ),
        },
    )

    # ── Output ────────────────────────────────────────────────────────────────
    output: str | list[str] | None = field(
        default=None,
        metadata={
            "cli": _cli(
                "Output file path (None = print to stdout). Extension "
                "determines"
                " format (.jsonl/.csv/.txt). JSON-array .json output is no"
                " longer supported; use .jsonl for structured output.",
                group="output",
                flags=["-o"],
            ),
        },
    )
    overwrite: bool = field(
        default=True,
        metadata={
            "cli": _cli("Overwrite existing output file", group="output")
        },
    )
    sort_keys: bool = field(
        default=True,
        metadata={"cli": _cli("Sort JSON keys in output", group="output")},
    )

    # ── Formatting ────────────────────────────────────────────────────────────
    format: SiteDefault | str = field(
        default_factory=lambda: SiteDefault("format"),
        metadata={
            "cli": _cli(
                "Message format template name (site-specific default)",
                group="format",
            ),
        },
    )
    format_file: str | None = field(
        default=None,
        metadata={
            "cli": _cli("Path to custom format definition file", group="format")
        },
    )

    # ── YouTube-specific ──────────────────────────────────────────────────────
    chat_type: Literal["live", "top"] = field(
        default="live",
        metadata={
            "cli": _cli(
                "Chat type ('live' or 'top')",
                group="youtube",
            ),
        },
    )
    ignore: list[str] | None = field(
        default=None,
        metadata={"cli": _cli("List of video IDs to ignore", group="youtube")},
    )

    # ── Twitch-specific ───────────────────────────────────────────────────────
    message_receive_timeout: float = field(
        default=DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
        metadata={
            "cli": _cli(
                "Seconds between message requests",
                group="twitch",
            ),
        },
    )
    buffer_size: int = field(
        default=DEFAULT_BUFFER_SIZE,
        metadata={
            "cli": _cli("Buffer size for message retrieval", group="twitch")
        },
    )

    # ── Constructors ──────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        """Validate request fields that have constrained runtime values."""
        if self.max_messages is not None and (
            not isinstance(self.max_messages, int) or self.max_messages <= 0
        ):
            raise ValueError(
                "max_messages must be a positive integer or None, "
                f"got {self.max_messages!r}"
            )
        if self.max_attempts < 1:
            raise ValueError(
                f"max_attempts must be >= 1, got {self.max_attempts!r}"
            )
        if self.buffer_size <= 0:
            raise ValueError(
                f"buffer_size must be positive, got {self.buffer_size!r}"
            )
        if self.chat_type not in ("live", "top"):
            raise ValueError(
                f"chat_type must be 'live' or 'top', got {self.chat_type!r}"
            )
        for name, value in (
            ("timeout", self.timeout),
            ("inactivity_timeout", self.inactivity_timeout),
        ):
            if value is not None and (not math.isfinite(value) or value <= 0):
                raise ValueError(
                    f"{name} must be a finite positive number or None, "
                    f"got {value!r}"
                )
        if not math.isfinite(self.message_receive_timeout) or (
            self.message_receive_timeout <= 0
        ):
            raise ValueError(
                "message_receive_timeout must be a finite positive number, "
                f"got {self.message_receive_timeout!r}"
            )

    @classmethod
    def from_kwargs(cls, *, strict: bool = False, **kwargs: Any) -> Self:
        """Construct a :class:`ChatRequest` from a ``**kwargs`` dict.

        Known keys are mapped to fields; unknown keys are silently ignored
        by default for legacy direct callers.  New call sites should pass
        ``strict=True`` to raise :exc:`TypeError` when any unknown key is
        present.

        This is the single canonical location for mapping a kwargs dict to a
        :class:`ChatRequest` — do not duplicate this logic elsewhere.

        :param strict: When ``True``, raise :exc:`TypeError` listing all
            unknown keys instead of silently ignoring them.  Defaults to
            ``False`` to preserve the existing behavior.
        :type strict: bool
        :param kwargs: Keyword arguments matching :class:`ChatRequest` field
            names.
        :return: Populated :class:`ChatRequest` instance.
        :raises TypeError: When ``strict=True`` and unknown keys are present.
        """
        known = {f.name for f in dc_fields(cls)}
        unknown = sorted(k for k in kwargs if k not in known)
        if strict and unknown:
            msg = (
                "ChatRequest.from_kwargs() received unknown keyword "
                "argument(s): "
                f"{unknown}.  Valid fields are: {sorted(known)}."
            )
            raise TypeError(
                msg,
            )
        filtered = {k: v for k, v in kwargs.items() if k in known}
        return cls(**filtered)

    def with_updates(self, **kwargs: Any) -> Self:
        """Return a copy of the request with selected fields replaced."""
        return replace(self, **kwargs)

    def resolved_for_site(self, site_object: Any) -> Self:
        """Resolve any site-default placeholder values for a specific site."""
        return self.with_updates(
            **{
                field.name: site_object.get_site_value(
                    getattr(self, field.name)
                )
                for field in dc_fields(self)
            },
        )

    def as_dict(self) -> dict[str, Any]:
        """Return all request fields as a plain ``dict``.

        This is the canonical dict view for runtime logging and compatibility
        edges that still need keyword-style parameters.
        """
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}

    def retry_kwargs(self) -> dict[str, Any]:
        """Return the retry-related subset of fields as plain kwargs."""
        return {
            "max_attempts": self.max_attempts,
            "retry_timeout": self.retry_timeout,
            "interruptible_retry": self.interruptible_retry,
        }


# ---------------------------------------------------------------------------
# RunConfig
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class RunConfig:
    """Runtime-only controls for :func:`chat_downloader.run` execution."""

    # "quiet" is CLI-exposed for output suppression and also part of runtime
    # execution controls.
    quiet: bool = field(
        default=False,
        metadata={
            "cli": _cli(
                "Suppress formatted chat output to stdout, defaults to False",
                group="debug",
            )
        },
    )
    max_seen_message_ids: int = DEFAULT_MAX_SEEN_MESSAGE_IDS
    exit_on_debug: bool = field(
        default=False,
        metadata={
            "cli": _cli(
                "Exit when something unexpected happens, defaults to False",
                group="debug",
            )
        },
    )
    pause_on_debug: bool = field(
        default=False,
        metadata={
            "cli": _cli(
                "Pause on certain debug messages, defaults to False",
                group="debug",
            )
        },
    )

    @classmethod
    def from_kwargs(cls, **kwargs: Any) -> Self:
        """Construct from keyword args, ignoring unknown keys."""
        known = {f.name for f in dc_fields(cls)}
        filtered = {k: v for k, v in kwargs.items() if k in known}
        return cls(**filtered)

    def as_dict(self) -> dict[str, Any]:
        """Return runtime fields as a plain dict."""
        return {f.name: getattr(self, f.name) for f in dc_fields(self)}


def coerce_chat_request(
    params_or_request: ChatRequest | dict[str, Any],
) -> ChatRequest:
    """Return a typed request from either a request object or legacy kwargs."""
    if isinstance(params_or_request, ChatRequest):
        return params_or_request
    return ChatRequest.from_kwargs(strict=True, **params_or_request)


# ---------------------------------------------------------------------------
# Module-level field name sets — used by chat_downloader._categorize_parameters
# ---------------------------------------------------------------------------

#: All field names belonging to :class:`DownloaderConfig`.
INIT_PARAM_NAMES: frozenset[str] = frozenset(
    f.name for f in dc_fields(DownloaderConfig)
)

#: All field names belonging to :class:`ChatRequest`.
CHAT_PARAM_NAMES: frozenset[str] = frozenset(
    f.name for f in dc_fields(ChatRequest)
)

#: All field names belonging to :class:`RunConfig`.
RUN_PARAM_NAMES: frozenset[str] = frozenset(
    f.name for f in dc_fields(RunConfig)
)


__all__ = [
    "CHAT_PARAM_NAMES",
    "DEFAULT_BUFFER_SIZE",
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MESSAGE_RECEIVE_TIMEOUT",
    "DEFAULT_MAX_SEEN_MESSAGE_IDS",
    "DEFAULT_READ_TIMEOUT",
    "ChatRequest",
    "DownloaderConfig",
    "INIT_PARAM_NAMES",
    "RUN_PARAM_NAMES",
    "RunConfig",
    "coerce_chat_request",
    "get_field_default",
]
