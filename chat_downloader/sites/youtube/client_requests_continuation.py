# SPDX-License-Identifier: MIT

"""Continuation request handling for YouTube chat polling."""

from __future__ import annotations

from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import (
    CaptchaChallengeRequired,
    IncompleteContinuationError,
    RetriesExceeded,
)
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.retry_utils import RetryPolicy
from chat_downloader.utils.string_utils import contains_any_hint

from .continuations import summarize_continuation_payload

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.models import ChatRequest


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
    """Wait and return if a retry is allowed; otherwise raise exc_cls.

    Consolidates the ``can_retry → wait → raise`` pattern shared across all
    error-handling paths in the continuation retry loop.

    Args:
        policy: The :class:`RetryPolicy` governing wait behaviour.
        attempt_number: Current attempt number (1-based).
        url: Endpoint URL, included verbatim in the raised exception message.
        message: Human-readable error description.
        exc_cls: Exception class to instantiate and raise when retries are
            exhausted.

    Raises:
        exc_cls: When ``policy.can_retry(attempt_number)`` returns ``False``,
            with *message* and *url* embedded in the exception text.
    """
    if policy.can_retry(attempt_number):
        policy.wait(attempt_number, interruptible=False)
        return
    raise exc_cls(f"Retries exhausted. {message}. Endpoint: {url}")


def _retry_or_raise_incomplete(
    attempt_number: int,
    reason: str,
    max_attempts: int,
    retry_policy: RetryPolicy,
    continuation_url: str,
) -> None:
    log(
        "warning",
        f"Retriable incomplete continuation response (attempt {attempt_number}/"
        f"{max_attempts}): {reason}",
    )
    if retry_policy.can_retry(attempt_number):
        retry_policy.wait(attempt_number, interruptible=False)
        return
    msg = (
        f"Retries exhausted after {max_attempts} attempt(s). "
        f"Endpoint: {continuation_url}. Last error: {reason}"
    )
    raise IncompleteContinuationError(msg)


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
    raise RetriesExceeded(msg)


def _handle_http_error(
    response: Any,
    continuation_url: str,
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
) -> bool:
    """Handle an HTTP error response.

    Returns True if the caller should retry (continue). Returns False only for
    terminal non-retryable statuses whose bodies may still contain a structured
    YouTube JSON error; the caller then parses the body and lets
    ``_handle_json_api_error`` choose the final exception.

    Args:
        response: The HTTP response object with ``status_code`` and ``text``.
        continuation_url: The endpoint URL, used in error messages.
        attempt_number: Current attempt number (1-based).
        max_attempts: Total attempt budget.
        retry_policy: Controls wait behaviour between retries.

    Returns:
        True to signal the caller to retry, False to fall through.

    Raises:
        CaptchaChallengeRequired: If challenge text is detected.
        RetriesExceeded: If a retryable status code exhausts the retry budget.
    """
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
            _captcha_guidance_message(
                endpoint=continuation_url, detail=error_message
            ),
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
    error: dict[str, Any],
    continuation_url: str,
    attempt_number: int,
    max_attempts: int,
    retry_policy: RetryPolicy,
) -> bool:
    """Handle a JSON-body API error from the YouTube continuation endpoint.

    Returns True if the caller should retry (continue). Raises on captcha or
    when the retry budget is exhausted for retryable codes.

    Args:
        error: The ``error`` dict extracted from the JSON response body.
        continuation_url: The endpoint URL, used in error messages.
        attempt_number: Current attempt number (1-based).
        max_attempts: Total attempt budget.
        retry_policy: Controls wait behaviour between retries.

    Returns:
        True to signal the caller to retry.

    Raises:
        CaptchaChallengeRequired: If challenge text is detected in the message.
        RetriesExceeded: If a retryable error code exhausts the retry budget.
        IncompleteContinuationError: If an "unknown error" exhausts retries.
    """
    error_code = error.get("code")
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
    if (
        isinstance(error_message, str)
        and "unknown error" in error_message.lower()
    ):
        _retry_or_raise_incomplete(
            attempt_number,
            detail,
            max_attempts,
            retry_policy,
            continuation_url,
        )
        return True
    return False


def _get_continuation_info(
    continuation_url: str,
    session_post: Callable[..., Any],
    program_params: ChatRequest | dict[str, Any],
    require_live_chat_continuation: bool = True,
    **post_kwargs: Any,
) -> dict[str, Any]:
    """Get continuation information from YouTube API with retry handling."""
    from chat_downloader.models import ChatRequest

    request = (
        program_params
        if isinstance(program_params, ChatRequest)
        else ChatRequest.from_kwargs(**program_params)
    )
    max_attempts = request.max_attempts
    retry_timeout = request.retry_timeout
    retry_policy = RetryPolicy(
        max_attempts=max_attempts,
        retry_timeout=retry_timeout,
        interruptible_retry=False,
    )

    for attempt_number in range(1, max_attempts + 1):
        response_text = ""
        try:
            response = session_post(continuation_url, **post_kwargs)
            response_text = getattr(response, "text", "")

            if response.status_code >= 400 and _handle_http_error(
                response,
                continuation_url,
                attempt_number,
                max_attempts,
                retry_policy,
            ):
                continue

            json_response: dict[str, Any] = response.json()

            error = json_response.get("error")
            if error and _handle_json_api_error(
                error,
                continuation_url,
                attempt_number,
                max_attempts,
                retry_policy,
            ):
                continue

            if (
                require_live_chat_continuation
                and not error
                and json_response
                and multi_get(
                    json_response,
                    "continuationContents",
                    "liveChatContinuation",
                )
                is None
            ):
                summary = summarize_continuation_payload(json_response)
                _retry_or_raise_incomplete(
                    attempt_number,
                    "Missing continuationContents.liveChatContinuation in "
                    "response body. "
                    f"Summary: {summary}",
                    max_attempts,
                    retry_policy,
                    continuation_url,
                )
                continue

            return json_response

        except JSONDecodeError:
            log(
                "error",
                f"Unable to parse JSON (attempt {attempt_number}/"
                f"{max_attempts}): {response_text}",
            )
            _apply_retry_or_raise(
                retry_policy,
                attempt_number,
                continuation_url,
                f"Unable to parse JSON: {response_text[:200]}",
                RetriesExceeded,
            )
            continue

        except (RequestException, OSError) as exc:
            log(
                "error",
                f"Network error (attempt {attempt_number}/{max_attempts}): "
                f"{type(exc).__name__}: {exc}",
            )
            _apply_retry_or_raise(
                retry_policy,
                attempt_number,
                continuation_url,
                f"{type(exc).__name__}: {exc}",
                RetriesExceeded,
            )
            continue

    # Unreachable: all loop paths either return, raise, or continue.
    # Explicit raise satisfies static analysis (mypy return-type check).
    msg = (
        f"Retries exhausted after {max_attempts} attempt(s). "
        f"Endpoint: {continuation_url}"
    )  # pragma: no cover
    raise RetriesExceeded(msg)  # pragma: no cover
