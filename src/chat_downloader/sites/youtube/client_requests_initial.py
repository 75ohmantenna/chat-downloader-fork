# SPDX-License-Identifier: MIT

"""Initial page request handling for YouTube pages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import RetriesExceeded
from chat_downloader.utils.json_utils import try_parse_json
from chat_downloader.utils.retry_utils import RetryPolicy
from chat_downloader.utils.string_utils import (
    get_title_of_webpage,
    regex_search,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.models import ChatRequest


def _get_initial_info(  # noqa: C901 — HTTP status-code dispatch + retry loop are intrinsic
    url: str,
    session_get: Callable[..., Any],
    params: ChatRequest | dict[str, Any] | None,
    yt_initial_data_re: str,
    yt_cfg_re: str,
    yt_initial_player_response_re: str,
) -> tuple[Any, Any, Any]:
    """Get initial YouTube page data with retry handling."""
    if params is None:
        max_attempts = 1
        retry_timeout = None
    else:
        from chat_downloader.models import ChatRequest

        request = (
            params
            if isinstance(params, ChatRequest)
            else ChatRequest.from_kwargs(**params)
        )
        max_attempts = request.max_attempts
        retry_timeout = request.retry_timeout
    retry_policy = RetryPolicy(
        max_attempts=max_attempts,
        retry_timeout=retry_timeout,
        interruptible_retry=False,
    )
    for attempt_number in range(1, max_attempts + 1):
        try:
            response = session_get(url)
            html = response.text

            if response.status_code != 200:
                title = get_title_of_webpage(html)
                error_message = title or f"HTTP {response.status_code}"
                if response.status_code == 404:
                    from chat_downloader.errors import VideoNotFound

                    raise VideoNotFound(error_message)
                if response.status_code in (403, 429):
                    log(
                        "warning",
                        f"Retriable HTTP error (attempt {attempt_number}/"
                        f"{max_attempts}): {error_message}",
                    )
                    if retry_policy.can_retry(attempt_number):
                        retry_policy.wait(attempt_number, interruptible=False)
                        continue
                    msg = (
                        f"Retries exhausted after {max_attempts} attempt(s) "
                        f"fetching: {url}. Last error: {error_message}"
                    )
                    raise RetriesExceeded(
                        msg,
                    )
                if response.status_code // 100 == 5:
                    log(
                        "warning",
                        f"Server error (attempt {attempt_number}/"
                        f"{max_attempts}): {title}",
                    )
                    if retry_policy.can_retry(attempt_number):
                        retry_policy.wait(attempt_number, interruptible=False)
                        continue
                    msg = (
                        f"Retries exhausted after {max_attempts} attempt(s) "
                        f"fetching: {url}. Last error: {error_message}"
                    )
                    raise RetriesExceeded(
                        msg,
                    )

            yt_initial_data = try_parse_json(
                regex_search(html, yt_initial_data_re),
                None,
            )

            if not yt_initial_data:
                log("debug", html)
                from chat_downloader.errors import ParsingError

                msg = "Unable to parse initial video data"
                raise ParsingError(msg)

            cfg = regex_search(html, yt_cfg_re)
            ytcfg = try_parse_json(cfg, {})
            player_response = regex_search(
                html,
                yt_initial_player_response_re,
            )
            player_response_info = try_parse_json(player_response, {})

        except (RequestException, OSError) as e:
            log(
                "error",
                f"Network error (attempt {attempt_number}/{max_attempts}): "
                f"{type(e).__name__}: {e}",
            )
            if retry_policy.can_retry(attempt_number):
                retry_policy.wait(attempt_number, interruptible=False)
                continue
            raise
        else:
            return yt_initial_data, ytcfg, player_response_info

    msg = f"Retries exhausted after {max_attempts} attempt(s) fetching: {url}"
    raise RetriesExceeded(  # pragma: no cover
        msg,
    )
