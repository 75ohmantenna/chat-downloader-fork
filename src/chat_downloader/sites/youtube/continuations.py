# SPDX-License-Identifier: MIT

"""YouTube live-chat continuation models and response parser."""

from __future__ import annotations

from dataclasses import dataclass, field

from chat_downloader.debugging import log
from chat_downloader.errors import IncompleteContinuationError
from chat_downloader.utils.dict_utils import multi_get, try_get_first_key
from chat_downloader.utils.json_types import (
    JSONAny,
    JSONDict,
    get_list,
    get_str,
)

_KNOWN_SEEK_CONTINUATIONS: frozenset[str] = frozenset({"playerSeekContinuationData"})
_KNOWN_CHAT_CONTINUATIONS: frozenset[str] = frozenset(
    {
        "invalidationContinuationData",
        "liveChatReplayContinuationData",
        "reloadContinuationData",
        "timedContinuationData",
    }
)


@dataclass(slots=True)
class ContinuationParseResult:
    """Parsed result from a single YouTube live-chat continuation response.

    Attributes:
        actions: Raw action dictionaries extracted from the payload. Empty for
            responses such as live heartbeat ticks with no new messages.
        next_continuation: Opaque token for the next request, or ``None`` when
            no further pages are available.
        timeout_ms: Provider-requested delay before the next request in
            milliseconds, or ``None`` when no hint is available.
        is_end: Whether no continuation token was found.
        debug_info: Bounded diagnostic fields for logging, not a stable public
            interface.
        click_tracking_params: Keyword-only click-tracking value for the next
            request, or ``None`` when absent.
    """

    actions: list[JSONAny] = field(default_factory=list)
    next_continuation: str | None = None
    timeout_ms: int | None = None
    is_end: bool = False
    debug_info: dict[str, object] = field(default_factory=dict)
    click_tracking_params: str | None = field(default=None, kw_only=True)


def summarize_continuation_payload(payload: JSONDict) -> dict[str, object]:
    """Return a compact, fixture-friendly summary of a continuation payload."""
    summary: dict[str, object] = {
        "top_level_keys": list(payload.keys()),
    }

    if "error" in payload:
        error = payload.get("error") or {}
        if isinstance(error, dict):
            summary["error"] = {
                "code": error.get("code"),
                "message": error.get("message"),
            }

    continuation_contents = payload.get("continuationContents")
    if isinstance(continuation_contents, dict):
        summary["continuation_contents_keys"] = list(continuation_contents.keys())

    info = multi_get(payload, "continuationContents", "liveChatContinuation")
    if isinstance(info, dict):
        summary["live_chat_keys"] = list(info.keys())
        actions = info.get("actions")
        if isinstance(actions, list):
            summary["actions_count"] = len(actions)
        continuations = info.get("continuations")
        if isinstance(continuations, list):
            summary["continuation_keys"] = [
                try_get_first_key(item)
                for item in continuations
                if isinstance(item, dict) and try_get_first_key(item) is not None
            ]

    return summary


_POLL_DELAY_FIELDS = (
    "timeoutMs",
    "timeout_ms",
    "pollingIntervalMillis",
    "polling_interval_millis",
)


def _extract_next_continuation(
    info: JSONDict,
) -> tuple[str | None, str | None, object, dict[str, object]]:
    """Scan continuations and return the first chat continuation entry.

    Seek-only continuations are intentionally skipped.
    """
    for cont in get_list(info, "continuations"):
        if not isinstance(cont, dict):
            continue
        continuation_key = next(iter(cont), None)
        if continuation_key is None:
            continue
        continuation_info = cont[continuation_key]
        if not isinstance(continuation_info, dict):
            continue

        if continuation_key in _KNOWN_SEEK_CONTINUATIONS:
            # Seek-only continuation — not a chat token; keep searching.
            continue

        debug: dict[str, object] = {
            "continuation_key": continuation_key,
            "continuation_entry": continuation_info,
        }
        if continuation_key not in _KNOWN_CHAT_CONTINUATIONS:
            debug["unknown"] = True
        return (
            get_str(continuation_info, "continuation") or None,
            get_str(continuation_info, "clickTrackingParams")
            or get_str(continuation_info, "trackingParams")
            or None,
            _extract_raw_poll_delay_ms(continuation_info),
            debug,
        )

    return None, None, None, {}


def _extract_raw_poll_delay_ms(
    continuation_info: JSONDict,
) -> object:
    """Return YouTube's raw poll-delay hint from known field names."""
    for delay_field in _POLL_DELAY_FIELDS:
        if delay_field in continuation_info:
            return continuation_info[delay_field]
    return None


def _extract_timeout_ms(raw_timeout: object) -> int | None:
    """Return YouTube's raw continuation timeout hint in milliseconds."""
    if raw_timeout is None:
        return None
    if isinstance(raw_timeout, bool) or not isinstance(raw_timeout, (str, int, float)):
        log("debug", f"Ignoring invalid continuation timeout: {raw_timeout}")
        return None
    try:
        return int(raw_timeout)
    except (TypeError, ValueError):
        log("debug", f"Ignoring invalid continuation timeout: {raw_timeout}")
        return None


def parse_continuation_response(
    payload: JSONDict,
) -> ContinuationParseResult:
    """Parse a raw YouTube live-chat continuation API response."""
    if "error" in payload:
        summary = summarize_continuation_payload(payload)
        msg = (
            "YouTube continuation response contains an API error payload. "
            f"Summary: {summary}"
        )
        raise IncompleteContinuationError(
            msg,
        )

    info = multi_get(payload, "continuationContents", "liveChatContinuation")
    if info is None:
        summary = summarize_continuation_payload(payload)
        msg = (
            "Unrecognized YouTube continuation response shape. "
            f"Summary: {summary}. "
            "Expected 'continuationContents.liveChatContinuation'."
        )
        raise IncompleteContinuationError(
            msg,
        )
    token, click_tracking, raw_timeout, debug_info = _extract_next_continuation(info)
    if debug_info:
        debug_info = {
            **debug_info,
            "payload_summary": summarize_continuation_payload(payload),
        }

    return ContinuationParseResult(
        actions=get_list(info, "actions"),
        next_continuation=token,
        timeout_ms=_extract_timeout_ms(raw_timeout),
        is_end=token is None,
        click_tracking_params=click_tracking,
        debug_info=debug_info,
    )


__all__ = [
    "ContinuationParseResult",
    "parse_continuation_response",
    "summarize_continuation_payload",
]
