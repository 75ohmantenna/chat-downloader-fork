# SPDX-License-Identifier: MIT

"""Snapshot tests for the public API surfaces of chat_downloader."""

from __future__ import annotations

import chat_downloader
from chat_downloader import errors, models, sites

EXPECTED_TOP_LEVEL: frozenset[str] = frozenset(
    {
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
    }
)

EXPECTED_MODELS: frozenset[str] = frozenset(
    {
        "CHAT_PARAM_NAMES",
        "DEFAULT_BUFFER_SIZE",
        "DEFAULT_CONNECT_TIMEOUT",
        "DEFAULT_MAX_ATTEMPTS",
        "DEFAULT_MAX_SEEN_MESSAGE_IDS",
        "DEFAULT_MESSAGE_RECEIVE_TIMEOUT",
        "DEFAULT_READ_TIMEOUT",
        "INIT_PARAM_NAMES",
        "RUN_PARAM_NAMES",
        "ChatRequest",
        "DownloaderConfig",
        "RunConfig",
        "SiteDefault",
        "coerce_chat_request",
        "get_field_default",
    }
)

EXPECTED_ERRORS: frozenset[str] = frozenset(
    {
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
    }
)

EXPECTED_SITES: frozenset[str] = frozenset(
    {
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
    }
)


def test_top_level_public_surface_is_stable() -> None:
    assert set(chat_downloader.__all__) == EXPECTED_TOP_LEVEL
    for name in EXPECTED_TOP_LEVEL:
        assert hasattr(chat_downloader, name), name


def test_models_public_surface_is_stable() -> None:
    assert set(models.__all__) == EXPECTED_MODELS
    for name in EXPECTED_MODELS:
        assert hasattr(models, name), name


def test_errors_public_surface_is_stable() -> None:
    assert set(errors.__all__) == EXPECTED_ERRORS
    for name in EXPECTED_ERRORS:
        assert hasattr(errors, name), name


def test_sites_public_surface_is_stable() -> None:
    assert set(sites.__all__) == EXPECTED_SITES
    for name in EXPECTED_SITES:
        assert hasattr(sites, name), name
