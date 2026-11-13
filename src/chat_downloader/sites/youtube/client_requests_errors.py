# SPDX-License-Identifier: MIT

"""Error and retry handling for YouTube continuation requests."""

from __future__ import annotations

from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    ChatDownloaderError,
    IncompleteContinuationError,
    RetriesExceeded,
)
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_dict
from chat_downloader.utils.retry_utils import parse_retry_after

from .client_challenges import contains_challenge_text, is_challenge_response
from .continuations import summarize_continuation_payload

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict
    from chat_downloader.utils.retry_utils import RetryPolicy

_RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({403, 429})


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
    *,
    server_minimum: float | None = None,
) -> None:
    """Wait for another attempt or raise with endpoint context."""
    if policy.can_retry(attempt_number):
        policy.wait(attempt_number, interruptible=False, server_minimum=server_minimum)
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
    server_minimum: float | None = None,
) -> bool:
    """Log, sleep, and return True to retry; raise when budget is gone."""
    log(
        "warning",
        f"Retriable {log_label} "
        f"(attempt {attempt_number}/{max_attempts}): {error_message}",
    )
    if retry_policy.can_retry(attempt_number):
        retry_policy.wait(
            attempt_number, interruptible=False, server_minimum=server_minimum
        )
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
    *,
    allow_terminal_error: bool = True,
) -> bool:
    """Retry transient HTTP errors; leave terminal JSON errors to the caller."""
    error_message = f"HTTP {response.status_code}"
    try:
        payload = response.json()
        yt_error = (
            get_dict(payload, "error").get("message")
            if isinstance(payload, dict)
            else None
        )
        if yt_error:
            error_message = f"{error_message}: {yt_error}"
    except (JSONDecodeError, ValueError):
        pass
    if is_challenge_response(response, error_message=error_message):
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
            server_minimum=parse_retry_after(getattr(response, "headers", None)),
        )
    if not allow_terminal_error:
        msg = (
            f"YouTube browse request failed: {error_message}. "
            f"Endpoint: {continuation_url}"
        )
        raise ChatDownloaderError(msg)
    # The continuation owner may recover a rejected initial chat request through a
    # fresh profile. Terminal errors are reported by the run error boundary.
    log("debug", error_message)
    return False


def _handle_json_api_error(
    error: JSONDict,
    continuation_url: str,
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
    *,
    server_minimum: float | None = None,
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
    if contains_challenge_text(error_message):
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
            server_minimum=server_minimum,
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
            server_minimum=server_minimum,
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
    server_minimum: float | None = None,
) -> bool:
    """Retry absent, empty, or wrongly typed required live-chat blocks.

    Disabled validation and already handled API errors bypass this guard.
    A nonempty dictionary, including one with ``actions=[]``, is acceptable.
    """
    if not require_live_chat_continuation or error:
        return False
    info = multi_get(json_response, "continuationContents", "liveChatContinuation")
    if isinstance(info, dict) and info:
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
        server_minimum=server_minimum,
    )


def _validate_continuation_body(
    payload: object,
    *,
    require_live_chat_continuation: bool,
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
    continuation_url: str,
    server_minimum: float | None,
) -> JSONDict | None:
    """Validate a decoded page before browse extraction or chat recovery."""
    if not isinstance(payload, dict):
        _retry_or_raise_exhausted(
            attempt_number,
            max_attempts,
            retry_policy,
            continuation_url,
            "Expected a JSON object in continuation response body.",
            "incomplete continuation response",
            exc_cls=IncompleteContinuationError,
            server_minimum=server_minimum,
        )
        return None
    error = payload.get("error")
    if isinstance(error, dict) and _handle_json_api_error(
        error,
        continuation_url,
        attempt_number,
        max_attempts,
        retry_policy,
        server_minimum=server_minimum,
    ):
        return None
    if not require_live_chat_continuation and "error" in payload:
        msg = (
            "YouTube browse response contains an API error. "
            f"Endpoint: {continuation_url}. Error: {error}"
        )
        raise ChatDownloaderError(msg)
    if _handle_missing_live_chat_continuation(
        payload,
        require_live_chat_continuation=require_live_chat_continuation,
        error=error,
        attempt_number=attempt_number,
        max_attempts=max_attempts,
        retry_policy=retry_policy,
        continuation_url=continuation_url,
        server_minimum=server_minimum,
    ):
        return None
    return payload
