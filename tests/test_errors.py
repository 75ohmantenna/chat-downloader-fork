# SPDX-License-Identifier: MIT

from typing import NoReturn

import pytest

from chat_downloader.errors import (
    ChatDisabled,
    ChatDownloaderError,
    ChatGeneratorError,
    CookieError,
    FormatError,
    FormatFileNotFound,
    FormatNotFound,
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
    UnexpectedError,
    URLNotProvided,
    UserNotFound,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
)


def test_base_error_inheritance() -> None:
    """Test that all errors inherit from ChatDownloaderError."""
    error_classes = [
        UnexpectedError,
        InvalidParameter,
        RetriesExceeded,
        VideoNotFound,
        UserNotFound,
        NoVideos,
        ParsingError,
        VideoUnavailable,
        LoginRequired,
        VideoUnplayable,
        NoChatReplay,
        ChatDisabled,
        URLNotProvided,
        InvalidURL,
        ChatGeneratorError,
        SiteError,
        SiteNotSupported,
        NoContinuation,
        CookieError,
        FormatError,
        FormatNotFound,
        FormatFileNotFound,
    ]

    for error_class in error_classes:
        assert issubclass(error_class, ChatDownloaderError)


def test_chat_downloader_error() -> None:
    """Test base ChatDownloaderError."""
    error = ChatDownloaderError("Test error")
    assert isinstance(error, Exception)
    assert str(error) == "Test error"


def test_unexpected_error() -> None:
    """Test UnexpectedError with various inputs."""
    error = UnexpectedError("Unexpected issue")
    assert str(error) == "Unexpected issue"

    error = UnexpectedError({"key": "value"})
    assert "key" in str(error)


def test_invalid_parameter() -> None:
    """Test InvalidParameter error."""
    error = InvalidParameter("Invalid param value")
    assert isinstance(error, ChatDownloaderError)


def test_retries_exceeded() -> None:
    """Test RetriesExceeded error."""
    error = RetriesExceeded("Max retries reached")
    assert isinstance(error, ChatDownloaderError)


def test_video_not_found() -> None:
    """Test VideoNotFound error."""
    error = VideoNotFound("Video ID not found")
    assert isinstance(error, ChatDownloaderError)


def test_user_not_found() -> None:
    """Test UserNotFound error."""
    error = UserNotFound("User does not exist")
    assert isinstance(error, ChatDownloaderError)


def test_no_videos() -> None:
    """Test NoVideos error."""
    error = NoVideos("Channel has no videos")
    assert isinstance(error, ChatDownloaderError)


def test_parsing_error() -> None:
    """Test ParsingError."""
    error = ParsingError("Failed to parse response")
    assert isinstance(error, ChatDownloaderError)


def test_video_unavailable() -> None:
    """Test VideoUnavailable error."""
    error = VideoUnavailable("Video is not available")
    assert isinstance(error, ChatDownloaderError)


def test_login_required() -> None:
    """Test LoginRequired error."""
    error = LoginRequired("Must be logged in")
    assert isinstance(error, ChatDownloaderError)


def test_video_unplayable() -> None:
    """Test VideoUnplayable error."""
    error = VideoUnplayable("Members-only content")
    assert isinstance(error, ChatDownloaderError)


def test_no_chat_replay() -> None:
    """Test NoChatReplay error."""
    error = NoChatReplay("No chat replay available")
    assert isinstance(error, ChatDownloaderError)


def test_chat_disabled() -> None:
    """Test ChatDisabled error."""
    error = ChatDisabled("Chat is disabled")
    assert isinstance(error, ChatDownloaderError)


def test_url_not_provided() -> None:
    """Test URLNotProvided error."""
    error = URLNotProvided("URL is required")
    assert isinstance(error, ChatDownloaderError)


def test_invalid_url() -> None:
    """Test InvalidURL error."""
    error = InvalidURL("URL format is invalid")
    assert isinstance(error, ChatDownloaderError)


def test_chat_generator_error() -> None:
    """Test ChatGeneratorError."""
    error = ChatGeneratorError("No valid generator found")
    assert isinstance(error, ChatDownloaderError)


def test_site_error() -> None:
    """Test SiteError base class."""
    error = SiteError("Site-specific error")
    assert isinstance(error, ChatDownloaderError)


def test_site_not_supported() -> None:
    """Test SiteNotSupported error."""
    error = SiteNotSupported("Site not supported")
    assert isinstance(error, SiteError)
    assert isinstance(error, ChatDownloaderError)


def test_no_continuation() -> None:
    """Test NoContinuation error."""
    error = NoContinuation("No continuation token")
    assert isinstance(error, ChatDownloaderError)


def test_cookie_error() -> None:
    """Test CookieError."""
    error = CookieError("Failed to load cookies")
    assert isinstance(error, ChatDownloaderError)


def test_format_error() -> None:
    """Test FormatError base class."""
    error = FormatError("Format error occurred")
    assert isinstance(error, ChatDownloaderError)


def test_format_not_found() -> None:
    """Test FormatNotFound error."""
    error = FormatNotFound("Format not found")
    assert isinstance(error, FormatError)
    assert isinstance(error, ChatDownloaderError)


def test_format_file_not_found() -> None:
    """Test FormatFileNotFound error."""
    error = FormatFileNotFound("Format file not found")
    assert isinstance(error, FormatError)
    assert isinstance(error, ChatDownloaderError)


def test_error_catching() -> NoReturn:
    """Test that errors can be caught properly."""
    # Catch specific error
    with pytest.raises(InvalidParameter):
        msg = "test"
        raise InvalidParameter(msg)

    # Catch via base class
    with pytest.raises(ChatDownloaderError):
        msg = "test"
        raise VideoNotFound(msg)

    # Catch SiteNotSupported via SiteError
    with pytest.raises(SiteError):
        msg = "test"
        raise SiteNotSupported(msg)

    # Catch FormatNotFound via FormatError
    with pytest.raises(FormatError):
        msg = "test"
        raise FormatNotFound(msg)


def test_error_message_preservation() -> None:
    """Test that error messages are preserved."""
    test_message = "This is a test error message"
    errors = [
        InvalidParameter(test_message),
        VideoNotFound(test_message),
        NoChatReplay(test_message),
        FormatNotFound(test_message),
    ]

    for error in errors:
        assert str(error) == test_message
