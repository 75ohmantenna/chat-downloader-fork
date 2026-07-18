# SPDX-License-Identifier: MIT

"""ChatRequest dataclass — a single chat retrieval request."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from dataclasses import fields as dc_fields
from typing import Any, Literal, Protocol, Self

from chat_downloader.models._base import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MESSAGE_RECEIVE_TIMEOUT,
    _cli,
)
from chat_downloader.models._site_default import SiteDefault
from chat_downloader.utils.time_utils import ensure_seconds


class _SiteValueResolver(Protocol):
    """Structural interface for objects that resolve site-default values."""

    def get_site_value(self, value: object) -> object: ...


def _validate_positive_integer(
    name: str,
    value: object,
    *,
    allow_none: bool = False,
) -> None:
    """Validate a positive integer request field."""
    if value is None and allow_none:
        return
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        suffix = " or None" if allow_none else ""
        msg = f"{name} must be a positive integer{suffix}, got {value!r}"
        raise ValueError(msg)


def _validate_finite_number(
    name: str,
    value: object,
    *,
    positive: bool,
    allow_none: bool,
) -> None:
    """Validate a finite numeric request field."""
    if value is None and allow_none:
        return
    invalid = (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or (positive and value <= 0)
    )
    if invalid:
        qualifier = "finite positive number" if positive else "finite number"
        suffix = " or None" if allow_none else ""
        msg = f"{name} must be a {qualifier}{suffix}, got {value!r}"
        raise ValueError(msg)


def _validate_time_bound(name: str, value: float | str | None) -> None:
    """Validate a numeric or colon-separated replay time bound."""
    if value is None:
        return
    invalid_time = object()
    if isinstance(value, bool) or ensure_seconds(value, invalid_time) is invalid_time:
        msg = f"{name} must be seconds or an hh:mm:ss value, got {value!r}"
        raise ValueError(msg)


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
                " format (.jsonl/.txt). Other extensions are not supported;"
                " use .jsonl for structured output.",
                group="output",
                flags=["-o"],
            ),
        },
    )
    overwrite: bool = field(
        default=True,
        metadata={"cli": _cli("Overwrite existing output file", group="output")},
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
        metadata={"cli": _cli("Path to custom format definition file", group="format")},
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
        metadata={"cli": _cli("Buffer size for message retrieval", group="twitch")},
    )

    # ── Constructors ──────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        """Validate request fields that have constrained runtime values."""
        _validate_positive_integer(
            "max_messages",
            self.max_messages,
            allow_none=True,
        )
        _validate_positive_integer("max_attempts", self.max_attempts)
        _validate_positive_integer("buffer_size", self.buffer_size)
        if self.chat_type not in ("live", "top"):
            msg = f"chat_type must be 'live' or 'top', got {self.chat_type!r}"
            raise ValueError(msg)
        _validate_finite_number(
            "retry_timeout",
            self.retry_timeout,
            positive=False,
            allow_none=True,
        )
        for name, value in (
            ("timeout", self.timeout),
            ("inactivity_timeout", self.inactivity_timeout),
        ):
            _validate_finite_number(
                name,
                value,
                positive=True,
                allow_none=True,
            )
        _validate_finite_number(
            "message_receive_timeout",
            self.message_receive_timeout,
            positive=True,
            allow_none=False,
        )
        for time_name, time_value in (
            ("start_time", self.start_time),
            ("end_time", self.end_time),
        ):
            _validate_time_bound(time_name, time_value)

    @classmethod
    def from_kwargs(cls, *, strict: bool = False, **kwargs: Any) -> Self:
        """Construct a :class:`ChatRequest` from a ``**kwargs`` dict.

        Known keys are mapped to fields; unknown keys are silently ignored
        by default.  Internal callers that forward an opaque ``params`` dict
        (where the dict may contain keys intended for other consumers) rely on
        this lenient default.  Pass ``strict=True`` to raise
        :exc:`TypeError` when any unknown key is present; the public API
        boundary (:func:`coerce_chat_request`) always uses ``strict=True``.

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

    def resolved_for_site(self, site_object: _SiteValueResolver) -> Self:
        """Resolve any site-default placeholder values for a specific site."""
        return self.with_updates(
            **{
                field.name: site_object.get_site_value(getattr(self, field.name))
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


#: All field names belonging to :class:`ChatRequest`.
CHAT_PARAM_NAMES: frozenset[str] = frozenset(f.name for f in dc_fields(ChatRequest))
