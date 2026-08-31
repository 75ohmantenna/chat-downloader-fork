# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import LoginRequired
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch.badge_client import update_badge_info
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader
from chat_downloader.sites.twitch.graphql_client import _PersistedQueryUnavailable
from chat_downloader.sites.twitch.parsing.badges import _parse_irc_badges


def _legacy_badge_id(set_id: str, version: str, channel_id: str = "") -> str:
    return base64.b64encode(f"{set_id};{version};{channel_id}".encode()).decode()


def _mobile_badge(set_id: str, version: str, title: str) -> dict[str, str]:
    return {
        "setID": set_id,
        "version": version,
        "title": title,
        "imageUrlNormal": f"https://badges.test/{set_id}/1",
        "imageUrlDouble": f"https://badges.test/{set_id}/2",
        "imageUrlQuadruple": f"https://badges.test/{set_id}/4",
    }


def test_badge_refresh_falls_back_independently_and_normalizes_mobile_shapes() -> None:
    calls = []
    channel_badge = _mobile_badge("subscriber", "12", "Subscriber")
    global_badge = _mobile_badge("moderator", "1", "Moderator")

    def download(_session_post, ops, client_id=None):
        calls.append((ops, client_id))
        operation_name = ops[0]["operationName"]
        if operation_name in {"ChatList_Badges", "GlobalBadges"}:
            raise _PersistedQueryUnavailable("rotated")
        if operation_name == "BroadcastBadges":
            return [{"data": {"user": {"broadcastBadges": [channel_badge]}}}]
        return [{"data": {"badges": [global_badge]}}]

    global_cache = {}
    channel_cache = {}
    update_badge_info(
        object(),
        "caseoh_",
        download,
        global_cache,
        channel_cache,
        channel_id="123",
        client_id="client-id",
    )

    assert [call[0][0] for call in calls] == [
        {
            "operationName": "ChatList_Badges",
            "variables": {"channelLogin": "caseoh_"},
        },
        {
            "operationName": "BroadcastBadges",
            "variables": {"userID": "123"},
        },
        {"operationName": "GlobalBadges"},
        {"operationName": "GlobalBadgesMobile"},
    ]
    assert [call[1] for call in calls] == ["client-id"] * 4
    stored_channel = channel_cache["123"][("subscriber", "12")]
    stored_global = global_cache[("moderator", "1")]
    assert stored_channel["image1x"] == "https://badges.test/subscriber/1"
    assert stored_channel["image2x"] == "https://badges.test/subscriber/2"
    assert stored_channel["image4x"] == "https://badges.test/subscriber/4"
    assert stored_global["title"] == "Moderator"


def test_channel_badge_failure_does_not_block_global_badges() -> None:
    global_badge = {
        "id": _legacy_badge_id("moderator", "1"),
        "title": "Moderator",
        "image1x": "global.png",
    }

    def download(_session_post, ops, client_id=None):
        _ = client_id
        if ops[0]["operationName"] == "ChatList_Badges":
            raise RequestException("channel unavailable")
        return [{"data": {"badges": [global_badge]}}]

    global_cache = {}
    update_badge_info(object(), "caseoh_", download, global_cache, {})

    assert global_cache[("moderator", "1")]["image1x"] == "global.png"


def test_badge_auth_failure_is_isolated_without_fallback() -> None:
    calls = []
    global_badge = {
        "id": _legacy_badge_id("moderator", "1"),
        "title": "Moderator",
    }

    def download(_session_post, ops, client_id=None):
        _ = client_id
        operation_name = ops[0]["operationName"]
        calls.append(operation_name)
        if operation_name == "ChatList_Badges":
            raise LoginRequired("badge auth unavailable")
        return [{"data": {"badges": [global_badge]}}]

    global_cache = {}
    update_badge_info(object(), "caseoh_", download, global_cache, {}, channel_id="123")

    assert calls == ["ChatList_Badges", "GlobalBadges"]
    assert ("moderator", "1") in global_cache


def test_global_badge_failure_does_not_discard_channel_badges() -> None:
    channel_badge = {
        "id": _legacy_badge_id("subscriber", "12", "123"),
        "title": "Subscriber",
        "clickAction": "visit_url",
        "clickURL": "https://example.test/subscriber",
    }

    def download(_session_post, ops, client_id=None):
        _ = client_id
        if ops[0]["operationName"] == "GlobalBadges":
            raise RequestException("global unavailable")
        return [{"data": {"badges": [channel_badge]}}]

    channel_cache = {}
    update_badge_info(object(), "caseoh_", download, {}, channel_cache)

    stored = channel_cache["123"][("subscriber", "12")]
    assert stored["clickAction"] == "visit_url"
    assert stored["clickURL"] == "https://example.test/subscriber"


def test_channel_fallback_requires_known_channel_id_but_global_still_runs() -> None:
    calls = []

    def download(_session_post, ops, client_id=None):
        _ = client_id
        operation_name = ops[0]["operationName"]
        calls.append(operation_name)
        if operation_name == "ChatList_Badges":
            raise _PersistedQueryUnavailable("rotated")
        return [{"data": {"badges": []}}]

    update_badge_info(object(), "caseoh_", download, {}, {})

    assert calls == ["ChatList_Badges", "GlobalBadges"]


def test_mobile_badge_refresh_skips_malformed_items() -> None:
    calls = 0

    def download(_session_post, ops, client_id=None):
        nonlocal calls
        _ = client_id
        calls += 1
        if ops[0]["operationName"] in {"ChatList_Badges", "GlobalBadges"}:
            raise _PersistedQueryUnavailable("rotated")
        badges = [None, {"setID": "missing-version"}]
        if ops[0]["operationName"] == "BroadcastBadges":
            return [{"data": {"user": {"broadcastBadges": badges}}}]
        return [{"data": {"badges": badges}}]

    update_badge_info(object(), "caseoh_", download, {}, {}, channel_id="123")

    assert calls == 4


def test_mobile_refresh_preserves_legacy_click_metadata() -> None:
    legacy_channel = {
        "id": _legacy_badge_id("subscriber", "12", "123"),
        "title": "Old subscriber",
        "clickAction": "visit_url",
        "clickURL": "https://example.test/subscriber",
    }
    legacy_global = {
        "id": _legacy_badge_id("moderator", "1"),
        "title": "Old moderator",
        "clickAction": "subscribe_to_channel",
        "clickURL": "https://example.test/moderator",
    }
    use_mobile = False

    def download(_session_post, ops, client_id=None):
        _ = client_id
        operation_name = ops[0]["operationName"]
        if not use_mobile:
            badge = (
                legacy_global if operation_name == "GlobalBadges" else legacy_channel
            )
            return [{"data": {"badges": [badge]}}]
        if operation_name in {"ChatList_Badges", "GlobalBadges"}:
            raise _PersistedQueryUnavailable("rotated")
        if operation_name == "BroadcastBadges":
            return [
                {
                    "data": {
                        "user": {
                            "broadcastBadges": [
                                _mobile_badge("subscriber", "12", "New subscriber")
                            ]
                        }
                    }
                }
            ]
        return [
            {"data": {"badges": [_mobile_badge("moderator", "1", "New moderator")]}}
        ]

    global_cache = {}
    channel_cache = {}
    update_badge_info(
        object(),
        "caseoh_",
        download,
        global_cache,
        channel_cache,
        channel_id="123",
    )
    use_mobile = True
    update_badge_info(
        object(),
        "caseoh_",
        download,
        global_cache,
        channel_cache,
        channel_id="123",
    )

    channel = channel_cache["123"][("subscriber", "12")]
    global_badge = global_cache[("moderator", "1")]
    assert channel["title"] == "New subscriber"
    assert channel["clickURL"] == "https://example.test/subscriber"
    assert global_badge["title"] == "New moderator"
    assert global_badge["clickAction"] == "subscribe_to_channel"


@pytest.mark.parametrize("payload", [[], [{"data": 1}]])
def test_malformed_operation_container_isolated_from_global_badges(payload) -> None:
    global_badge = {
        "id": _legacy_badge_id("moderator", "1"),
        "title": "Moderator",
    }

    def download(_session_post, ops, client_id=None):
        _ = client_id
        if ops[0]["operationName"] == "ChatList_Badges":
            return payload
        return [{"data": {"badges": [global_badge]}}]

    global_cache = {}
    update_badge_info(object(), "caseoh_", download, global_cache, {})

    assert ("moderator", "1") in global_cache


def test_badge_refresh_does_not_hide_collaborator_type_error() -> None:
    def download(_session_post, _ops, client_id=None):
        _ = client_id
        raise TypeError("programmer error")

    with pytest.raises(TypeError, match="programmer error"):
        update_badge_info(object(), "caseoh_", download, {}, {})


def test_malformed_global_container_preserves_channel_badges() -> None:
    channel_badge = {
        "id": _legacy_badge_id("subscriber", "12", "123"),
        "title": "Subscriber",
    }

    def download(_session_post, ops, client_id=None):
        _ = client_id
        if ops[0]["operationName"] == "GlobalBadges":
            return [{"data": {"badges": 1}}]
        return [{"data": {"badges": [channel_badge]}}]

    channel_cache = {}
    update_badge_info(object(), "caseoh_", download, {}, channel_cache)

    assert ("subscriber", "12") in channel_cache["123"]


def test_malformed_channel_container_preserves_global_badges() -> None:
    global_badge = {
        "id": _legacy_badge_id("moderator", "1"),
        "title": "Moderator",
    }

    def download(_session_post, ops, client_id=None):
        _ = client_id
        if ops[0]["operationName"] == "ChatList_Badges":
            return [{"data": {"user": 1}}]
        return [{"data": {"badges": [global_badge]}}]

    global_cache = {}
    update_badge_info(object(), "caseoh_", download, global_cache, {})

    assert ("moderator", "1") in global_cache


def test_metadata_badge_fallback_cache_parser_and_reconnect_compose() -> None:
    channel_badge = _mobile_badge("subscriber", "12", "Subscriber")
    global_badge = _mobile_badge("moderator", "1", "Moderator")
    badge_cycle = [
        [{"errors": [{"message": "PersistedQueryNotFound"}]}],
        [{"data": {"user": {"broadcastBadges": [channel_badge]}}}],
        [{"errors": [{"message": "PersistedQueryNotFound"}]}],
        [{"data": {"badges": [global_badge]}}],
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

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return next(payloads)

    def session_post(_url, json, headers):
        _ = headers
        requests.append(json)
        return Response()

    downloader = TwitchChatDownloader()
    downloader._session_post = session_post
    downloader.get_chat_by_stream_id(
        "CaseOh_",
        ChatRequest(url="https://www.twitch.tv/caseoh_", max_attempts=1),
    )
    downloader._update_badge_info("caseoh_")

    parsed = _parse_irc_badges(
        "subscriber/12,moderator/1",
        "123",
        downloader.badge_cache.snapshot(),
    )
    assert [badge["title"] for badge in parsed] == ["Subscriber", "Moderator"]
    assert parsed[0]["icons"][0]["url"] == "https://badges.test/subscriber/1"
    assert requests[2][0]["variables"] == {"userID": "123"}
    assert requests[6][0]["variables"] == {"userID": "123"}
