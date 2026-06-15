# SPDX-License-Identifier: MIT

"""Snapshot tests for the public API surfaces of chat_downloader."""

from __future__ import annotations

import chat_downloader
from chat_downloader import models

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


def test_top_level_public_surface_is_stable() -> None:
    assert set(chat_downloader.__all__) == EXPECTED_TOP_LEVEL
    for name in EXPECTED_TOP_LEVEL:
        assert hasattr(chat_downloader, name), name


def test_models_public_surface_is_stable() -> None:
    assert set(models.__all__) == EXPECTED_MODELS
    for name in EXPECTED_MODELS:
        assert hasattr(models, name), name
