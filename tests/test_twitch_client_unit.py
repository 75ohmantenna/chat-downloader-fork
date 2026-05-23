# SPDX-License-Identifier: MIT

import base64
from typing import NoReturn

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import UserNotFound
from chat_downloader.sites.twitch.constants import (
    CLIENT_ID,
    GQL_API_URL,
    OPERATION_HASHES,
)
from chat_downloader.sites.twitch.discovery import get_user_videos
from chat_downloader.sites.twitch.graphql_client import (
    _download_gql,
    update_badge_info,
)
from chat_downloader.sites.twitch.irc_transport import (
    _is_benign_unmatched_irc_buffer,
)
from chat_downloader.sites.twitch.replay_transport import (
    get_chat_messages_by_vod_id,
)


class _Resp:
    def __init__(self, payload) -> None:
        self._payload = payload

    def json(self):
        return self._payload


def test_download_gql_adds_persisted_query_hash_and_calls_base(
    monkeypatch,
) -> None:
    op_name = next(iter(OPERATION_HASHES.keys()))
    ops = [{"operationName": op_name, "variables": {"x": 1}}]
    ops_snapshot = [{"operationName": op_name, "variables": {"x": 1}}]

    calls = {}

    def session_post(url, json, headers):
        calls["url"] = url
        calls["json"] = json
        calls["headers"] = headers
        return _Resp([{"data": {"ok": True}}])

    out = _download_gql(session_post, ops)
    assert out == [{"data": {"ok": True}}]

    assert calls["url"] == GQL_API_URL
    assert calls["headers"]["Client-ID"] == CLIENT_ID
    assert (
        calls["json"][0]["extensions"]["persistedQuery"]["sha256Hash"]
        == OPERATION_HASHES[op_name]
    )
    assert ops == ops_snapshot, "Input operations must not be mutated in-place"


def test_download_gql_uses_client_id_override() -> None:
    op_name = next(iter(OPERATION_HASHES.keys()))
    calls = {}

    def session_post(url, json, headers):
        calls["headers"] = headers
        return _Resp([{"data": {"ok": True}}])

    _download_gql(
        session_post,
        [{"operationName": op_name, "variables": {}}],
        client_id="custom-client",
    )

    assert calls["headers"]["Client-ID"] == "custom-client"


def test_update_badge_info_merges_global_and_channel_badges() -> None:
    badge_info = {}
    subscriber_badge_info = {}

    def b64_id(set_id: str, version: str, channel_id: str) -> str:
        raw = f"{set_id};{version};{channel_id}".encode()
        return base64.b64encode(raw).decode()

    channel_badge = {"id": b64_id("subscriber", "12", "123"), "title": "Sub"}
    global_badge = {"id": b64_id("moderator", "1", ""), "title": "Mod"}
    calls = {}

    def download_gql_func(_session_post, ops, client_id=None):
        calls.setdefault("client_ids", []).append(client_id)
        op_name = ops[0]["operationName"]
        if op_name == "ChatList_Badges":
            return [
                {
                    "data": {
                        "badges": [channel_badge],
                        "user": {"broadcastBadges": []},
                    },
                },
            ]
        if op_name == "GlobalBadges":
            return [
                {
                    "data": {
                        "badges": [global_badge],
                        "user": {"broadcastBadges": []},
                    },
                },
            ]
        msg = f"Unexpected operationName: {op_name}"
        raise AssertionError(msg)

    # session_post isn't used by our stub download func.
    update_badge_info(
        session_post=lambda *a, **k: None,
        channel="xenova",
        download_gql_func=download_gql_func,
        badge_info=badge_info,
        subscriber_badge_info=subscriber_badge_info,
        client_id="custom-client",
    )

    assert calls["client_ids"] == ["custom-client", "custom-client"]

    assert ("moderator", "1") in badge_info
    assert badge_info[("moderator", "1")]["title"] == "Mod"

    assert "123" in subscriber_badge_info
    assert ("subscriber", "12") in subscriber_badge_info["123"]
    assert subscriber_badge_info["123"][("subscriber", "12")]["title"] == "Sub"


def test_get_user_videos_raises_user_not_found_on_empty_user_id() -> None:
    def download_gql_func(_session_post, _query):
        return [{"data": {"user": {"id": "", "videos": None}}}]

    gen = get_user_videos(
        session_post=lambda *a, **k: None,
        download_gql_func=download_gql_func,
        username="doesnotexist",
        limit=1,
    )

    try:
        next(gen)
        msg = "Expected UserNotFound"
        raise AssertionError(msg)
    except UserNotFound as exc:
        assert "doesnotexist" in str(exc)


def test_get_chat_messages_by_vod_id_uses_scalar_offset_for_first_page() -> (
    None
):
    calls = {}

    def download_gql_func(query):
        calls["query"] = query
        return [{"data": {"video": {"comments": {"edges": []}}}}]

    comments, info = get_chat_messages_by_vod_id(
        session_post=lambda *a, **k: None,
        download_gql_func=download_gql_func,
        vod_id="vod123",
        cursor=None,
        content_offset_seconds=12.5,
    )

    assert comments == {"edges": []}
    assert info == {"comments": {"edges": []}}
    assert calls["query"][0]["variables"]["contentOffsetSeconds"] == 12.5
    assert "cursor" not in calls["query"][0]["variables"]


def test_get_chat_messages_by_vod_id_prefers_cursor_over_offset() -> None:
    calls = {}

    def download_gql_func(query):
        calls["query"] = query
        return [{"data": {"video": {"comments": {"edges": []}}}}]

    get_chat_messages_by_vod_id(
        session_post=lambda *a, **k: None,
        download_gql_func=download_gql_func,
        vod_id="vod123",
        cursor="cursor123",
        content_offset_seconds=99.0,
    )

    assert calls["query"][0]["variables"]["cursor"] == "cursor123"
    assert "contentOffsetSeconds" not in calls["query"][0]["variables"]


def test_benign_unmatched_irc_buffer_detection_suppresses_join_part_ping_numeric() -> (
    None
):
    readbuffer = (
        "PING :tmi.twitch.tv\r\n"
        "PONG :tmi.twitch.tv\r\n"
        ":tmi.twitch.tv CAP * ACK :twitch.tv/tags twitch.tv/commands twitch.tv/membership\r\n"
        ":tmi.twitch.tv 001 justinfan67420 :Welcome, GLHF!\r\n"
        ":justinfan67420.tmi.twitch.tv 353 justinfan67420 = #idubbbz :foo bar baz\r\n"
        ":user!user@user.tmi.twitch.tv JOIN #idubbbz\r\n"
        ":user!user@user.tmi.twitch.tv PART #idubbbz\r\n"
    )
    assert _is_benign_unmatched_irc_buffer(readbuffer) is True


def test_benign_unmatched_irc_buffer_detection_keeps_unknown_lines() -> None:
    readbuffer = "THIS IS NOT A TWITCH IRC HOUSEKEEPING LINE\r\n"
    assert _is_benign_unmatched_irc_buffer(readbuffer) is False


# ---------------------------------------------------------------------------
# update_badge_info: graceful degradation with specific exceptions (Fix 4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        RequestException("connection refused"),
        ValueError("bad base64"),
        KeyError("missing key"),
    ],
)
def test_update_badge_info_logs_warning_on_network_error(exc, caplog) -> None:
    """update_badge_info should log a warning and not raise on expected
    errors.
    """
    import logging

    def download_gql_func(_session_post, _ops, client_id=None) -> NoReturn:
        raise exc

    with caplog.at_level(logging.WARNING, logger="chat_downloader"):
        update_badge_info(
            session_post=lambda *a, **k: None,
            channel="testchan",
            download_gql_func=download_gql_func,
            badge_info={},
            subscriber_badge_info={},
        )

    assert any("testchan" in r.message for r in caplog.records)
    assert any("Continuing without badges" in r.message for r in caplog.records)


def test_update_badge_info_does_not_swallow_unexpected_exceptions() -> None:
    """Unexpected exception types should propagate (not caught by narrowed
    clause).
    """

    class _WeirdError(RuntimeError):
        pass

    def download_gql_func(_session_post, _ops, client_id=None) -> NoReturn:
        msg = "unexpected"
        raise _WeirdError(msg)

    with pytest.raises(_WeirdError):
        update_badge_info(
            session_post=lambda *a, **k: None,
            channel="testchan",
            download_gql_func=download_gql_func,
            badge_info={},
            subscriber_badge_info={},
        )
