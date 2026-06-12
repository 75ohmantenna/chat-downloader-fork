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
from chat_downloader.sites.models import SiteDefault


class _SiteValueResolver(Protocol):
    """Structural interface for objects that resolve site-default values."""

    def get_site_value(self, value: object) -> object: ...


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
        if self.max_messages is not None and (
            not isinstance(self.max_messages, int) or self.max_messages <= 0
        ):
            msg = (
                "max_messages must be a positive integer or None, "
                f"got {self.max_messages!r}"
            )
            raise ValueError(msg)
        if self.max_attempts < 1:
            msg = f"max_attempts must be >= 1, got {self.max_attempts!r}"
            raise ValueError(msg)
        if self.buffer_size <= 0:
            msg = f"buffer_size must be positive, got {self.buffer_size!r}"
            raise ValueError(msg)
        if self.chat_type not in ("live", "top"):
            msg = f"chat_type must be 'live' or 'top', got {self.chat_type!r}"
            raise ValueError(msg)
        for name, value in (
            ("timeout", self.timeout),
            ("inactivity_timeout", self.inactivity_timeout),
        ):
            if value is not None and (not math.isfinite(value) or value <= 0):
                msg = f"{name} must be a finite positive number or None, got {value!r}"
                raise ValueError(msg)
        if not math.isfinite(self.message_receive_timeout) or (
            self.message_receive_timeout <= 0
        ):
            msg = (
                "message_receive_timeout must be a finite positive number, "
                f"got {self.message_receive_timeout!r}"
            )
            raise ValueError(msg)

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
