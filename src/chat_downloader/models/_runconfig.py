# SPDX-License-Identifier: MIT

"""RunConfig dataclass — runtime execution controls."""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields as dc_fields
from typing import Any, Self

from chat_downloader._shared_defaults import DEFAULT_MAX_SEEN_MESSAGE_IDS
from chat_downloader.models._base import _cli
from chat_downloader.models._request import ChatRequest


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
    """Return a typed :class:`ChatRequest` from a request object or params dict.

    Applies ``strict=True`` so any unknown keys in a plain dict are rejected
    at the public API boundary.
    """
    if isinstance(params_or_request, ChatRequest):
        return params_or_request
    return ChatRequest.from_kwargs(strict=True, **params_or_request)


#: All field names belonging to :class:`RunConfig`.
RUN_PARAM_NAMES: frozenset[str] = frozenset(
    f.name for f in dc_fields(RunConfig)
)
