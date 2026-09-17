# SPDX-License-Identifier: MIT

"""Captured mobile replay models through parsing, filtering and real writers."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.models import ChatRequest
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat
from chat_downloader.sites.youtube.continuation import _ContinuationLoop
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from chat_downloader.sites.youtube.paid_events import PaidEventCache
from chat_downloader.sites.youtube.parsing.actions_router import process_action
from chat_downloader.sites.youtube.parsing.message_content_text_parser import (
    _parse_runs,
)
from chat_downloader.sites.youtube.parsing.message_items_content_parser import (
    _parse_item,
)
from chat_downloader.sites.youtube.parsing.modern_elements import normalize_element
from chat_downloader.sites.youtube.parsing.modern_text import (
    attributed_text,
    author_badges,
    image_thumbnails,
)

FIXTURE = (
    Path(__file__).parent / "fixtures/youtube/live_events/mobile-replay-elements.json"
)


def _actions():
    return json.loads(FIXTURE.read_text())["continuationContents"][
        "liveChatContinuation"
    ]["actions"]


def _poll(actions, *, continuation=None):
    chat = {"actions": actions}
    if continuation is not None:
        chat["continuations"] = [
            {"timedContinuationData": {"continuation": continuation, "timeoutMs": 500}}
        ]
    return {"continuationContents": {"liveChatContinuation": chat}}


def _replay(monkeypatch, groups=None):
    actions = _actions()
    responses = iter([_poll(actions[:3], continuation="next"), _poll(actions[3:])])
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation._get_continuation_info",
        lambda *a, **kw: next(responses),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.continuation.polling_sleep", lambda _: None
    )
    provider = YouTubeChatDownloader(request_profile="youtube_android")
    loop = _ContinuationLoop(
        provider,
        {"status": "was_live", "continuation_info": {"Live chat": "first"}},
        {"INNERTUBE_API_KEY": "fixture"},
        ChatRequest(
            url="https://www.youtube.com/watch?v=fixture",
            message_groups=groups or ["all"],
        ),
    )
    try:
        yield from loop.run()
    finally:
        provider.close()


def test_mobile_replay_preserves_paid_events_emotes_and_writer_parity(
    monkeypatch, tmp_path
):
    formatter = ItemFormatter()
    chat = Chat(chat=_replay(monkeypatch))
    chat.set_formatter(lambda item: formatter.format(item, format_name="youtube"))
    raw, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    chat.attach_writer(ContinuousWriter(str(raw), lazy_initialise=True))
    chat.attach_writer(ContinuousWriter(str(txt), lazy_initialise=True))
    messages = list(chat)
    chat.close()
    assert [m["message_type"] for m in messages] == [
        "viewer_engagement_message",
        "text_message",
        "paid_message",
        "ticker_paid_message_item",
        "paid_sticker",
        "ticker_paid_sticker_item",
        "chat_ended",
    ]
    assert messages[0]["message"].startswith("Live chat replay is on.")
    assert messages[1]["message"] == ":thanksdoc:" * 6
    assert messages[1]["emotes"][0]["images"]
    for paid, ticker, amount in [
        (messages[2], messages[3], 20),
        (messages[4], messages[5], 5),
    ]:
        for key in ("message_id", "message", "money"):
            assert paid[key] == ticker[key]
        assert ticker["money"]["amount"] == amount
        assert ticker["money"]["currency"] == "BRL"
        assert paid["author"]["name"] == ticker["author"]["name"] == "@fixture-viewer"
    assert messages[4]["sticker_images"]
    assert "Lemon" in messages[4]["message"]
    assert all("timestamp" not in m for m in messages)
    assert messages[2]["time_in_seconds"] == messages[3]["time_in_seconds"] == 8376.202
    stats = audit_capture(raw, txt, formatter=formatter, format_name="youtube")
    assert not stats.failed
    assert (stats.jsonl_records, stats.txt_lines, stats.suppressed_duplicates) == (
        7,
        5,
        2,
    )
    assert "R$20.00" in txt.read_text()
    assert "Fixture paid message" in txt.read_text()


def test_ticker_only_filter_still_enriches_from_paid_events_across_polls(monkeypatch):
    messages = list(_replay(monkeypatch, ["tickers"]))
    assert len(messages) == 2
    assert messages[0]["message"] == "Fixture paid message"
    assert messages[0]["money"]["amount"] == 20
    assert "Lemon" in messages[1]["message"]


def _element(**models):
    return {
        "elementRenderer": {
            "newElement": {"type": {"componentType": {"model": models}}}
        }
    }


@pytest.mark.parametrize(
    "item",
    [{}, {"elementRenderer": None}]
    + [
        _element(**{name: {}})
        for name in (
            "unknownModel",
            "liveChatTextMessageModel",
            "superChatItemModel",
            "liveChatPaidStickerModel",
            "viewerEngagementMessageModel",
        )
    ]
    + [
        _element(liveChatTextMessageModel={}, superChatItemModel={}),
        _element(
            liveChatTextMessageModel={
                "messageData": {"attributedTextData": {"unexpected": True}}
            }
        ),
        _element(superChatItemModel={"paidMessageData": {"unexpected": True}}),
        _element(
            liveChatPaidStickerModel={"liveChatPaidSticker": {"unexpected": True}}
        ),
    ],
)
def test_unknown_or_malformed_element_is_not_silently_reclassified(item):
    assert normalize_element(item) is item


def test_logging_identifiers_never_become_message_timestamps():
    item = _actions()[1]["replayChatItemAction"]["actions"][0]["addChatItemAction"][
        "item"
    ]
    before = deepcopy(item)
    one = normalize_element(item)
    assert item == before
    item["elementRenderer"]["newElement"]["properties"]["identifierProperties"][
        "uniqueLoggingIdentifier"
    ] = "1889485735158894512"
    assert normalize_element(item) == one
    assert "timestampUsec" not in one["liveChatTextMessageRenderer"]


def _attachment(start=0, length=1, label="wave"):
    return {
        "startIndex": start,
        "length": length,
        "element": {
            "properties": {"accessibilityProperties": {"label": label}},
            "type": {
                "imageType": {
                    "image": {"sources": [{"url": "https://img.example/emote"}]}
                }
            },
        },
    }


@pytest.mark.parametrize(
    ("content", "attachments", "expected"),
    [
        ("😀□", [_attachment(start, length)], "😀□")
        for start, length in (
            (-1, 1),
            (True, 1),
            (1, True),
            (1, 1),
            (0, 1),
            (2, 0),
            (2, -1),
            (2, 2),
            (99, 1),
            ("2", 1),
        )
    ]
    + [
        (
            "□□",
            [_attachment(), _attachment(), _attachment(1, label=None)],
            ":wave::emoji:",
        ),
        ("□", [None, {"startIndex": 0, "length": 1}], "□"),
        ("😀□ café □!", [_attachment(9), _attachment(2)], "😀:wave: café :wave:!"),
    ],
)
def test_attachment_ranges_preserve_unicode_and_input(content, attachments, expected):
    value = {"content": content, "attachmentRuns": attachments}
    before = deepcopy(value)
    result = _parse_runs(attributed_text(value))
    assert result["message"] == expected
    if content == "😀□ café □!":
        assert len(result["emotes"]) == 1
    assert value == before


def test_malformed_attributed_metadata_is_ignored():
    assert attributed_text({}) == {}
    assert author_badges({"authorName": {"attachmentRuns": [None, {}]}}) == []
    assert image_thumbnails({"sources": [None, {}, {"url": " "}]}) == {}


def _event(cache=None, *, ticker=False, message_id="a", **fields):
    item = {
        "message_id": message_id,
        "message_type": "ticker_paid_message_item" if ticker else "paid_message",
        **fields,
    }
    if cache is not None:
        cache.enrich(item)
    return item


def test_paid_cache_is_bounded_and_does_not_alias_or_overwrite():
    cache = PaidEventCache(limit=1)
    paid = _event(
        cache,
        message="original",
        money={"amount": 5},
        author={"id": "u", "name": "name"},
    )
    paid["money"]["amount"] = 99
    ticker = _event(cache, ticker=True, author={"id": "u"})
    assert ticker["money"]["amount"] == 5
    ticker["money"]["amount"] = 12
    ticker["message"] = "ticker text"
    cache.enrich(ticker)
    assert ticker["message"] == "ticker text"
    assert ticker["money"]["amount"] == 12
    _event(cache, message_id="b")
    assert "money" not in _event(cache, ticker=True)
    cache.enrich({"message_type": "paid_message"})
    wrong_type = _event(cache, message_id="b", message_type="ticker_paid_sticker_item")
    assert "money" not in wrong_type


def test_paid_cache_rejects_conflicting_author_and_preserves_zero_timestamp():
    cache = PaidEventCache()
    _event(
        cache,
        money={"amount": 5},
        author={"id": "a", "name": "original"},
        timestamp=123,
    )
    conflict = _event(ticker=True, author={"id": "other"})
    before = deepcopy(conflict)
    cache.enrich(conflict)
    assert conflict == before
    ticker = _event(cache, ticker=True, timestamp=0)
    assert ticker["timestamp"] == 0
    assert ticker["author"]["name"] == "original"
    ticker["author"]["name"] = "changed"
    assert _event(cache, ticker=True)["author"]["name"] == "original"


def test_zero_capacity_cache_and_unidentified_messages_do_not_enrich():
    cache = PaidEventCache(limit=0)
    cache.enrich({"message_type": [], "message_id": "a"})
    _event(cache, message="body")
    assert "message" not in _event(cache, ticker=True)


@pytest.mark.parametrize(
    "item",
    [
        _element(
            superChatItemModel={
                "paidMessageData": {
                    "paidItemHeaderStaticData": {"authorName": "viewer"},
                    "paidMessageHeaderData": {"priceText": ""},
                }
            }
        ),
        _element(
            liveChatPaidStickerModel={
                "liveChatPaidSticker": {"authorName": {"content": "viewer"}}
            }
        ),
    ],
)
def test_missing_mobile_price_never_becomes_synthetic_money(item):
    message = _parse_item(item)
    assert "money" not in message
    assert message["author"]["name"] == "viewer"


def test_replacement_and_nested_items_use_same_mobile_normalizer():
    item = _actions()[2]["replayChatItemAction"]["actions"][0]["addChatItemAction"][
        "item"
    ]
    result = process_action({"replaceChatItemAction": {"replacementItem": item}})
    assert result.message_type == "liveChatPaidMessageRenderer"
    assert result.parsed_data["message"] == "Fixture paid message"
    assert result.parsed_data["money"]["amount"] == 20
    classic = _parse_item(
        {
            "liveChatTextMessageRenderer": {
                "timestampUsec": "1234567",
                "message": {"runs": [{"text": "classic"}]},
            }
        }
    )
    assert classic["timestamp"] == 1234567


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {
                "authorName": {"simpleText": display},
                "authorUsername": {"simpleText": "@handle"},
            },
            expected,
        )
        for display, expected in [("Display name", "Display name"), ("", "@handle")]
    ]
    + [({"authorUsername": {}}, None)],
)
def test_ticker_author_display_name_fallback(reverse, fields, expected):
    if reverse:
        fields = dict(reversed(list(fields.items())))
    message = _parse_item({"liveChatTickerPaidMessageItemRenderer": fields})
    assert message.get("author", {}).get("name") == expected
