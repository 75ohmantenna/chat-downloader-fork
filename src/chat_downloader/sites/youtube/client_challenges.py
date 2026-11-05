# SPDX-License-Identifier: MIT

"""Canonical challenge evidence shared by YouTube request paths."""

from __future__ import annotations

from chat_downloader.errors import CaptchaChallengeRequired
from chat_downloader.utils.string_utils import contains_any_hint

_CHALLENGE_HINTS: tuple[str, ...] = (
    "/sorry/index",
    "captcha",
    "verify you are human",
    "unusual traffic",
    "recaptcha",
    "challenge",
)


def contains_challenge_text(text: object) -> bool:
    """Recognize provider challenge hints in textual evidence only."""
    return isinstance(text, str) and contains_any_hint(text, _CHALLENGE_HINTS)


def is_challenge_response(response: object, *, error_message: object) -> bool:
    """Inspect the final response URL, body, and decoded error detail."""
    return (
        contains_challenge_text(getattr(response, "url", ""))
        or contains_challenge_text(getattr(response, "text", ""))
        or contains_challenge_text(error_message)
    )


def raise_if_initial_challenge_response(
    response: object,
    *,
    url: str,
    error_message: str,
) -> None:
    """Stop an initial-page/bootstrap challenge without changing retry policy."""
    if not is_challenge_response(response, error_message=error_message):
        return
    msg = (
        "YouTube is requiring a captcha/challenge before the initial page "
        "request can continue. "
        f"{error_message}. URL: {url}. "
        "Try fresh cookies, reduce request rate, or change request fingerprint "
        "with --request_profile (youtube_android/youtube_ios)."
    )
    raise CaptchaChallengeRequired(msg)
