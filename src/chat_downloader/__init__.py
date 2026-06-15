# SPDX-License-Identifier: MIT

"""Top-level package for chat-downloader.

This is 75ohmantenna's fork of xenova's MIT-licensed chat-downloader with 2026
enhancements. Portions of this codebase have been developed with AI assistance.
"""

from __future__ import annotations

from .chat_downloader import ChatDownloader, run
from .errors import (
    CaptchaChallengeRequired,
    ChatDisabled,
    ChatDownloaderError,
    ChatGeneratorError,
    CookieError,
    FormatError,
    FormatFileNotFound,
    FormatNotFound,
    IncompleteContinuationError,
    InvalidParameter,
    InvalidURL,
    LoginRequired,
    NoChatReplay,
    NoContinuation,
    NoVideos,
    ParsingError,
    RetriesExceeded,
    SiteError,
    SiteNotSupported,
    URLNotProvided,
    UserNotFound,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
)
from .formatting import ItemFormatter
from .metadata import __version__
from .models import ChatRequest, DownloaderConfig
from .output import ContinuousFileWriter, ContinuousWriter
from .sites import get_all_sites
from .sites.base import BaseChatDownloader
from .sites.kick import KickChatDownloader, KickError
from .sites.models import Chat, Image
from .sites.remap import Remapper
from .sites.twitch import TwitchChatDownloader, TwitchError
from .sites.youtube import YouTubeChatDownloader
from .utils.timed_generator import TimedGenerator

__all__ = [
    "BaseChatDownloader",
    "CaptchaChallengeRequired",
    "Chat",
    "ChatDisabled",
    # Main classes
    "ChatDownloader",
    # Errors
    "ChatDownloaderError",
    "ChatGeneratorError",
    "ChatRequest",
    "ContinuousFileWriter",
    "ContinuousWriter",
    "CookieError",
    # Typed config/request objects
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
]
