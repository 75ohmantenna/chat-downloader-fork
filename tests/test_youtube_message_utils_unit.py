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
    _apply_author_roles,
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


@pytest.fixture
def item_parser(monkeypatch):
    def configure(remapping, colours=()):
        monkeypatch.setattr(
            "chat_downloader.sites.youtube.constants_message.build_remapping",
            lambda: remapping,
        )
        monkeypatch.setattr(
            "chat_downloader.sites.youtube.constants_message._COLOUR_KEYS",
            colours,
        )
        return _parse_item

    return configure


def _nested_item(renderer):
    return {"showItemEndpoint": {"showLiveChatItemEndpoint": {"renderer": renderer}}}


def _wrap(path, value):
    for key in reversed(path.split(".")):
        value = {key: value}
    return value


@pytest.mark.parametrize("suffix", ["=s48-c-k", ""])
def test_source_image_url(suffix):
    assert _get_source_image_url(f"https://img.example/avatar{suffix}") == (
        "https://img.example/avatar"
    )


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("/redirect?q=https%3A%2F%2Fexample.com", "https://example.com"),
        (
            "https://www.youtube.com/redirect?q=https%3A%2F%2Fexample.com%2Fwatch",
            "https://example.com/watch",
        ),
        ("//cdn.example.com/image.png", "https://cdn.example.com/image.png"),
        ("/watch?v=abc123", "https://www.youtube.com/watch?v=abc123"),
        ("https://example.com/plain", "https://example.com/plain"),
    ],
)
def test_youtube_links(url, expected):
    assert _parse_youtube_link(url) == expected


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

    emoji = {
        "emojiId": "smile",
        "shortcuts": [":)"],
        "searchTerms": ["smile"],
        "image": {"thumbnails": [_image("//img.example/smile", 24)]},
        "isCustomEmoji": True,
    }
    parsed = _parse_runs(
        {
            "runs": [
                {"text": "Look "},
                {
                    "text": "here",
                    "navigationEndpoint": _wrap(
                        "commandMetadata.webCommandMetadata.url", "/watch?v=abc123"
                    ),
                },
                {"emoji": emoji},
                {"emoji": {"emojiId": "smile", "shortcuts": [":)"]}},
                {"unknown": True},
            ]
        }
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
            "images": _images("https://img.example/smile", 24),
            "is_custom_emoji": True,
        },
    ]

    assert _parse_runs(
        {"runs": [{"text": "plain", "navigationEndpoint": {}}]},
        parse_links=False,
    ) == {"message": "plain"}
    assert _parse_runs("not-a-dict") == {"message": ""}


@pytest.mark.parametrize(
    ("emoji", "name"),
    [
        ({"emojiId": "UC_custom_abc123"}, ":UC_custom_abc123:"),
        ({"emojiId": "emoji-1", "shortcuts": [123]}, ":emoji-1:"),
        ({}, ":emoji:"),
        (None, ":emoji:"),
    ],
)
def test_emoji_fallback_names(emoji, name):
    parsed = _parse_runs({"runs": [{"emoji": emoji}]})
    assert parsed["message"] == name
    if emoji:
        assert parsed["emotes"][0]["name"] == name
    else:
        assert parsed == {"message": name}


def test_parse_runs_ignores_invalid_containers_and_entries() -> None:
    assert _parse_runs({"runs": 123}) == {"message": ""}
    assert _parse_runs({"runs": [None, 123, "text", {"text": "valid"}]}) == {
        "message": "valid"
    }
    assert _parse_runs({"content": 123}) == {"message": ""}


def _image(url, size, **extra):
    return {"url": f"{url}=s{size}", "width": size, "height": size, **extra}


def _images(url, *sizes):
    return [{"url": url, "id": "source"}] + [
        _image(url, size, id=f"{size}x{size}") for size in sizes
    ]


@pytest.mark.parametrize("sizes", [(24, 48), (12,), (24,)])
def test_thumbnail_containers(sizes):
    entries = [_image("//img.example/thumb", n) for n in sizes]
    if sizes == (24,):
        entries[0]["newField"] = "ignored"
        entries += [
            {"width": 48, "height": 48},
            {"url": None},
            {"url": 123},
            {"url": "   "},
            "not-an-object",
        ]
    payload = {"thumbnails": entries}
    if sizes == (12,):
        payload = [payload]
    assert _parse_thumbnails(payload) == _images("https://img.example/thumb", *sizes)


@pytest.mark.parametrize("payload", ["invalid", []])
def test_invalid_thumbnail_containers(payload):
    assert _parse_thumbnails(cast("Any", payload)) == []


def test_action_buttons():

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


@pytest.mark.parametrize("valid", [True, False])
def test_badge_icons_and_missing_titles(monkeypatch, valid):
    badge = {"icon": "SPONSOR", "badge_icons": [{}, {"url": ""}]}
    expected = {"icon_name": "sponsor", "icons": []}
    if valid:
        badge = {
            "tooltip": "Moderator",
            "icon": "MODERATOR",
            "badge_icons": [
                {"url": f"https://img.example/badge={size}"}
                for size in ("s16", "s32", "no-size")
            ],
        }
        expected = {
            "title": "Moderator",
            "icon_name": "moderator",
            "icons": _images("https://img.example/badge", 16, 32),
        }
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.parsing.message_items_content_parser._parse_item",
        lambda _badge: badge,
    )
    assert _parse_badges([{"liveChatAuthorBadgeRenderer": {}}]) == [expected]


@pytest.mark.parametrize(
    ("text", "amount", "currency", "symbol"),
    [
        ("$1,234.50", 1234.5, "USD", "$"),
        ("CHF12.30", 12.3, "CHF", "CHF"),
        ("12.30", 12.3, "", ""),
        ("unstructured", 42.5, None, None),
    ],
)
def test_currency(monkeypatch, text, amount, currency, symbol):
    if currency is None:
        prefix = "chat_downloader.sites.youtube.parsing.message_content_badges.re"
        monkeypatch.setattr(f"{prefix}.split", lambda *_: ["not-parseable"])
        monkeypatch.setattr(f"{prefix}.sub", lambda *_: "42.5")
    assert _parse_currency({"simpleText": text}) == {
        "text": text,
        "amount": amount,
        "currency": currency,
        "currency_symbol": symbol,
    }


def test_parse_item_returns_existing_info_for_empty_renderer() -> None:
    info = {"kept": True}
    assert _parse_item({"liveChatTextMessageRenderer": {}}, info=info) == {"kept": True}


def test_parse_item_recurses_moves_author_and_applies_offset_once(item_parser) -> None:
    parse = item_parser(
        {"authorImages": "author_images", "timeText": "time_text"},
        ["bodyBackgroundColor"],
    )

    result = parse(
        {
            "outerRenderer": {
                "bodyBackgroundColor": 0xFF112233,
                **_nested_item({"nestedRenderer": {"authorImages": {"thumb": "img"}}}),
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


@pytest.mark.parametrize("icons", [None, [{"url": "badge"}]])
def test_custom_author_badge_roles(icons):
    badge = {"icon_name": "custom"}
    if icons is not None:
        badge["icons"] = icons
    author = {"badges": [badge]}
    _apply_author_roles(author)
    if icons:
        assert author["is_sponsor"] is True
    else:
        assert author == {"badges": [{"icon_name": "custom"}]}


@pytest.mark.parametrize("header", [True, False])
def test_parse_item_header_and_generated_time(item_parser, header):
    payload = {"unused": True}
    info = {"time_in_seconds": 5}
    if header:
        payload = {
            "showItemEndpoint": {"showLiveChatItemEndpoint": {}},
            "header": {"headerRenderer": {"timeText": "0:09"}},
        }
        info = None
    result = item_parser({"timeText": "time_text"} if header else {})(
        {"outerRenderer": payload},
        info=info,
    )
    assert result["time_in_seconds"] == (9 if header else 5)
    if not header:
        assert result["time_text"] == "0:05"
    assert result["message"] is None


@pytest.mark.parametrize(
    ("nested", "info", "expected"),
    [
        ({"timeText": "0:09"}, {"time_in_seconds": 5.25}, (5.25, "0:05")),
        ({}, {"time_in_seconds": 5.25, "time_text": "0:02"}, (5.25, "0:02")),
        ({"timeText": "0:09"}, {"time_in_seconds": 0}, (9, "0:09")),
    ],
)
def test_parse_item_preserves_wrapper_or_nested_timing(
    item_parser, nested, info, expected
):
    parse = item_parser({"timeText": "time_text"} if nested else {})
    result = parse(
        {"outerRenderer": _nested_item({"nestedRenderer": nested})},
        info=info,
        preserve_wrapper_time=True,
    )
    assert (result["time_in_seconds"], result["time_text"]) == expected


@pytest.mark.parametrize(
    ("styles", "expected"),
    [(["LIVE"], "LIVE"), (None, "DEFAULT"), (["", "UPCOMING"], "UPCOMING")],
)
def test_video_overlay_styles(styles, expected):
    payload = {
        "videoId": "abc123",
        "title": {"runs": [{"text": "Example"}]},
        "viewCountText": {"simpleText": "1 watching"},
        "shortViewCountText": {"simpleText": "1"},
    }
    if styles is not None:
        payload["thumbnailOverlays"] = [
            {"thumbnailOverlayTimeStatusRenderer": {"style": style}} for style in styles
        ]
    assert _parse_video(payload) == {
        "video_id": "abc123",
        "title": "Example",
        "video_type": expected,
        "view_count": "1 watching",
        "short_view_count": "1",
    }


@pytest.mark.parametrize("live", [True, False], ids=["live-lockup", "plain-lockup"])
def test_parse_video_accepts_lockup_view_model(live) -> None:
    video_id, title = (
        ("live123", "Live stream") if live else ("plain123", "Plain upload")
    )
    metadata = {"title": {"content": title}}
    lockup = {
        "contentId": video_id,
        "metadata": {"lockupMetadataViewModel": metadata},
    }
    if live:
        lockup.update(_make_lockup_with_badge("LIVE"))
        metadata["metadata"] = _wrap(
            "contentMetadataViewModel.metadataRows",
            [
                {"metadataParts": [{"text": {"content": "1 watching"}}]},
            ],
        )
    expected = {"video_id": video_id, "title": title, "video_type": "DEFAULT"}
    if live:
        expected.update(
            video_type="LIVE", view_count="1 watching", short_view_count="1 watching"
        )
    assert _parse_video({"lockupViewModel": lockup}) == expected


def _make_lockup_with_badge(text: str) -> dict[str, Any]:
    return _wrap(
        "contentImage.thumbnailViewModel.overlays",
        [
            _wrap(
                "thumbnailBottomOverlayViewModel.badges",
                [
                    {"thumbnailBadgeViewModel": {"text": text}},
                ],
            ),
        ],
    )


@pytest.mark.parametrize(
    ("badge", "expected"),
    [
        ("PREMIERE", "UPCOMING"),
        ("UPCOMING", "UPCOMING"),
        ("SHORTS", None),
        (None, None),
    ],
)
def test_parse_lockup_badge_style(badge, expected) -> None:
    lockup = _make_lockup_with_badge(badge) if badge is not None else {}
    assert _parse_lockup_badge_style(lockup) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Free", None), ("N/A", None), ("1.99", 1.99), ("1234", 1234.0)],
)
def test_safe_float(text, expected) -> None:
    assert _safe_float(text) == expected
