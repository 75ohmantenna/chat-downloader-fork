# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any, cast

import pytest

from chat_downloader.sites.youtube.parsing.message_content_badges import (
    _parse_badges,
    _parse_currency,
    _safe_float,
)
from chat_downloader.sites.youtube.parsing.message_content_text_parser import (
    _get_simple_text,
    _parse_action_button,
    _parse_runs,
    _parse_text,
    _parse_thumbnails,
)
from chat_downloader.sites.youtube.parsing.message_items_content_parser import (
    _get_remapping,
    _parse_item,
)
from chat_downloader.sites.youtube.parsing.message_items_video import (
    _parse_lockup_badge_style,
    _parse_video,
)
from chat_downloader.sites.youtube.parsing.message_links import (
    _get_source_image_url,
    _parse_navigation_endpoint,
    _parse_youtube_link,
)


@pytest.fixture(autouse=True)
def _clear_remapping_cache():
    """Isolate the memoised remapping table across tests that patch it."""
    _get_remapping.cache_clear()
    yield
    _get_remapping.cache_clear()


def test_link_helpers_normalize_supported_youtube_url_forms() -> None:
    assert _get_source_image_url("https://img.example/avatar=s48-c-k") == (
        "https://img.example/avatar"
    )
    assert _get_source_image_url("https://img.example/avatar") == (
        "https://img.example/avatar"
    )

    assert _parse_youtube_link("/redirect?q=https%3A%2F%2Fexample.com") == (
        "https://example.com"
    )
    assert (
        _parse_youtube_link(
            "https://www.youtube.com/redirect?q=https%3A%2F%2Fexample.com%2Fwatch",
        )
        == "https://example.com/watch"
    )
    assert _parse_youtube_link("//cdn.example.com/image.png") == (
        "https://cdn.example.com/image.png"
    )
    assert _parse_youtube_link("/watch?v=abc123") == (
        "https://www.youtube.com/watch?v=abc123"
    )
    assert _parse_youtube_link("https://example.com/plain") == (
        "https://example.com/plain"
    )


def test_parse_navigation_endpoint_returns_default_on_invalid_payload() -> None:
    assert (
        _parse_navigation_endpoint(
            {"commandMetadata": {"webCommandMetadata": {"url": "/watch?v=abc123"}}},
        )
        == "https://www.youtube.com/watch?v=abc123"
    )
    assert _parse_navigation_endpoint({}, default_text="fallback") == "fallback"


def test_text_helpers_parse_simple_text_runs_links_and_emotes() -> None:
    assert _get_simple_text({"simpleText": "hello"}) == "hello"
    assert _parse_text({"simpleText": "hello"}) == "hello"

    parsed = _parse_runs(
        {
            "runs": [
                {"text": "Look "},
                {
                    "text": "here",
                    "navigationEndpoint": {
                        "commandMetadata": {
                            "webCommandMetadata": {"url": "/watch?v=abc123"},
                        },
                    },
                },
                {
                    "emoji": {
                        "emojiId": "smile",
                        "shortcuts": [":)"],
                        "searchTerms": ["smile"],
                        "image": {
                            "thumbnails": [
                                {
                                    "url": "//img.example/smile=s24",
                                    "width": 24,
                                    "height": 24,
                                },
                            ],
                        },
                        "isCustomEmoji": True,
                    },
                },
                {"emoji": {"emojiId": "smile", "shortcuts": [":)"]}},
                {"unknown": True},
            ],
        },
    )

    assert parsed["message"] == (
        "Look https://www.youtube.com/watch?v=abc123:):){'unknown': True}"
    )
    assert parsed["emotes"] == [
        {
            "id": "smile",
            "name": ":)",
            "shortcuts": [":)"],
            "search_terms": ["smile"],
            "images": [
                {"url": "https://img.example/smile", "id": "source"},
                {
                    "url": "https://img.example/smile=s24",
                    "width": 24,
                    "height": 24,
                    "id": "24x24",
                },
            ],
            "is_custom_emoji": True,
        },
    ]

    assert _parse_runs(
        {"runs": [{"text": "plain", "navigationEndpoint": {}}]},
        parse_links=False,
    ) == {"message": "plain"}
    assert _parse_runs("not-a-dict") == {"message": ""}


def test_emoji_without_shortcuts_falls_back_to_colon_wrapped_emoji_id() -> None:
    parsed = _parse_runs({"runs": [{"emoji": {"emojiId": "UC_custom_abc123"}}]})
    assert parsed["message"] == ":UC_custom_abc123:"
    assert parsed["emotes"][0]["name"] == ":UC_custom_abc123:"


def test_thumbnail_and_action_button_helpers_parse_expected_shapes() -> None:
    thumbnails = _parse_thumbnails(
        {
            "thumbnails": [
                {"url": "//img.example/thumb=s24", "width": 24, "height": 24},
                {"url": "//img.example/thumb=s48", "width": 48, "height": 48},
            ],
        },
    )

    assert thumbnails == [
        {"url": "https://img.example/thumb", "id": "source"},
        {
            "url": "https://img.example/thumb=s24",
            "width": 24,
            "height": 24,
            "id": "24x24",
        },
        {
            "url": "https://img.example/thumb=s48",
            "width": 48,
            "height": 48,
            "id": "48x48",
        },
    ]
    assert _parse_thumbnails(
        [
            {
                "thumbnails": [
                    {
                        "url": "//img.example/thumb=s12",
                        "width": 12,
                        "height": 12,
                    },
                ],
            },
        ],
    ) == [
        {"url": "https://img.example/thumb", "id": "source"},
        {
            "url": "https://img.example/thumb=s12",
            "width": 12,
            "height": 12,
            "id": "12x12",
        },
    ]
    assert _parse_thumbnails(cast("Any", "invalid")) == []
    assert _parse_thumbnails([]) == []

    assert _parse_action_button(
        {
            "buttonRenderer": {
                "navigationEndpoint": {
                    "commandMetadata": {"webCommandMetadata": {"url": "/watch?v=xyz"}},
                },
                "text": {"simpleText": "Open"},
            },
        },
    ) == {"url": "https://www.youtube.com/watch?v=xyz", "text": "Open"}
    assert _parse_action_button({}) == {"url": "", "text": ""}


def test_parse_badges_and_currency_cover_icon_and_fallback_paths(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.message_items_content_parser._parse_item",
        lambda _badge: {
            "tooltip": "Moderator",
            "icon": "MODERATOR",
            "badge_icons": [
                {"url": "https://img.example/badge=s16"},
                {"url": "https://img.example/badge=s32"},
                {"url": "https://img.example/badge=no-size"},
            ],
        },
    )

    assert _parse_badges([{"liveChatAuthorBadgeRenderer": {}}]) == [
        {
            "title": "Moderator",
            "icon_name": "moderator",
            "icons": [
                {"url": "https://img.example/badge", "id": "source"},
                {
                    "url": "https://img.example/badge=s16",
                    "width": 16,
                    "height": 16,
                    "id": "16x16",
                },
                {
                    "url": "https://img.example/badge=s32",
                    "width": 32,
                    "height": 32,
                    "id": "32x32",
                },
            ],
        },
    ]

    assert _parse_currency({"simpleText": "$1,234.50"}) == {
        "text": "$1,234.50",
        "amount": 1234.5,
        "currency": "USD",
        "currency_symbol": "$",
    }
    assert _parse_currency({"simpleText": "CHF12.30"}) == {
        "text": "CHF12.30",
        "amount": 12.3,
        "currency": "CHF",
        "currency_symbol": "CHF",
    }
    assert _parse_currency({"simpleText": "12.30"}) == {
        "text": "12.30",
        "amount": 12.3,
        "currency": "",
        "currency_symbol": "",
    }


def test_parse_badges_ignores_malformed_icons_and_missing_title(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.message_items_content_parser._parse_item",
        lambda _badge: {
            "icon": "SPONSOR",
            "badge_icons": [
                {},
                {"url": ""},
            ],
        },
    )

    assert _parse_badges([{"liveChatAuthorBadgeRenderer": {}}]) == [
        {
            "icon_name": "sponsor",
            "icons": [],
        },
    ]


def test_parse_currency_uses_numeric_fallback_when_split_fails(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.message_content_badges.re.split",
        lambda _pattern, _text: ["not-parseable"],
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.message_content_badges.re.sub",
        lambda _pattern, _repl, _text: "42.5",
    )

    assert _parse_currency({"simpleText": "unstructured"}) == {
        "text": "unstructured",
        "amount": 42.5,
        "currency": None,
        "currency_symbol": None,
    }


def test_parse_item_returns_existing_info_for_empty_renderer() -> None:
    info = {"kept": True}
    assert _parse_item({"liveChatTextMessageRenderer": {}}, info=info) == {"kept": True}


def test_parse_item_recurses_moves_author_and_applies_offset_once(
    monkeypatch,
) -> None:

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message.build_remapping",
        lambda: {
            "authorImages": "author_images",
            "timeText": "time_text",
        },
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message._COLOUR_KEYS",
        ["bodyBackgroundColor"],
    )

    result = _parse_item(
        {
            "outerRenderer": {
                "bodyBackgroundColor": 0xFF112233,
                "showItemEndpoint": {
                    "showLiveChatItemEndpoint": {
                        "renderer": {
                            "nestedRenderer": {"authorImages": {"thumb": "img"}},
                        },
                    },
                },
                "header": {"headerRenderer": {"timeText": "1:02"}},
            },
        },
        offset=2,
    )

    assert result["body_background_colour"] == "#112233ff"
    assert result["author"] == {"images": {"thumb": "img"}, "name": ""}
    assert result["time_in_seconds"] == 60
    assert result["time_text"] == "1:00"
    assert result["message"] is None


def test_apply_author_roles_marks_badged_author_as_sponsor() -> None:
    from chat_downloader.sites.youtube.parsing import (
        message_items_content_parser as _mcp,
    )

    author = {"badges": [{"icon_name": "custom", "icons": [{"url": "badge"}]}]}
    _mcp._apply_author_roles(author)

    assert author["is_sponsor"] is True


def test_apply_author_roles_ignores_unknown_badge_without_icons() -> None:
    from chat_downloader.sites.youtube.parsing import (
        message_items_content_parser as _mcp,
    )

    author = {"badges": [{"icon_name": "custom"}]}
    _mcp._apply_author_roles(author)

    assert author == {"badges": [{"icon_name": "custom"}]}


def test_modern_element_adapter_handles_malformed_optional_fields() -> None:
    from chat_downloader.sites.youtube.parsing import (
        message_items_content_parser as _mcp,
    )

    assert (
        _mcp._modern_author_badges(
            {"authorName": {"attachmentRuns": [None]}},
        )
        == []
    )
    assert _mcp._modern_timestamp_usec({}) == ""
    assert (
        _mcp._modern_timestamp_usec(
            {
                "newElement": {
                    "properties": {
                        "identifierProperties": {
                            "uniqueLoggingIdentifier": "x" * 19,
                        },
                    },
                },
            },
        )
        == ""
    )

    item = {
        "elementRenderer": {
            "newElement": {
                "type": {
                    "componentType": {
                        "model": {"liveChatTextMessageModel": {}},
                    },
                },
            },
        },
    }
    assert _mcp._normalize_modern_element_item(item) is item


def test_parse_item_merges_header_when_show_item_endpoint_has_no_renderer(
    monkeypatch,
) -> None:

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message.build_remapping",
        lambda: {"timeText": "time_text"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message._COLOUR_KEYS",
        [],
    )

    result = _parse_item(
        {
            "outerRenderer": {
                "showItemEndpoint": {"showLiveChatItemEndpoint": {}},
                "header": {"headerRenderer": {"timeText": "0:09"}},
            },
        },
    )

    assert result["time_in_seconds"] == 9
    assert result["message"] is None


def test_parse_item_generates_time_text_from_time_in_seconds(
    monkeypatch,
) -> None:

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message.build_remapping",
        dict,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message._COLOUR_KEYS",
        [],
    )

    result = _parse_item(
        {"outerRenderer": {"unused": True}},
        info={"time_in_seconds": 5},
    )

    assert result["time_in_seconds"] == 5
    assert result["time_text"] == "0:05"
    assert result["message"] is None


def test_parse_item_preserves_authoritative_wrapper_timing_pair(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message.build_remapping",
        lambda: {"timeText": "time_text"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message._COLOUR_KEYS",
        [],
    )

    result = _parse_item(
        {
            "outerRenderer": {
                "showItemEndpoint": {
                    "showLiveChatItemEndpoint": {
                        "renderer": {
                            "nestedRenderer": {"timeText": "0:09"},
                        },
                    },
                },
            },
        },
        info={"time_in_seconds": 5.25},
        preserve_wrapper_time=True,
    )

    assert result["time_in_seconds"] == 5.25
    assert result["time_text"] == "0:05"


def test_parse_item_does_not_treat_empty_nested_shell_as_timing_merge(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message.build_remapping",
        dict,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message._COLOUR_KEYS",
        [],
    )

    result = _parse_item(
        {
            "outerRenderer": {
                "showItemEndpoint": {
                    "showLiveChatItemEndpoint": {
                        "renderer": {"nestedRenderer": {}},
                    },
                },
            },
        },
        info={"time_in_seconds": 5.25, "time_text": "0:02"},
        preserve_wrapper_time=True,
    )

    assert result["time_in_seconds"] == 5.25
    assert result["time_text"] == "0:02"


def test_parse_item_preserves_authoritative_zero_wrapper_time(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message.build_remapping",
        lambda: {"timeText": "time_text"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.constants_message._COLOUR_KEYS",
        [],
    )

    result = _parse_item(
        {
            "outerRenderer": {
                "showItemEndpoint": {
                    "showLiveChatItemEndpoint": {
                        "renderer": {
                            "nestedRenderer": {"timeText": "0:09"},
                        },
                    },
                },
            },
        },
        info={"time_in_seconds": 0},
        preserve_wrapper_time=True,
    )

    assert result["time_in_seconds"] == 0
    assert result["time_text"] == "0:00"


def test_parse_video_uses_overlay_style_or_default() -> None:
    live_video = _parse_video(
        {
            "videoId": "abc123",
            "title": {"runs": [{"text": "Example"}]},
            "viewCountText": {"simpleText": "1 watching"},
            "shortViewCountText": {"simpleText": "1"},
            "thumbnailOverlays": [
                {"thumbnailOverlayTimeStatusRenderer": {"style": "LIVE"}},
            ],
        },
    )
    default_video = _parse_video(
        {
            "videoId": "def456",
            "title": {"runs": [{"text": "Other"}]},
            "viewCountText": {"simpleText": "2 views"},
            "shortViewCountText": {"simpleText": "2"},
        },
    )

    assert live_video == {
        "video_id": "abc123",
        "title": "Example",
        "video_type": "LIVE",
        "view_count": "1 watching",
        "short_view_count": "1",
    }
    assert default_video["video_type"] == "DEFAULT"


def test_parse_video_accepts_lockup_view_model() -> None:
    video = _parse_video(
        {
            "lockupViewModel": {
                "contentId": "live123",
                "contentImage": {
                    "thumbnailViewModel": {
                        "overlays": [
                            {
                                "thumbnailBottomOverlayViewModel": {
                                    "badges": [
                                        {
                                            "thumbnailBadgeViewModel": {
                                                "text": "LIVE",
                                            },
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                },
                "metadata": {
                    "lockupMetadataViewModel": {
                        "title": {"content": "Live stream"},
                        "metadata": {
                            "contentMetadataViewModel": {
                                "metadataRows": [
                                    {
                                        "metadataParts": [
                                            {
                                                "text": {
                                                    "content": "1 watching",
                                                },
                                            },
                                        ],
                                    },
                                ],
                            },
                        },
                    },
                },
            },
        },
    )

    assert video == {
        "video_id": "live123",
        "title": "Live stream",
        "video_type": "LIVE",
        "view_count": "1 watching",
        "short_view_count": "1 watching",
    }


def test_parse_video_accepts_lockup_without_view_count_or_badge() -> None:
    video = _parse_video(
        {
            "lockupViewModel": {
                "contentId": "plain123",
                "metadata": {
                    "lockupMetadataViewModel": {
                        "title": {"content": "Plain upload"},
                    },
                },
            },
        },
    )

    assert video["video_id"] == "plain123"
    assert video["title"] == "Plain upload"
    assert video["video_type"] == "DEFAULT"
    assert "view_count" not in video


def test_parse_video_uses_later_overlay_when_first_style_is_empty() -> None:
    video = _parse_video(
        {
            "videoId": "abc123",
            "title": {"runs": [{"text": "Example"}]},
            "thumbnailOverlays": [
                {"thumbnailOverlayTimeStatusRenderer": {"style": ""}},
                {"thumbnailOverlayTimeStatusRenderer": {"style": "UPCOMING"}},
            ],
        },
    )

    assert video["video_type"] == "UPCOMING"


def _make_lockup_with_badge(text: str) -> dict[str, Any]:
    return {
        "contentImage": {
            "thumbnailViewModel": {
                "overlays": [
                    {
                        "thumbnailBottomOverlayViewModel": {
                            "badges": [
                                {"thumbnailBadgeViewModel": {"text": text}},
                            ],
                        },
                    },
                ],
            },
        },
    }


def test_parse_lockup_badge_style_returns_upcoming_for_premiere() -> None:
    assert _parse_lockup_badge_style(_make_lockup_with_badge("PREMIERE")) == "UPCOMING"


def test_parse_lockup_badge_style_returns_upcoming_for_upcoming() -> None:
    assert _parse_lockup_badge_style(_make_lockup_with_badge("UPCOMING")) == "UPCOMING"


def test_parse_lockup_badge_style_returns_none_for_unknown_badge() -> None:
    assert _parse_lockup_badge_style(_make_lockup_with_badge("SHORTS")) is None


def test_parse_lockup_badge_style_returns_none_when_no_overlays() -> None:
    assert _parse_lockup_badge_style({}) is None


def test_safe_float_returns_none_for_non_numeric_text() -> None:
    assert _safe_float("Free") is None
    assert _safe_float("N/A") is None


def test_safe_float_returns_float_for_numeric_text() -> None:
    assert _safe_float("1.99") == 1.99
    assert _safe_float("1234") == 1234.0
