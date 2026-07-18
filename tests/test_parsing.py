# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.sites.youtube.parsing import (
    _get_simple_text,
    _get_source_image_url,
    _parse_navigation_endpoint,
    _parse_text,
    _parse_youtube_link,
)
from chat_downloader.utils.color_utils import argb_int_to_rgba, rgba_to_hex

# ---------------------------------------------------------------------------
# YouTube parsing
# ---------------------------------------------------------------------------


def test_get_source_image_url_strips_query_params() -> None:
    assert _get_source_image_url("https://yt3.ggpht.com/image=s32-c-k") == (
        "https://yt3.ggpht.com/image"
    )


def test_get_source_image_url_no_query_params() -> None:
    assert _get_source_image_url("https://yt3.ggpht.com/image") == (
        "https://yt3.ggpht.com/image"
    )


def test_parse_youtube_link_redirect_with_q_param() -> None:
    assert _parse_youtube_link("/redirect?q=https://example.com&redir_token=xyz") == (
        "https://example.com"
    )


def test_parse_youtube_link_full_redirect_url() -> None:
    assert _parse_youtube_link(
        "https://www.youtube.com/redirect?q=https://test.com"
    ) == ("https://test.com")


def test_parse_youtube_link_protocol_relative() -> None:
    assert _parse_youtube_link("//example.com/path") == "https://example.com/path"


def test_parse_youtube_link_internal() -> None:
    result = _parse_youtube_link("/watch?v=abc123")
    assert result.startswith("https://")
    assert "youtube.com" in result
    assert "/watch?v=abc123" in result


def test_parse_youtube_link_normal() -> None:
    assert _parse_youtube_link("https://example.com/page") == "https://example.com/page"


def test_parse_navigation_endpoint_valid() -> None:
    endpoint = {
        "commandMetadata": {"webCommandMetadata": {"url": "/watch?v=test123"}},
    }
    assert "test123" in _parse_navigation_endpoint(endpoint)


def test_parse_navigation_endpoint_missing_returns_default() -> None:
    assert _parse_navigation_endpoint({}, default_text="default") == "default"


def test_get_simple_text_present() -> None:
    assert _get_simple_text({"simpleText": "Hello World"}) == "Hello World"


def test_get_simple_text_absent() -> None:
    assert _get_simple_text({"otherField": "value"}) is None


def test_parse_text_with_simple_text() -> None:
    assert _parse_text({"simpleText": "Simple message"}) == "Simple message"


def test_argb_to_hex_returns_correct_format() -> None:
    hex_color = rgba_to_hex(argb_int_to_rgba(4278248447))
    assert isinstance(hex_color, str)
    assert hex_color.startswith("#")
    assert len(hex_color) == 9  # #RRGGBBAA


@pytest.mark.parametrize(
    ("argb", "expected_hex"),
    [
        (0xFFFF0000, "#ff0000ff"),  # Red
        (0xFF00FF00, "#00ff00ff"),  # Green
        (0xFF0000FF, "#0000ffff"),  # Blue
        (0xFFFFFFFF, "#ffffffff"),  # White
        (0xFF000000, "#000000ff"),  # Black
    ],
)
def test_color_conversion(argb: int, expected_hex: str) -> None:
    assert rgba_to_hex(argb_int_to_rgba(argb)) == expected_hex


# ---------------------------------------------------------------------------
# Cross-cutting message parsing
# ---------------------------------------------------------------------------


def test_parse_youtube_link_returns_unchanged_external_link() -> None:
    url = "https://example.com"
    assert _parse_youtube_link(url) == url


def test_parse_navigation_endpoint_nested_structure() -> None:
    complex_data = {
        "navigationEndpoint": {
            "commandMetadata": {"webCommandMetadata": {"url": "/test"}},
        },
    }
    assert (
        _parse_navigation_endpoint(complex_data.get("navigationEndpoint", {}))
        is not None
    )
