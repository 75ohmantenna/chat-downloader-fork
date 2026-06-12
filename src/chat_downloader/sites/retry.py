# SPDX-License-Identifier: MIT

"""Shared retry orchestration helpers for site downloaders."""

from __future__ import annotations

from json import JSONDecodeError
from typing import TYPE_CHECKING, Any

from chat_downloader.debugging import log
from chat_downloader.errors import RetriesExceeded
from chat_downloader.utils.retry_utils import RetryPolicy
from chat_downloader.utils.string_utils import get_title_of_webpage
from chat_downloader.utils.timed_generator import polling_sleep

if TYPE_CHECKING:
    from chat_downloader.models import ChatRequest


def _attempt_numbers(max_attempts: int) -> range:
    """Return a retry range, raising RetriesExceeded when max_attempts < 1."""
    if max_attempts < 1:
        msg = f"Maximum number of retries has been reached ({max_attempts})."
        raise RetriesExceeded(
            msg,
        )
    from chat_downloader.utils.conversion_utils import attempts

    return attempts(max_attempts)


def retry(
    attempt_number: int,
    max_attempts: int = 1,
    error: Exception | None = None,
    retry_timeout: float | None = None,
    text: Any = None,
    interruptible_retry: bool = True,
    request: ChatRequest | None = None,
) -> None:
    """Retry after an error occurs using the shared retry policy."""
    if request is not None:
        retry_config = request.retry_kwargs()
        max_attempts = retry_config["max_attempts"]
        retry_timeout = retry_config["retry_timeout"]
        interruptible_retry = retry_config["interruptible_retry"]

    policy = RetryPolicy(
        max_attempts=max_attempts,
        retry_timeout=retry_timeout,
        interruptible_retry=interruptible_retry,
    )

    if not policy.can_retry(attempt_number):
        msg = f"Maximum number of retries has been reached ({max_attempts})."
        raise RetriesExceeded(
            msg,
        ) from error

    if text is None:
        text = []
    elif not isinstance(text, (tuple, list)):
        text = [text]

    sleep_text = policy.sleep_text(attempt_number, interruptible=interruptible_retry)

    retry_text = f"Retry #{attempt_number}/{max_attempts} {sleep_text}."

    if isinstance(error, Exception):
        retry_text += f" {error} ({error.__class__.__name__})"

    if isinstance(error, JSONDecodeError):
        log("debug", f"JSONDecodeError at pos={error.pos!r}: {error.msg!r}")
        page_title = get_title_of_webpage(error.doc)
        if page_title:
            log("debug", f"Title: {page_title}")

    log("warning", [*text, retry_text])

    policy.wait(
        attempt_number,
        interruptible=interruptible_retry,
        sleep_func=polling_sleep,
    )
