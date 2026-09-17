# SPDX-License-Identifier: MIT

"""Error and retry handling for YouTube continuation requests."""

from __future__ import annotations

from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    IncompleteContinuationError,
    RetriesExceeded,
)
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.string_utils import contains_any_hint

from .continuations import summarize_continuation_payload

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict
    from chat_downloader.utils.retry_utils import RetryPolicy

_RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({403, 429})

_CHALLENGE_HINTS: tuple[str, ...] = (
    "captcha",
    "verify you are human",
    "unusual traffic",
    "recaptcha",
    "challenge",
)


def _contains_challenge_text(text: object) -> bool:
    if not isinstance(text, str):
        return False
    return contains_any_hint(text, _CHALLENGE_HINTS)


def _captcha_guidance_message(*, endpoint: str, detail: str) -> str:
    return (
        "YouTube is requiring a captcha/challenge before chat requests can "
        "continue. "
        f"{detail}. Endpoint: {endpoint}. "
        "Try fresh cookies, reduce request rate, or change request fingerprint "
        "with --request_profile (youtube_android/youtube_ios)."
    )


def _apply_retry_or_raise(
    policy: RetryPolicy,
    attempt_number: int,
    url: str,
    message: str,
    exc_cls: type[Exception],
) -> None:
    """Wait for another attempt or raise with endpoint context."""
    if policy.can_retry(attempt_number):
        policy.wait(attempt_number, interruptible=False)
        return
    msg = f"Retries exhausted. {message}. Endpoint: {url}"
    raise exc_cls(msg)


def _is_retryable_status(code: int | None) -> bool:
    """Return True for 403, 429 and 5xx status codes."""
    if not isinstance(code, int):
        return False
    return code in _RETRYABLE_HTTP_STATUS_CODES or code // 100 == 5


def _retry_or_raise_exhausted(
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
    continuation_url: str,
    error_message: str,
    log_label: str,
    *,
    exc_cls: type[Exception] = RetriesExceeded,
) -> bool:
    """Log, sleep, and return True to retry; raise when budget is gone."""
    log(
        "warning",
        f"Retriable {log_label} "
        f"(attempt {attempt_number}/{max_attempts}): {error_message}",
    )
    if retry_policy.can_retry(attempt_number):
        retry_policy.wait(attempt_number, interruptible=False)
        return True
    msg = (
        f"Retries exhausted after {max_attempts} attempt(s). "
        f"Endpoint: {continuation_url}. Last error: {error_message}"
    )
    raise exc_cls(msg)


def _handle_http_error(
    response: Any,
    continuation_url: str,
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
) -> bool:
    """Retry transient HTTP errors; leave terminal JSON errors to the caller."""
    response_text = getattr(response, "text", "")
    error_message = f"HTTP {response.status_code}"
    try:
        yt_error = response.json().get("error", {}).get("message")
        if yt_error:
            error_message = f"{error_message}: {yt_error}"
    except (JSONDecodeError, ValueError):
        pass
    if _contains_challenge_text(error_message) or _contains_challenge_text(
        response_text
    ):
        raise CaptchaChallengeRequired(
            _captcha_guidance_message(endpoint=continuation_url, detail=error_message),
        )
    if _is_retryable_status(response.status_code):
        return _retry_or_raise_exhausted(
            attempt_number,
            max_attempts,
            retry_policy,
            continuation_url,
            error_message,
            "HTTP/API error",
        )
    log("error", error_message)
    return False


def _handle_json_api_error(
    error: JSONDict,
    continuation_url: str,
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
) -> bool:
    """Handle API challenges, transient statuses, and incomplete responses."""
    _raw_code = error.get("code")
    error_code: int | None = (
        _raw_code
        if isinstance(_raw_code, int) and not isinstance(_raw_code, bool)
        else None
    )
    error_message = error.get("message")
    detail = f"YouTube API error ({error_code}): {error_message}"
    if _contains_challenge_text(error_message):
        raise CaptchaChallengeRequired(
            _captcha_guidance_message(endpoint=continuation_url, detail=detail),
        )
    if _is_retryable_status(error_code):
        return _retry_or_raise_exhausted(
            attempt_number,
            max_attempts,
            retry_policy,
            continuation_url,
            detail,
            "API error",
        )
    if isinstance(error_message, str) and "unknown error" in error_message.lower():
        return _retry_or_raise_exhausted(
            attempt_number,
            max_attempts,
            retry_policy,
            continuation_url,
            detail,
            "incomplete continuation response",
            exc_cls=IncompleteContinuationError,
        )
    return False


def _handle_missing_live_chat_continuation(
    json_response: JSONDict,
    *,
    require_live_chat_continuation: bool,
    error: object,
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
    continuation_url: str,
) -> bool:
    """Return True (retry) when the live-chat continuation block is missing.

    Returns False when the body is acceptable: the guard is disabled, an error
    was already handled upstream, the body is empty, or the
    ``continuationContents.liveChatContinuation`` block is present.
    """
    if not (
        require_live_chat_continuation
        and not error
        and json_response
        and multi_get(json_response, "continuationContents", "liveChatContinuation")
        is None
    ):
        return False
    summary = summarize_continuation_payload(json_response)
    return _retry_or_raise_exhausted(
        attempt_number,
        max_attempts,
        retry_policy,
        continuation_url,
        "Missing continuationContents.liveChatContinuation in response body. "
        f"Summary: {summary}",
        "incomplete continuation response",
        exc_cls=IncompleteContinuationError,
    )
