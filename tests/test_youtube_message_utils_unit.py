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
from tests.youtube_third_helpers import patch, returns, wrap


@pytest.fixture(autouse=True)
def _clear_remapping_cache():
    """Isolate the memoised remapping table across patched tests."""
    _get_remapping.cache_clear()
    yield
    _get_remapping.cache_clear()


@pytest.fixture
def item_parser(monkeypatch):
    def configure(remapping, colours=()):
        _get_remapping.cache_clear()
        returns(monkeypatch, "constants_message.build_remapping", remapping)
        patch(monkeypatch, "constants_message._COLOUR_KEYS", colours)
        return _parse_item

    return configure


def _image(url, size, **extra):
    return {"url": f"{url}=s{size}", "width": size, "height": size, **extra}


def _images(url, *sizes):
    return [{"url": url, "id": "source"}] + [
        _image(url, size, id=f"{size}x{size}") for size in sizes
    ]


def _nested_item(renderer):
    return wrap("showItemEndpoint.showLiveChatItemEndpoint.renderer", renderer)


def _lockup_badge(text):
    return wrap(
        "contentImage.thumbnailViewModel.overlays",
        [
            wrap(
                "thumbnailBottomOverlayViewModel.badges",
                [
                    wrap("thumbnailBadgeViewModel.text", text),
                ],
            ),
        ],
    )


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


@pytest.mark.parametrize("valid", [True, False])
def test_navigation_and_action_buttons(valid):
    endpoint = wrap("commandMetadata.webCommandMetadata.url", "/watch?v=xyz")
    assert _parse_navigation_endpoint(
        endpoint if valid else {},
        default_text="fallback",
    ) == ("https://www.youtube.com/watch?v=xyz" if valid else "fallback")
    button = wrap(
        "buttonRenderer",
        {
            "navigationEndpoint": endpoint,
            "text": {"simpleText": "Open"},
        },
    )
    assert _parse_action_button(button if valid else {}) == (
        {"url": "https://www.youtube.com/watch?v=xyz", "text": "Open"}
        if valid
        else {"url": "", "text": ""}
    )


def test_text_helpers_parse_simple_text_runs_links_and_emotes():
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
                    "navigationEndpoint": wrap(
                        "commandMetadata.webCommandMetadata.url",
                        "/watch?v=abc123",
                    ),
                },
                {"emoji": emoji},
                {"emoji": {"emojiId": "smile", "shortcuts": [":)"]}},
                {"unknown": True},
            ]
        }
    )
    assert parsed == {
        "message": "Look https://www.youtube.com/watch?v=abc123:):){'unknown': True}",
        "emotes": [
            {
                "id": "smile",
                "name": ":)",
                "shortcuts": [":)"],
                "search_terms": ["smile"],
                "images": _images("https://img.example/smile", 24),
                "is_custom_emoji": True,
            }
        ],
    }
    assert _parse_runs(
        {"runs": [{"text": "plain", "navigationEndpoint": {}}]},
        parse_links=False,
    ) == {"message": "plain"}


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


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-a-dict", ""),
        ({"runs": 123}, ""),
        ({"content": 123}, ""),
        ({"runs": [None, 123, "text", {"text": "valid"}]}, "valid"),
    ],
)
def test_invalid_run_containers_and_entries(payload, message):
    assert _parse_runs(payload) == {"message": message}


@pytest.mark.parametrize(
    "sizes",
    [(24, 48), (12,), (24,), "invalid", ()],
)
def test_thumbnail_containers(sizes):
    if sizes == "invalid" or not sizes:
        assert _parse_thumbnails(cast("Any", sizes or [])) == []
        return
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
    assert _parse_thumbnails([payload] if sizes == (12,) else payload) == (
        _images("https://img.example/thumb", *sizes)
    )


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
    returns(monkeypatch, "parsing.message_items_content_parser._parse_item", badge)
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
        returns(
            monkeypatch, "parsing.message_content_badges.re.split", ["not-parseable"]
        )
        returns(monkeypatch, "parsing.message_content_badges.re.sub", "42.5")
    assert _parse_currency({"simpleText": text}) == {
        "text": text,
        "amount": amount,
        "currency": currency,
        "currency_symbol": symbol,
    }


def test_parse_item_empty_and_nested_renderer(item_parser):
    assert _parse_item({"liveChatTextMessageRenderer": {}}, info={"kept": True}) == {
        "kept": True,
    }
    parse = item_parser(
        {"authorImages": "author_images", "timeText": "time_text"},
        ["bodyBackgroundColor"],
    )
    result = parse(
        {
            "outerRenderer": {
                "bodyBackgroundColor": 0xFF112233,
                **_nested_item(wrap("nestedRenderer.authorImages", {"thumb": "img"})),
                **wrap("header.headerRenderer.timeText", "1:02"),
            }
        },
        offset=2,
    )
    assert result["body_background_colour"] == "#112233ff"
    assert result["author"] == {"images": {"thumb": "img"}, "name": ""}
    assert (result["time_in_seconds"], result["time_text"]) == (60, "1:00")
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


@pytest.mark.parametrize(
    ("kind", "nested", "info", "expected"),
    [
        ("header", {"timeText": "0:09"}, None, (9, "0:09")),
        ("generated", {}, {"time_in_seconds": 5}, (5, "0:05")),
        ("nested", {"timeText": "0:09"}, {"time_in_seconds": 5.25}, (5.25, "0:05")),
        ("nested", {}, {"time_in_seconds": 5.25, "time_text": "0:02"}, (5.25, "0:02")),
        ("nested", {"timeText": "0:09"}, {"time_in_seconds": 0}, (9, "0:09")),
    ],
)
def test_item_header_and_wrapper_timing(item_parser, kind, nested, info, expected):
    payload = _nested_item({"nestedRenderer": nested})
    if kind == "header":
        payload = {
            **wrap("showItemEndpoint.showLiveChatItemEndpoint", {}),
            **wrap("header.headerRenderer", nested),
        }
    elif kind == "generated":
        payload = {"unused": True}
    result = item_parser({"timeText": "time_text"} if nested else {})(
        {"outerRenderer": payload},
        info=info,
        preserve_wrapper_time=kind == "nested",
    )
    assert (result["time_in_seconds"], result["time_text"]) == expected
    if kind != "nested":
        assert result["message"] is None


@pytest.mark.parametrize(
    ("styles", "expected"),
    [(["LIVE"], "LIVE"), (None, "DEFAULT"), (["", "UPCOMING"], "UPCOMING")],
)
def test_video_overlay_styles(styles, expected):
    payload = {
        "videoId": "abc123",
        **wrap("title.runs", [{"text": "Example"}]),
        **wrap("viewCountText.simpleText", "1 watching"),
        **wrap("shortViewCountText.simpleText", "1"),
    }
    if styles is not None:
        payload["thumbnailOverlays"] = [
            wrap("thumbnailOverlayTimeStatusRenderer.style", style) for style in styles
        ]
    assert _parse_video(payload) == {
        "video_id": "abc123",
        "title": "Example",
        "video_type": expected,
        "view_count": "1 watching",
        "short_view_count": "1",
    }


@pytest.mark.parametrize(
    "live",
    [True, False],
    ids=["live-lockup", "plain-lockup"],
)
def test_video_lockup_view_model(live):
    video_id, title = (
        ("live123", "Live stream") if live else ("plain123", "Plain upload")
    )
    metadata = wrap("title.content", title)
    lockup = {
        "contentId": video_id,
        **wrap("metadata.lockupMetadataViewModel", metadata),
    }
    expected = {"video_id": video_id, "title": title, "video_type": "DEFAULT"}
    if live:
        lockup.update(_lockup_badge("LIVE"))
        metadata["metadata"] = wrap(
            "contentMetadataViewModel.metadataRows",
            [
                {"metadataParts": [wrap("text.content", "1 watching")]},
            ],
        )
        expected.update(
            video_type="LIVE",
            view_count="1 watching",
            short_view_count="1 watching",
        )
    assert _parse_video({"lockupViewModel": lockup}) == expected


@pytest.mark.parametrize(
    ("badge", "expected"),
    [
        ("PREMIERE", "UPCOMING"),
        ("UPCOMING", "UPCOMING"),
        ("SHORTS", None),
        (None, None),
    ],
)
def test_lockup_badge_style(badge, expected):
    assert _parse_lockup_badge_style(
        _lockup_badge(badge) if badge is not None else {}
    ) == (expected)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("Free", None), ("N/A", None), ("1.99", 1.99), ("1234", 1234.0)],
)
def test_safe_float(text, expected):
    assert _safe_float(text) == expected
