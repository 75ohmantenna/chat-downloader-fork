# SPDX-License-Identifier: MIT

from typing import Any, cast

from chat_downloader.sites.youtube.parsing.message_content_badges import (
    _parse_badges,
    _parse_currency,
)
from chat_downloader.sites.youtube.parsing.message_content_text_parser import (
    _get_simple_text,
    _parse_action_button,
    _parse_runs,
    _parse_text,
    _parse_thumbnails,
)
from chat_downloader.sites.youtube.parsing.message_items_content_parser import (
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
            {
                "commandMetadata": {
                    "webCommandMetadata": {"url": "/watch?v=abc123"}
                }
            },
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
        False,
    ) == {"message": "plain"}
    assert _parse_runs("not-a-dict") == {"message": ""}


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
                    "commandMetadata": {
                        "webCommandMetadata": {"url": "/watch?v=xyz"}
                    },
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
    assert _parse_item({"liveChatTextMessageRenderer": {}}, info=info) == {
        "kept": True
    }


def test_parse_item_recurses_moves_author_and_normalizes_time(
    monkeypatch,
) -> None:
    import chat_downloader.sites.youtube.parsing.message_items_content_parser as _mcp

    monkeypatch.setattr(_mcp, "_REMAPPING", None)
    monkeypatch.setattr(_mcp, "_COLOUR_KEYS", None)
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
                            "nestedRenderer": {
                                "authorImages": {"thumb": "img"}
                            },
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
    assert result["time_in_seconds"] == 58
    assert result["time_text"] == "0:58"
    assert result["message"] is None


def test_parse_item_generates_time_text_from_time_in_seconds(
    monkeypatch,
) -> None:
    import chat_downloader.sites.youtube.parsing.message_items_content_parser as _mcp

    monkeypatch.setattr(_mcp, "_REMAPPING", None)
    monkeypatch.setattr(_mcp, "_COLOUR_KEYS", None)
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
    assert (
        _parse_lockup_badge_style(_make_lockup_with_badge("PREMIERE"))
        == "UPCOMING"
    )


def test_parse_lockup_badge_style_returns_upcoming_for_upcoming() -> None:
    assert (
        _parse_lockup_badge_style(_make_lockup_with_badge("UPCOMING"))
        == "UPCOMING"
    )


def test_parse_lockup_badge_style_returns_none_for_unknown_badge() -> None:
    assert _parse_lockup_badge_style(_make_lockup_with_badge("SHORTS")) is None


def test_parse_lockup_badge_style_returns_none_when_no_overlays() -> None:
    assert _parse_lockup_badge_style({}) is None
