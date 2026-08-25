# SPDX-License-Identifier: MIT

from __future__ import annotations

import re

import pytest

from chat_downloader.sites import YouTubeChatDownloader, get_all_sites
from chat_downloader.sites.base import BaseChatDownloader
from chat_downloader.sites.kick.extractor import KickChatDownloader
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader


def test_base_matches_method_exists() -> None:
    """Test that matches method exists in BaseChatDownloader."""
    assert hasattr(BaseChatDownloader, "matches")
    assert callable(BaseChatDownloader.matches)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=jfKfPfyJRdk",
        "https://youtube.com/watch?v=jfKfPfyJRdk",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/jfKfPfyJRdk",
        "https://www.youtube.com/v/jfKfPfyJRdk",
        "https://www.youtube.com/embed/jfKfPfyJRdk",
    ],
)
def test_youtube_video_url_matching(url: str) -> None:
    """Test YouTube video URL matching."""
    result = YouTubeChatDownloader.matches(url)
    assert result is not None, f"Failed to match URL: {url}"
    function_name, match = result
    assert function_name == "_get_chat_by_video_id"
    assert isinstance(match, re.Match)


@pytest.mark.parametrize(
    ("url", "expected_id"),
    [
        ("https://www.youtube.com/watch?v=jfKfPfyJRdk", "jfKfPfyJRdk"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=abc_-123456", "abc_-123456"),
    ],
)
def test_youtube_video_id_extraction(url: str, expected_id: str) -> None:
    """Test YouTube video ID extraction from URLs."""
    result = YouTubeChatDownloader.matches(url)
    assert result is not None
    _function_name, match = result
    video_id = match.group("id")
    assert video_id == expected_id


@pytest.mark.parametrize(
    ("url", "expected_id", "expected_type"),
    [
        (
            "https://www.youtube.com/channel/UCR3TOnFWDeAlT-Ho6LueDmg/live",
            "UCR3TOnFWDeAlT-Ho6LueDmg",
            "channel/",
        ),
        ("https://www.youtube.com/@example/live", "example", "@"),
        ("https://www.youtube.com/user/example/live", "example", "user/"),
    ],
)
def test_youtube_live_channel_url_matching(
    url: str,
    expected_id: str,
    expected_type: str,
) -> None:
    result = YouTubeChatDownloader.matches(url)

    assert result is not None
    function_name, match = result
    assert function_name == "_get_chat_by_live_user"
    assert match.group("id") == expected_id
    assert match.group("type") == expected_type
    assert match.end() == len(url)


def test_youtube_live_channel_url_accepts_query_suffix() -> None:
    url = "https://www.youtube.com/@example/live?app=desktop"

    result = YouTubeChatDownloader.matches(url)

    assert result is not None
    function_name, match = result
    assert function_name == "_get_chat_by_live_user"
    assert match.group("id") == "example"


@pytest.mark.parametrize("suffix", ["/lives", "/live/extra"])
def test_youtube_live_channel_url_rejects_lookalike_suffix(suffix: str) -> None:
    url = f"https://www.youtube.com/@example{suffix}"

    assert YouTubeChatDownloader.matches(url) is None


def test_youtube_without_protocol() -> None:
    """Test YouTube URL matching without protocol."""
    url = "www.youtube.com/watch?v=jfKfPfyJRdk"
    result = YouTubeChatDownloader.matches(url)
    # URL matching expects a scheme; ChatDownloader.get_chat() handles
    # missing schemes by retrying with https://.
    assert result is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.twitch.tv/videos/1234567890",
        "https://twitch.tv/videos/9876543210",
    ],
)
def test_twitch_url_matching(url: str) -> None:
    """Test Twitch URL matching."""
    result = TwitchChatDownloader.matches(url)
    assert result is not None, f"Failed to match URL: {url}"
    _function_name, match = result
    assert isinstance(match, re.Match)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "https://not-a-valid-site.com/video/123",
        "invalid url",
        "",
    ],
)
def test_invalid_url_no_match(url: str) -> None:
    """Test that invalid URLs don't match."""
    result = YouTubeChatDownloader.matches(url)
    assert result is None, f"Should not match invalid URL: {url}"


def test_matches_returns_correct_format() -> None:
    """Test that matches returns correct format."""
    url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    result = YouTubeChatDownloader.matches(url)

    assert result is not None
    assert isinstance(result, tuple)
    assert len(result) == 2

    function_name, match = result
    assert isinstance(function_name, str)
    assert isinstance(match, re.Match)


def test_matches_with_multiple_params() -> None:
    """Test URL matching with multiple query parameters."""
    url = "https://www.youtube.com/watch?foo=bar&v=jfKfPfyJRdk&baz=qux"
    result = YouTubeChatDownloader.matches(url)

    assert result is not None
    _function_name, match = result
    video_id = match.group("id")
    assert video_id == "jfKfPfyJRdk"


def test_valid_urls_dict_structure() -> None:
    """Test that _VALID_URLS dict has correct structure."""
    sites = [YouTubeChatDownloader, TwitchChatDownloader]

    for site in sites:
        assert hasattr(site, "_VALID_URLS")
        assert isinstance(site._VALID_URLS, dict)

        for function_name, regex in site._VALID_URLS.items():
            assert isinstance(function_name, str)
            assert function_name.startswith("_get_chat_by")
            assert isinstance(regex, str)


def test_get_all_sites() -> None:
    """Test that get_all_sites returns site classes."""
    sites = get_all_sites()
    assert sites == [
        TwitchChatDownloader,
        YouTubeChatDownloader,
        KickChatDownloader,
    ]

    for site in sites:
        assert hasattr(site, "matches")
        assert hasattr(site, "_VALID_URLS")


def test_get_all_sites_include_parent() -> None:
    """Test that get_all_sites can include the base downloader."""
    assert get_all_sites(include_parent=True) == [
        BaseChatDownloader,
        TwitchChatDownloader,
        YouTubeChatDownloader,
        KickChatDownloader,
    ]


def test_multiple_url_patterns_per_site() -> None:
    """Test sites with multiple URL patterns."""
    # YouTube has multiple patterns (video, clip, channel, etc.)
    assert len(YouTubeChatDownloader._VALID_URLS) > 1


def test_matches_first_pattern_only() -> None:
    """Test that matches returns first matching pattern."""
    # If a URL matches multiple patterns, only first should be returned
    url = "https://www.youtube.com/watch?v=jfKfPfyJRdk"
    result = YouTubeChatDownloader.matches(url)

    assert result is not None
    function_name, _match = result
    # Should get exactly one match (not multiple)
    assert isinstance(function_name, str)


@pytest.mark.parametrize(
    "url",
    [
        "https://www.YouTube.com/watch?v=jfKfPfyJRdk",
        "https://www.youtube.com/watch?v=jfKfPfyJRdk",
        "https://www.YOUTUBE.com/watch?v=jfKfPfyJRdk",
    ],
)
def test_case_insensitivity_where_appropriate(url: str) -> None:
    """Test case insensitivity in URL matching."""
    result = YouTubeChatDownloader.matches(url)
    assert result is not None


@pytest.mark.parametrize("protocol", ["https://", "http://", "//"])
def test_protocol_variations(protocol: str) -> None:
    """Test different protocol variations."""
    url = protocol + "www.youtube.com/watch?v=jfKfPfyJRdk"
    result = YouTubeChatDownloader.matches(url)
    assert result is not None, f"Failed with protocol: {protocol}"


@pytest.mark.parametrize(
    ("site", "url"),
    [
        (
            TwitchChatDownloader,
            "https://evil.example/?next=https://www.twitch.tv/realchannel",
        ),
        (KickChatDownloader, "prefix https://kick.com/xqc"),
        (YouTubeChatDownloader, "ABCDEFGHIJK-extra"),
        (TwitchChatDownloader, "https://www.twitch.tv/videos"),
        (YouTubeChatDownloader, "https://www.youtube.com/watch"),
        (YouTubeChatDownloader, "https://www.youtube.com/playlist"),
    ],
)
def test_url_matching_rejects_embedded_trailing_and_reserved_routes(
    site: type[BaseChatDownloader],
    url: str,
) -> None:
    assert site.matches(url) is None


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=jfKfPfyJRdk&list=example",
        "https://www.twitch.tv/realchannel?referrer=raid",
        "https://kick.com/xqc?clip=example",
    ],
)
def test_url_matching_accepts_complete_urls_with_query_suffixes(url: str) -> None:
    assert any(site.matches(url) is not None for site in get_all_sites())
