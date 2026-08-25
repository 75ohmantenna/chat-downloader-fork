# SPDX-License-Identifier: MIT

"""Public exception hierarchy for chat-downloader."""

from __future__ import annotations


class ChatDownloaderError(Exception):
    """Base class for chat-downloader errors."""


class InvalidParameter(ChatDownloaderError):
    """Raised if an invalid parameter is specified."""


class RetriesExceeded(ChatDownloaderError):
    """Raised after the maximum number of retries has been reached."""


class IncompleteContinuationError(RetriesExceeded):
    """Raised when continuation payload shape stays incomplete after retries."""


class VideoNotFound(ChatDownloaderError):
    """Raised when a video cannot be found."""


class UserNotFound(ChatDownloaderError):
    """Raised when a user cannot be found."""


class NoVideos(ChatDownloaderError):
    """Raised when a channel does not have any videos."""


class ParsingError(ChatDownloaderError):
    """Raised when provider data cannot be parsed."""


class VideoUnavailable(ChatDownloaderError):
    """Raised when a video is unavailable."""


class LoginRequired(ChatDownloaderError):
    """Raised when a video requires login, such as for private content."""


class CaptchaChallengeRequired(LoginRequired):
    """Raised when a CAPTCHA or challenge gate blocks requests."""


class VideoUnplayable(ChatDownloaderError):
    """Raised when a video is unplayable, such as for members-only content."""


class NoChatReplay(ChatDownloaderError):
    """Raised when the video does not contain a chat replay."""


class ChatDisabled(ChatDownloaderError):
    """Raised when the chat is disabled."""


class URLNotProvided(ChatDownloaderError):
    """Raised when no URL is provided."""


class InvalidURL(ChatDownloaderError):
    """Raised when a URL is invalid."""


class ChatGeneratorError(ChatDownloaderError):
    """Raised when no valid generator method can be found for a site."""


class SiteError(ChatDownloaderError):
    """Raised when an error occurs with a specific site."""


class SiteNotSupported(SiteError):
    """Raised when a URL is valid but its site is not supported."""


class NoContinuation(ChatDownloaderError):
    """Raised when no continuation can be found."""


class CookieError(ChatDownloaderError):
    """Raised when an error occurs while loading a cookie file."""


class FormatError(ChatDownloaderError):
    """Raised when a formatting error occurs."""


class FormatNotFound(FormatError):
    """Raised when a specified format cannot be found."""


class FormatFileNotFound(FormatError):
    """Raised when the format file cannot be found."""


__all__ = [
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
