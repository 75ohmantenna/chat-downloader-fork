# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import LoginRequired
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch.badge_client import update_badge_info
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader
from chat_downloader.sites.twitch.graphql_client import _PersistedQueryUnavailable
from chat_downloader.sites.twitch.parsing.badges import _parse_irc_badges


def _mobile_badge(set_id, version, title):
    return {
        "setID": set_id,
        "version": version,
        "title": title,
        "imageUrlNormal": f"https://badges.test/{set_id}/1",
        "imageUrlDouble": f"https://badges.test/{set_id}/2",
        "imageUrlQuadruple": f"https://badges.test/{set_id}/4",
    }


def _legacy_badge(set_id, version, channel_id="", **metadata):
    return {
        "id": base64.b64encode(f"{set_id};{version};{channel_id}".encode()).decode(),
        **metadata,
    }


def _payload(badges, *, channel=False):
    return [
        {
            "data": {"user": {"broadcastBadges": badges}}
            if channel
            else {"badges": badges}
        }
    ]


def _mobile_cycle(channel_badges, global_badges):
    return [
        _PersistedQueryUnavailable("rotated"),
        _payload(channel_badges, channel=True),
        _PersistedQueryUnavailable("rotated"),
        _payload(global_badges),
    ]


def _refresh(download, global_cache=None, channel_cache=None, **kwargs):
    global_cache = {} if global_cache is None else global_cache
    channel_cache = {} if channel_cache is None else channel_cache
    update_badge_info(
        object(), "caseoh_", download, global_cache, channel_cache, **kwargs
    )
    return global_cache, channel_cache


@pytest.mark.parametrize(
    "malformed", [False, True], ids=["normalized", "skip-malformed"]
)
def test_badge_refresh_falls_back_independently_and_normalizes_mobile_shapes(malformed):
    channel_badges = (
        [None, {"setID": "missing-version"}]
        if malformed
        else [_mobile_badge("subscriber", "12", "Subscriber")]
    )
    global_badges = (
        [None, {"setID": "missing-version"}]
        if malformed
        else [_mobile_badge("moderator", "1", "Moderator")]
    )
    download = Mock(side_effect=_mobile_cycle(channel_badges, global_badges))
    global_cache, channel_cache = _refresh(
        download, channel_id="123", client_id="client-id"
    )
    assert [call.args[1][0] for call in download.call_args_list] == [
        {"operationName": "ChatList_Badges", "variables": {"channelLogin": "caseoh_"}},
        {"operationName": "BroadcastBadges", "variables": {"userID": "123"}},
        {"operationName": "GlobalBadges"},
        {"operationName": "GlobalBadgesMobile"},
    ]
    if malformed:
        assert global_cache == channel_cache == {}
    else:
        stored = channel_cache["123"][("subscriber", "12")]
        assert [stored[f"image{size}x"] for size in (1, 2, 4)] == [
            f"https://badges.test/subscriber/{size}" for size in (1, 2, 4)
        ]
        assert global_cache[("moderator", "1")]["title"] == "Moderator"


@pytest.mark.parametrize(
    ("source", "failure", "channel_id"),
    [
        ("channel", RequestException("channel unavailable"), None),
        ("channel", LoginRequired("badge auth unavailable"), "123"),
        ("global", RequestException("global unavailable"), None),
        ("channel", _PersistedQueryUnavailable("rotated"), None),
        ("channel", [], None),
        ("channel", [{"data": 1}], None),
        ("global", [{"data": {"badges": 1}}], None),
        ("channel", [{"data": {"user": 1}}], None),
    ],
    ids=[
        "channel-network",
        "auth-no-fallback",
        "global-network",
        "no-id-no-fallback",
        "empty-operation",
        "bad-data",
        "bad-global-list",
        "bad-channel-user",
    ],
)
def test_badge_source_failure_is_isolated_without_fallback(source, failure, channel_id):
    channel_badge = _legacy_badge(
        "subscriber",
        "12",
        "123",
        title="Subscriber",
        clickAction="visit_url",
        clickURL="https://example.test/subscriber",
    )
    global_badge = _legacy_badge(
        "moderator", "1", title="Moderator", image1x="global.png"
    )
    replies = (
        [failure, _payload([global_badge])]
        if source == "channel"
        else [_payload([channel_badge]), failure]
    )
    download = Mock(side_effect=replies)
    global_cache, channel_cache = _refresh(download, channel_id=channel_id)
    assert [call.args[1][0]["operationName"] for call in download.call_args_list] == [
        "ChatList_Badges",
        "GlobalBadges",
    ]
    if source == "channel":
        assert global_cache[("moderator", "1")]["image1x"] == "global.png"
    else:
        stored = channel_cache["123"][("subscriber", "12")]
        assert stored["clickAction"] == "visit_url"
        assert stored["clickURL"] == "https://example.test/subscriber"


def test_mobile_refresh_preserves_legacy_click_metadata():
    channel_badge = _legacy_badge(
        "subscriber",
        "12",
        "123",
        title="Old subscriber",
        clickAction="visit_url",
        clickURL="https://example.test/subscriber",
    )
    global_badge = _legacy_badge(
        "moderator",
        "1",
        title="Old moderator",
        clickAction="subscribe_to_channel",
        clickURL="https://example.test/moderator",
    )
    download = Mock(
        side_effect=[
            _payload([channel_badge]),
            _payload([global_badge]),
            *_mobile_cycle(
                [_mobile_badge("subscriber", "12", "New subscriber")],
                [_mobile_badge("moderator", "1", "New moderator")],
            ),
        ]
    )
    global_cache, channel_cache = _refresh(download, channel_id="123")
    _refresh(download, global_cache, channel_cache, channel_id="123")
    channel = channel_cache["123"][("subscriber", "12")]
    global_badge = global_cache[("moderator", "1")]
    assert channel["title"] == "New subscriber"
    assert channel["clickURL"] == "https://example.test/subscriber"
    assert global_badge["title"] == "New moderator"
    assert global_badge["clickAction"] == "subscribe_to_channel"


def test_badge_refresh_does_not_hide_collaborator_type_error():
    with pytest.raises(TypeError, match="programmer error"):
        _refresh(Mock(side_effect=TypeError("programmer error")))


def test_metadata_badge_fallback_cache_parser_and_reconnect_compose():
    unavailable = [{"errors": [{"message": "PersistedQueryNotFound"}]}]
    badge_cycle = [
        unavailable,
        _payload([_mobile_badge("subscriber", "12", "Subscriber")], channel=True),
        unavailable,
        _payload([_mobile_badge("moderator", "1", "Moderator")]),
    ]
    payloads = iter(
        [
            [
                {
                    "data": {
                        "user": {
                            "id": "123",
                            "stream": {"type": "live"},
                            "lastBroadcast": {"title": "Live"},
                        }
                    }
                }
            ],
            *badge_cycle,
            *badge_cycle,
        ]
    )
    requests = []

    def session_post(_url, json, headers):
        requests.append(json)
        return SimpleNamespace(status_code=200, text="", json=lambda: next(payloads))

    downloader = TwitchChatDownloader()
    downloader._session_post = session_post
    downloader.get_chat_by_stream_id(
        "CaseOh_",
        ChatRequest(url="https://www.twitch.tv/caseoh_", max_attempts=1),
    )
    downloader._update_badge_info("caseoh_")
    parsed = _parse_irc_badges(
        "subscriber/12,moderator/1", "123", downloader.badge_cache.snapshot()
    )
    assert [badge["title"] for badge in parsed] == ["Subscriber", "Moderator"]
    assert parsed[0]["icons"][0]["url"] == "https://badges.test/subscriber/1"
    assert requests[2][0]["variables"] == {"userID": "123"}
    assert requests[6][0]["variables"] == {"userID": "123"}
