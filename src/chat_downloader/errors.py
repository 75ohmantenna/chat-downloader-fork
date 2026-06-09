# SPDX-License-Identifier: MIT

"""File for defining errors."""


class ChatDownloaderError(Exception):
    """Base class for Chat Downloader errors."""


class InvalidParameter(ChatDownloaderError):
    """Raised if an invalid parameter is specified."""


class RetriesExceeded(ChatDownloaderError):
    """Raised after the maximum number of retries has been reached."""


class IncompleteContinuationError(RetriesExceeded):
    """Raised when continuation payload shape stays incomplete after retries."""


class VideoNotFound(ChatDownloaderError):
    """Raised when video cannot be found."""


class UserNotFound(ChatDownloaderError):
    """Raised when user cannot be found."""


class NoVideos(ChatDownloaderError):
    """Raised when a channel does not have any videos."""


class ParsingError(ChatDownloaderError):
    """Raised when video data cannot be parsed."""


class VideoUnavailable(ChatDownloaderError):
    """Raised when video is unavailable."""


class LoginRequired(ChatDownloaderError):
    """Raised when video is login is required (e.g. if video is private)."""


class CaptchaChallengeRequired(LoginRequired):
    """Raised when a captcha/challenge gate blocks requests."""


class VideoUnplayable(ChatDownloaderError):
    """Raised when video is unplayable (e.g. if video is members-only)."""


class NoChatReplay(ChatDownloaderError):
    """Raised when the video does not contain a chat replay."""


class ChatDisabled(ChatDownloaderError):
    """Raised when the chat is disabled."""


class URLNotProvided(ChatDownloaderError):
    """Raised when no url is provided."""


class InvalidURL(ChatDownloaderError):
    """Raised when the url is invalid."""


class ChatGeneratorError(ChatDownloaderError):
    """Raised when no valid generator method for a site can be found."""


class SiteError(ChatDownloaderError):
    """Raised when an error occurs with a specific site."""


class SiteNotSupported(SiteError):
    """Raised when the url is valid, but the site is not supported."""


class NoContinuation(ChatDownloaderError):
    """Raised when no continuation can be found."""


class CookieError(ChatDownloaderError):
    """Raised when an error occurs while loading a cookie file."""


class FormatError(ChatDownloaderError):
    """Raised when a formatting error occurs."""


class FormatNotFound(FormatError):
    """Raised when a specified format can not be found."""


class FormatFileNotFound(FormatError):
    """Raised when the format file can not be found."""


__all__ = [
    "ChatDisabled",
    "ChatDownloaderError",
    "ChatGeneratorError",
    "CaptchaChallengeRequired",
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
