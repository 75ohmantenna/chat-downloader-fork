# SPDX-License-Identifier: MIT

"""Continuation request handling for YouTube chat polling."""

from __future__ import annotations

from json.decoder import JSONDecodeError
from typing import TYPE_CHECKING, Any

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import RetriesExceeded
from chat_downloader.utils.retry_utils import RetryPolicy

from .client_requests_errors import (
    _apply_retry_or_raise,
    _handle_http_error,
    _handle_json_api_error,
    _handle_missing_live_chat_continuation,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.models import ChatRequest


def _get_continuation_info(
    continuation_url: str,
    session_post: Callable[..., Any],
    program_params: ChatRequest | dict[str, Any],
    *,
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

            if _handle_missing_live_chat_continuation(
                json_response,
                require_live_chat_continuation=require_live_chat_continuation,
                error=error,
                attempt_number=attempt_number,
                max_attempts=max_attempts,
                retry_policy=retry_policy,
                continuation_url=continuation_url,
            ):
                continue

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

        else:
            return json_response

    # Unreachable: all loop paths either return, raise, or continue.
    # Explicit raise satisfies static analysis (mypy return-type check).
    msg = (
        f"Retries exhausted after {max_attempts} attempt(s). "
        f"Endpoint: {continuation_url}"
    )  # pragma: no cover
    raise RetriesExceeded(msg)  # pragma: no cover
