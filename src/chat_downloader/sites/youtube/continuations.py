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
    JSONList,
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

    :param actions: List of raw action dicts extracted from the payload.
        Empty when the response carries no actions (e.g. a heartbeat tick
        for a live stream with no new messages).
    :type actions: list
    :param next_continuation: Opaque continuation token for the next request,
        or ``None`` when no further pages are available.
    :type next_continuation: str | None
    :param timeout_ms: Milliseconds to wait before the next request, as
        requested by YouTube. ``None`` when no timeout hint is available.
    :type timeout_ms: int | None
    :param is_end: ``True`` when no continuation token was found and the
        stream is considered finished.
    :type is_end: bool
    :param debug_info: Small dictionary of diagnostic fields preserved for
        logging (continuation key, raw continuation entry). Not intended
        for programmatic use.
    :type debug_info: dict
    """

    actions: list[JSONAny] = field(default_factory=list)
    next_continuation: str | None = None
    timeout_ms: int | None = None
    is_end: bool = False
    debug_info: dict[str, object] = field(default_factory=dict)


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


def _extract_actions(info: JSONDict) -> JSONList:
    """Return the ``actions`` list from a ``liveChatContinuation`` dict."""
    return get_list(info, "actions")


_POLL_DELAY_FIELDS = (
    "timeoutMs",
    "timeout_ms",
    "pollingIntervalMillis",
    "polling_interval_millis",
)


def _extract_next_continuation(
    info: JSONDict,
) -> tuple[str | None, JSONAny, object, dict[str, object]]:
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

        if continuation_key in _KNOWN_CHAT_CONTINUATIONS:
            token = get_str(continuation_info, "continuation") or None
            click_tracking = continuation_info.get(
                "clickTrackingParams",
            ) or continuation_info.get("trackingParams")
            raw_poll_delay_ms = _extract_raw_poll_delay_ms(continuation_info)
            debug: dict[str, object] = {
                "continuation_key": continuation_key,
                "continuation_entry": continuation_info,
            }
            return token, click_tracking, raw_poll_delay_ms, debug

        if continuation_key in _KNOWN_SEEK_CONTINUATIONS:
            # Seek-only continuation — not a chat token; keep searching.
            continue

        # Unknown continuation key — extract token generically if available.
        token = get_str(continuation_info, "continuation") or None
        click_tracking = continuation_info.get(
            "clickTrackingParams",
        ) or continuation_info.get("trackingParams")
        raw_poll_delay_ms = _extract_raw_poll_delay_ms(continuation_info)
        return (
            token,
            click_tracking,
            raw_poll_delay_ms,
            {
                "continuation_key": continuation_key,
                "continuation_entry": continuation_info,
                "unknown": True,
            },
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
    if isinstance(raw_timeout, bool):
        log("debug", f"Ignoring invalid continuation timeout: {raw_timeout}")
        return None
    if not isinstance(raw_timeout, (str, int, float)):
        log("debug", f"Ignoring invalid continuation timeout: {raw_timeout}")
        return None
    try:
        return int(raw_timeout)
    except (TypeError, ValueError):
        log("debug", f"Ignoring invalid continuation timeout: {raw_timeout}")
        return None


def _detect_end(next_continuation: str | None) -> bool:
    """Return ``True`` when there is no next continuation token."""
    return next_continuation is None


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

    actions = _extract_actions(info)
    token, _click_tracking, raw_timeout, debug_info = _extract_next_continuation(info)
    if debug_info:
        debug_info = {
            **debug_info,
            "payload_summary": summarize_continuation_payload(payload),
        }

    timeout_ms = _extract_timeout_ms(raw_timeout)
    is_end = _detect_end(token)

    return ContinuationParseResult(
        actions=actions,
        next_continuation=token,
        timeout_ms=timeout_ms,
        is_end=is_end,
        debug_info=debug_info,
    )


__all__ = [
    "ContinuationParseResult",
    "parse_continuation_response",
    "summarize_continuation_payload",
]
