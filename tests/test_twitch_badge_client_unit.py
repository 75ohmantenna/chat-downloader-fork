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
from chat_downloader.sites.twitch.parsing.messages import _parse_item
from chat_downloader.sites.twitch.types import _BADGE_CHANNEL_LIMIT, BadgeCache
from tests.twitch_third_helpers import gql_comment, gql_message, irc_frame, parse_irc


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


def _subscriber_badge(title="Subscriber"):
    return _legacy_badge(
        "subscriber",
        "12",
        "123",
        title=title,
        clickAction="visit_url",
        clickURL="https://example.test/subscriber",
    )


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
    channel_badges, global_badges = (
        [None, {"setID": "missing-version"}]
        if malformed
        else [_mobile_badge(name, version, title)]
        for name, version, title in (
            ("subscriber", "12", "Subscriber"),
            ("moderator", "1", "Moderator"),
        )
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
    channel_badge = _subscriber_badge()
    global_badge = _legacy_badge(
        "moderator", "1", title="Moderator", image1x="global.png"
    )
    replies = (
        [failure, _payload([global_badge])]
        if source == "channel"
        else [_payload([channel_badge], channel=True), failure]
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
    channel_badge = _subscriber_badge("Old subscriber")
    global_badge = _legacy_badge(
        "moderator",
        "1",
        title="Old moderator",
        clickAction="subscribe_to_channel",
        clickURL="https://example.test/moderator",
    )
    download = Mock(
        side_effect=[
            _payload([channel_badge], channel=True),
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

    def session_post(_url, json, headers, **_kwargs):
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


@pytest.mark.parametrize("mobile", [False, True], ids=["legacy", "mobile"])
@pytest.mark.parametrize("empty", [False, True], ids=["replace", "remove-all"])
def test_successful_refresh_replaces_badge_keys_without_mutating_snapshot(
    mobile, empty
):
    cache = BadgeCache()
    _refresh(
        Mock(
            side_effect=[
                _payload(
                    [_subscriber_badge(), _legacy_badge("obsolete", "1", "123")],
                    channel=True,
                ),
                _payload(
                    [
                        _legacy_badge("moderator", "1", title="Old moderator"),
                        _legacy_badge("obsolete", "1"),
                    ]
                ),
            ]
        ),
        cache.global_badges,
        cache.channel_badges,
        channel_id="123",
    )
    cache.channel_badges["456"] = {("subscriber", "12"): {"title": "Other"}}
    before = cache.snapshot()
    channel = [] if empty else [_subscriber_badge("New subscriber")]
    global_badges = (
        [] if empty else [_legacy_badge("moderator", "1", title="New moderator")]
    )
    replies = [_payload(channel, channel=True), _payload(global_badges)]
    if mobile:
        replies = _mobile_cycle(
            [] if empty else [_mobile_badge("subscriber", "12", "New subscriber")],
            [] if empty else [_mobile_badge("moderator", "1", "New moderator")],
        )
    _refresh(
        Mock(side_effect=replies),
        cache.global_badges,
        cache.channel_badges,
        channel_id="123",
    )
    assert set(cache.channel_badges["123"]) == (
        set() if empty else {("subscriber", "12")}
    )
    assert set(cache.global_badges) == (set() if empty else {("moderator", "1")})
    assert cache.channel_badges["456"] == before.channel_badges["456"]
    if not empty:
        assert cache.channel_badges["123"][("subscriber", "12")]["title"] == (
            "New subscriber"
        )
        assert cache.global_badges[("moderator", "1")]["title"] == "New moderator"
    assert before.channel_badges["123"][("subscriber", "12")]["title"] == "Subscriber"
    assert ("obsolete", "1") in before.global_badges
    assert before.global_badges[("moderator", "1")]["title"] == "Old moderator"


@pytest.mark.parametrize("failed_source", ["channel", "global"])
@pytest.mark.parametrize(
    "failure_kind",
    [
        "network",
        "bad-list",
        "unusable-records",
        "missing-data",
        "missing-list",
        "null-list",
        "null-user",
        "empty-set",
        "empty-version",
        "wrong-source",
    ],
)
def test_failed_refresh_keeps_old_source_while_successful_source_replaces(
    failed_source, failure_kind
):
    global_cache = {("old-global", "1"): {"title": "Useful global"}}
    channel_cache = {
        "123": {("old-channel", "1"): {"title": "Useful channel"}},
        "456": {("other", "1"): {"title": "Other channel"}},
    }
    channel = failed_source == "channel"
    identity_channel = "123" if channel else ""
    failure = {
        "network": RequestException("temporarily unavailable"),
        "bad-list": _payload(1, channel=channel),
        "unusable-records": _payload([{}], channel=channel),
        "missing-data": [{"data": {}}],
        "missing-list": [{"data": {"user": {}}}],
        "null-list": _payload(None, channel=channel),
        "null-user": [{"data": {"user": None}}],
        "empty-set": _payload(
            [_legacy_badge("", "1", identity_channel)], channel=channel
        ),
        "empty-version": _payload(
            [_legacy_badge("subscriber", "", identity_channel)], channel=channel
        ),
        "wrong-source": _payload(
            [_legacy_badge("subscriber", "1", "" if channel else "123")],
            channel=channel,
        ),
    }[failure_kind]
    replies = (
        [failure, _payload([])]
        if failed_source == "channel"
        else [_payload([], channel=True), failure]
    )
    _refresh(Mock(side_effect=replies), global_cache, channel_cache, channel_id="123")
    assert global_cache == (
        {}
        if failed_source == "channel"
        else {("old-global", "1"): {"title": "Useful global"}}
    )
    assert channel_cache["123"] == (
        {("old-channel", "1"): {"title": "Useful channel"}}
        if failed_source == "channel"
        else {}
    )
    assert channel_cache["456"] == {("other", "1"): {"title": "Other channel"}}


def test_provider_reuse_bounds_lru_badges_and_logins_and_preserves_shared_snapshot(
    monkeypatch,
):
    def download(_session_post, operations, **_kwargs):
        operation = operations[0]
        if operation["operationName"] == "GlobalBadges":
            return _payload([_legacy_badge("moderator", "1", title="Moderator")])
        channel_id = operation["variables"]["channelLogin"].lower().removeprefix("user")
        return _payload(
            [_legacy_badge("subscriber", "12", channel_id, title=f"Sub {channel_id}")],
            channel=True,
        )

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor._download_gql", download
    )
    downloader = TwitchChatDownloader()
    downloader._update_badge_info("User0", "0")
    downloader._update_badge_info("User1", "1")
    active = downloader.badge_cache.snapshot()
    for index in range(2, _BADGE_CHANNEL_LIMIT):
        downloader._update_badge_info(f"user{index}", str(index))
    downloader._update_badge_info("USER0")
    downloader._update_badge_info(
        f"user{_BADGE_CHANNEL_LIMIT}", str(_BADGE_CHANNEL_LIMIT)
    )
    assert "0" in downloader.badge_cache.channel_badges
    assert "1" not in downloader.badge_cache.channel_badges
    assert "user0" in downloader._channel_ids
    assert "user1" not in downloader._channel_ids
    for index in range(_BADGE_CHANNEL_LIMIT + 1, 3 * _BADGE_CHANNEL_LIMIT):
        downloader._update_badge_info(f"user{index}", str(index))
        assert len(downloader.badge_cache.channel_badges) == _BADGE_CHANNEL_LIMIT
        assert len(downloader._channel_ids) == _BADGE_CHANNEL_LIMIT
    assert len(downloader.badge_cache.snapshot().channel_badges) == _BADGE_CHANNEL_LIMIT
    raw = irc_frame(
        "room-id=0;badges=subscriber/12;source-room-id=1;"
        "source-badges=subscriber/12;source-id=source-message"
    )
    parsed = parse_irc(raw, badge_set=active)
    assert parsed["author"]["badges"][0]["title"] == "Sub 0"
    assert parsed["shared_chat_source_badges"][0]["title"] == "Sub 1"
    assert _parse_irc_badges(
        "subscriber/12", "0", downloader.badge_cache.snapshot()
    ) == [{"name": "subscriber", "version": 12}]
    replay = _parse_item(
        gql_comment(
            message=gql_message(userBadges=[{"setID": "subscriber", "version": "12"}])
        ),
        offset=0,
        channel_id="0",
        badge_set=active,
    )
    assert replay["author"]["badges"][0]["title"] == "Sub 0"


@pytest.mark.parametrize("broadcast_badges", [None, [{}]])
def test_legacy_globals_cannot_mask_malformed_broadcast_collection(broadcast_badges):
    old_global = {("moderator", "1"): {"title": "Useful global"}}
    old_channel = {"123": {("subscriber", "12"): {"title": "Useful channel"}}}
    download = Mock(
        side_effect=[
            [
                {
                    "data": {
                        "badges": [
                            _legacy_badge("moderator", "1", title="Unrelated global")
                        ],
                        "user": {"broadcastBadges": broadcast_badges},
                    }
                }
            ],
            RequestException("global unavailable"),
        ]
    )
    _refresh(download, old_global, old_channel, channel_id="123")
    assert old_global[("moderator", "1")]["title"] == "Unrelated global"
    assert old_channel == {"123": {("subscriber", "12"): {"title": "Useful channel"}}}


@pytest.mark.parametrize("kind", ["vod", "clip"])
def test_lazy_replay_owns_badges_before_provider_reuse_evicts_channel(
    monkeypatch, kind
):
    def download(_session_post, operations, **_kwargs):
        operation = operations[0]
        if operation["operationName"] == "GlobalBadges":
            return _payload([])
        channel_id = operation["variables"]["channelLogin"].removeprefix("user")
        return _payload(
            [_legacy_badge("subscriber", "12", channel_id, title=f"Sub {channel_id}")],
            channel=True,
        )

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor._download_gql", download
    )
    downloader = TwitchChatDownloader()
    owner = {"login": "user0", "id": "0"}
    downloader._download_gql = Mock(
        return_value=[
            {
                "data": {
                    "video": {"title": "Replay", "lengthSeconds": 30, "owner": owner}
                }
            }
        ]
    )
    downloader._download_base_gql = Mock(
        return_value={
            "data": {
                "clip": {
                    "title": "Clip",
                    "video": {"id": "vod"},
                    "broadcaster": owner,
                    "videoOffsetSeconds": 0,
                    "durationSeconds": 30,
                }
            }
        }
    )
    fetch = Mock(
        return_value=(
            {
                "edges": [
                    {
                        "cursor": "terminal",
                        "node": gql_comment(
                            message=gql_message(
                                userBadges=[{"setID": "subscriber", "version": "12"}]
                            )
                        ),
                    }
                ],
                "pageInfo": {"hasNextPage": False},
            },
            {"creator": {"id": "creator", "channel": {"id": "0"}}},
        )
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id", fetch
    )
    request = ChatRequest(max_attempts=1)
    chat = (
        downloader.get_chat_by_vod_id("vod", request)
        if kind == "vod"
        else downloader.get_chat_by_clip_id("clip", request)
    )
    for index in range(1, _BADGE_CHANNEL_LIMIT + 2):
        downloader._update_badge_info(f"user{index}", str(index))
    assert "0" not in downloader.badge_cache.channel_badges
    assert "user0" not in downloader._channel_ids
    assert next(iter(chat))["author"]["badges"][0]["title"] == "Sub 0"
    chat.close()
    fetch.assert_called_once()


@pytest.mark.parametrize("standalone", ["valid", "empty", "network", "malformed"])
def test_legacy_root_globals_are_independent_current_refresh_metadata(standalone):
    cache = BadgeCache(
        global_badges={
            ("moderator", "1"): {"title": "Old", "clickURL": "https://badges.test/mod"},
            ("obsolete", "1"): {"title": "Obsolete"},
        },
        channel_badges={"123": {("subscriber", "12"): {"title": "Useful channel"}}},
    )
    before = cache.snapshot()
    standalone_response = {
        "valid": _payload([_legacy_badge("vip", "1", title="Current VIP")]),
        "empty": _payload([]),
        "network": RequestException("global unavailable"),
        "malformed": _payload(None),
    }[standalone]
    download = Mock(
        side_effect=[
            [
                {
                    "data": {
                        "badges": [
                            _legacy_badge("moderator", "1", title="Current moderator"),
                            _legacy_badge("invalid", ""),
                        ],
                        "user": None,
                    }
                }
            ],
            standalone_response,
        ]
    )
    _refresh(download, cache.global_badges, cache.channel_badges, channel_id="123")
    assert set(cache.global_badges) == (
        {("moderator", "1"), ("vip", "1")}
        if standalone == "valid"
        else {("moderator", "1")}
    )
    parsed = _parse_irc_badges("moderator/1", "123", cache.snapshot())[0]
    assert parsed["title"] == "Current moderator"
    assert parsed["clickURL"] == "https://badges.test/mod"
    assert cache.channel_badges == before.channel_badges
    assert before.global_badges[("moderator", "1")]["title"] == "Old"
    assert ("obsolete", "1") in before.global_badges
    _refresh(
        Mock(side_effect=[_payload([], channel=True), _payload([])]),
        cache.global_badges,
        cache.channel_badges,
        channel_id="123",
    )
    assert cache.global_badges == {}
    assert cache.channel_badges == {"123": {}}


@pytest.mark.parametrize("root_badges", [None, [{}]])
def test_bad_legacy_root_globals_do_not_block_valid_broadcast_refresh(root_badges):
    global_cache = {("moderator", "1"): {"title": "Useful global"}}
    channel_cache = {"123": {("obsolete", "1"): {"title": "Old channel"}}}
    _refresh(
        Mock(
            side_effect=[
                [
                    {
                        "data": {
                            "badges": root_badges,
                            "user": {"broadcastBadges": [_subscriber_badge("Current")]},
                        }
                    }
                ],
                RequestException("global unavailable"),
            ]
        ),
        global_cache,
        channel_cache,
        channel_id="123",
    )
    assert global_cache == {("moderator", "1"): {"title": "Useful global"}}
    assert set(channel_cache["123"]) == {("subscriber", "12")}
    assert channel_cache["123"][("subscriber", "12")]["title"] == "Current"
