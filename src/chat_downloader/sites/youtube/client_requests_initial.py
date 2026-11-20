# SPDX-License-Identifier: MIT

"""Initial page request handling for YouTube pages."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from json import JSONDecodeError, JSONDecoder
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit

from requests.exceptions import RequestException

from chat_downloader.debugging import log
from chat_downloader.errors import (
    ParsingError,
    RetriesExceeded,
    VideoNotFound,
)
from chat_downloader.sites.session import reject_redirect
from chat_downloader.utils.json_utils import try_parse_json
from chat_downloader.utils.retry_utils import (
    RetryPolicy,
    get_retry_after,
    parse_retry_after,
)
from chat_downloader.utils.string_utils import (
    get_title_of_webpage,
    regex_search,
)

from .client_challenges import raise_if_initial_challenge_response
from .constants_patterns import (
    _YT_CFG_RE,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.models import ChatRequest
    from chat_downloader.utils.json_types import JSONDict


_INITIAL_PAGE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "www.google.com",
        "consent.google.com",
        "consent.youtube.com",
        "accounts.google.com",
    }
)


# Recognize the existing statement boundaries after decoding the object.
_ASSIGNMENT_END = re.compile(r"\s*;\s*(?:$|var\s+(?:meta|head)|\n)")
_SCRIPT_TYPE = re.compile(
    r"module|(?:application|text)/(?:x-)?(?:ecma|java)script"
    r"|text/(?:javascript1\.[0-5]|jscript|livescript)"
)
_JS_STRING_OR_COMMENT = (
    r""""(?:[^"\\]|\\[\s\S])*"|'(?:[^'\\]|\\[\s\S])*'|"""
    r"`(?:[^`\\]|\\[\s\S])*`|(?:<!--|//)[^\r\n]*|/\*[\s\S]*?\*/"
)


class _BootstrapScriptParser(HTMLParser):
    """Read YouTube bootstrap scripts and their JSON assignments."""

    def __init__(self, html: str) -> None:
        super().__init__()
        self.reading = False
        self.dedicated = False
        self.parts: list[str] = []
        self.scripts: list[str] = []
        self.initial_data: JSONDict = {}
        self.feed(html)
        if not self.scripts and not html.lstrip().startswith("<"):
            self.scripts.append(html)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            attributes = dict(attrs)
            self.dedicated = ("id", "yt-initial-data") in attrs
            script_type = (attributes.get("type") or "").strip().lower()
            self.reading = self.dedicated or (
                "src" not in attributes
                and (not script_type or _SCRIPT_TYPE.fullmatch(script_type) is not None)
            )
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.reading:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.reading:
            script = "".join(self.parts)
            if self.dedicated:
                value = try_parse_json(script)
                if isinstance(value, dict) and value and not self.initial_data:
                    self.initial_data = value
            else:
                self.scripts.append(script)
            self.reading = False

    def assignment(self, pattern: str) -> JSONDict:
        """Return the first complete nonempty object assigned to a bootstrap name."""
        decoder = JSONDecoder()
        for script in self.scripts:
            for match in re.finditer(rf"{_JS_STRING_OR_COMMENT}|({pattern})", script):
                if match[1] is None:
                    continue
                try:
                    value, end = decoder.raw_decode(script, match.end())
                except JSONDecodeError:
                    continue
                # The assignment patterns match only values starting with an object.
                if value and _ASSIGNMENT_END.match(script, end):
                    return cast("JSONDict", value)
        return {}


def _get_initial_info(  # noqa: C901 — HTTP status-code dispatch + retry loop are intrinsic
    url: str,
    session_get: Callable[..., Any],
    params: ChatRequest | dict[str, Any] | None,
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
            response = session_get(url, allow_redirects=True)
            reject_redirect(response)
            html = response.text
            response_url = getattr(response, "url", None)
            try:
                final_host = urlsplit(
                    response_url if isinstance(response_url, str) else url
                ).hostname
            except ValueError:
                final_host = None
            safe_host = final_host if final_host in _INITIAL_PAGE_HOSTS else "other"
            log(
                "debug",
                f"YouTube initial page response: HTTP {response.status_code}; "
                f"final_host={safe_host}",
            )

            if response.status_code != 200:
                title = get_title_of_webpage(html)
                error_message = title or f"HTTP {response.status_code}"
                if response.status_code == 404:
                    raise VideoNotFound(error_message)
                if (
                    response.status_code in (403, 429)
                    or response.status_code // 100 == 5
                ):
                    if response.status_code in (403, 429):
                        raise_if_initial_challenge_response(
                            response,
                            url=url,
                            error_message=error_message,
                        )
                        label, detail = "Retriable HTTP error", error_message
                    else:
                        label, detail = (
                            "Server error",
                            title or f"HTTP {response.status_code}",
                        )
                    log(
                        "warning",
                        f"{label} (attempt {attempt_number}/{max_attempts}): {detail}",
                    )
                    if retry_policy.can_retry(attempt_number):
                        retry_policy.wait(
                            attempt_number,
                            interruptible=False,
                            server_minimum=parse_retry_after(
                                getattr(response, "headers", None)
                            ),
                        )
                        continue
                    msg = (
                        f"Retries exhausted after {max_attempts} attempt(s) "
                        f"fetching: {url}. Last error: {error_message}"
                    )
                    raise RetriesExceeded(msg)

            script_parser = _BootstrapScriptParser(html)
            yt_initial_data = script_parser.initial_data or script_parser.assignment(
                _YT_INITIAL_DATA_RE
            )

            if not yt_initial_data:
                log("debug", f"HTML ({len(html)} chars): {html[:500]}")
                msg = "Unable to parse initial video data"
                raise ParsingError(msg)

            cfg = regex_search(html, _YT_CFG_RE)
            ytcfg = cast("JSONDict", try_parse_json(cfg, {}))
            player_response_info = script_parser.assignment(
                _YT_INITIAL_PLAYER_RESPONSE_RE
            )

        except (RequestException, OSError) as e:
            log(
                "error",
                f"Network error (attempt {attempt_number}/{max_attempts}): "
                f"{type(e).__name__}: {e}",
            )
            if retry_policy.can_retry(attempt_number):
                retry_policy.wait(
                    attempt_number,
                    interruptible=False,
                    server_minimum=get_retry_after(e),
                )
                continue
            raise
        else:
            return yt_initial_data, ytcfg, player_response_info

    msg = f"Retries exhausted after {max_attempts} attempt(s) fetching: {url}"
    raise RetriesExceeded(  # pragma: no cover
        msg,
    )
