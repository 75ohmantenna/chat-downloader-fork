# SPDX-License-Identifier: MIT

"""Ratchets for importable public API names."""

from __future__ import annotations

import pytest

import chat_downloader
from chat_downloader import errors, models, sites

EXPECTED_TOP_LEVEL = frozenset(
    [
        "BaseChatDownloader",
        "CaptchaChallengeRequired",
        "Chat",
        "ChatDisabled",
        "ChatDownloader",
        "ChatDownloaderError",
        "ChatGeneratorError",
        "ChatRequest",
        "ContinuousFileWriter",
        "ContinuousWriter",
        "CookieError",
        "DownloaderConfig",
        "FormatError",
        "FormatFileNotFound",
        "FormatNotFound",
        "Image",
        "IncompleteContinuationError",
        "InvalidParameter",
        "InvalidURL",
        "ItemFormatter",
        "KickChatDownloader",
        "KickCountryBlocked",
        "KickError",
        "LoginRequired",
        "NoChatReplay",
        "NoContinuation",
        "NoVideos",
        "ParsingError",
        "Remapper",
        "RetriesExceeded",
        "SiteError",
        "SiteNotSupported",
        "TimedGenerator",
        "TwitchChatDownloader",
        "TwitchError",
        "URLNotProvided",
        "UserNotFound",
        "VideoNotFound",
        "VideoUnavailable",
        "VideoUnplayable",
        "YouTubeChatDownloader",
        "__version__",
        "get_all_sites",
        "run",
    ]
)

EXPECTED_MODELS = frozenset(
    [
        "CHAT_PARAM_NAMES",
        "ChatRequest",
        "DEFAULT_BUFFER_SIZE",
        "DEFAULT_CONNECT_TIMEOUT",
        "DEFAULT_MAX_ATTEMPTS",
        "DEFAULT_MAX_SEEN_MESSAGE_IDS",
        "DEFAULT_MESSAGE_RECEIVE_TIMEOUT",
        "DEFAULT_READ_TIMEOUT",
        "DownloaderConfig",
        "INIT_PARAM_NAMES",
        "RUN_PARAM_NAMES",
        "RunConfig",
        "SiteDefault",
        "coerce_chat_request",
        "get_field_default",
    ]
)

EXPECTED_ERRORS = frozenset(
    [
        "CaptchaChallengeRequired",
        "ChatDisabled",
        "ChatDownloaderError",
        "ChatGeneratorError",
        "CookieError",
        "FormatError",
        "FormatFileNotFound",
        "FormatNotFound",
        "IncompleteContinuationError",
        "InvalidParameter",
        "InvalidURL",
        "LoginRequired",
        "NoChatReplay",
        "NoContinuation",
        "NoVideos",
        "ParsingError",
        "RetriesExceeded",
        "SiteError",
        "SiteNotSupported",
        "URLNotProvided",
        "UserNotFound",
        "VideoNotFound",
        "VideoUnavailable",
        "VideoUnplayable",
    ]
)

EXPECTED_SITES = frozenset(
    [
        "BaseChatDownloader",
        "Chat",
        "Image",
        "KickChatDownloader",
        "KickCountryBlocked",
        "KickError",
        "Remapper",
        "TwitchChatDownloader",
        "TwitchError",
        "YouTubeChatDownloader",
        "get_all_sites",
    ]
)


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (chat_downloader, EXPECTED_TOP_LEVEL),
        (models, EXPECTED_MODELS),
        (errors, EXPECTED_ERRORS),
        (sites, EXPECTED_SITES),
    ],
)
def test_public_surface_is_stable(module, expected) -> None:
    assert set(module.__all__) == expected
    for name in expected:
        assert hasattr(module, name), name
