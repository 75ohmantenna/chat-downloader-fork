# SPDX-License-Identifier: MIT

"""Initial page request handling for YouTube pages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import CaptchaChallengeRequired, RetriesExceeded
from chat_downloader.utils.json_utils import try_parse_json
from chat_downloader.utils.retry_utils import RetryPolicy
from chat_downloader.utils.string_utils import (
    contains_any_hint,
    get_title_of_webpage,
    regex_search,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict


_CHALLENGE_HINTS: tuple[str, ...] = (
    "/sorry/index",
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


def _raise_if_challenge_response(
    response: Any,
    *,
    url: str,
    error_message: str,
) -> None:
    response_url = getattr(response, "url", "")
    response_text = getattr(response, "text", "")
    if not (
        _contains_challenge_text(response_url)
        or _contains_challenge_text(response_text)
        or _contains_challenge_text(error_message)
    ):
        return

    msg = (
        "YouTube is requiring a captcha/challenge before the initial page "
        "request can continue. "
        f"{error_message}. URL: {url}. "
        "Try fresh cookies, reduce request rate, or change request fingerprint "
        "with --request_profile (youtube_android/youtube_ios)."
    )
    raise CaptchaChallengeRequired(msg)


def _get_initial_info(  # noqa: C901 — HTTP status-code dispatch + retry loop are intrinsic
    url: str,
    session_get: Callable[..., Any],
    params: ChatRequest | dict[str, Any] | None,
    yt_initial_data_re: str,
    yt_cfg_re: str,
    yt_initial_player_response_re: str,
) -> tuple[JSONDict, JSONDict, JSONDict]:
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
                    _raise_if_challenge_response(
                        response,
                        url=url,
                        error_message=error_message,
                    )
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

            yt_initial_data = cast(
                "JSONDict",
                try_parse_json(regex_search(html, yt_initial_data_re), None),
            )

            if not yt_initial_data:
                log("debug", html)
                from chat_downloader.errors import ParsingError

                msg = "Unable to parse initial video data"
                raise ParsingError(msg)

            cfg = regex_search(html, yt_cfg_re)
            ytcfg = cast("JSONDict", try_parse_json(cfg, {}))
            player_response = regex_search(
                html,
                yt_initial_player_response_re,
            )
            player_response_info = cast("JSONDict", try_parse_json(player_response, {}))

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
