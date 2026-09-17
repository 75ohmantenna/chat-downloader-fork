# SPDX-License-Identifier: MIT

from __future__ import annotations

import base64
import hashlib
from contextlib import nullcontext
from unittest.mock import Mock

import pytest
from requests.exceptions import RequestException

from chat_downloader.errors import LoginRequired, ParsingError, UserNotFound
from chat_downloader.sites.twitch.badge_client import update_badge_info
from chat_downloader.sites.twitch.constants import (
    CLIENT_ID,
    GQL_API_URL,
    OPERATION_HASHES,
)
from chat_downloader.sites.twitch.discovery import get_user_videos
from chat_downloader.sites.twitch.graphql_client import (
    _FULL_QUERY_DOCUMENTS,
    _download_gql,
    _handle_result_errors,
)
from chat_downloader.sites.twitch.irc_transport import (
    _is_benign_unmatched_irc_buffer,
)
from chat_downloader.sites.twitch.replay_transport import (
    get_chat_messages_by_vod_id,
)


def _post(*payloads):
    return Mock(
        side_effect=[
            Mock(status_code=200, text="", json=Mock(return_value=p)) for p in payloads
        ]
    )


def _errors(*messages):
    return {"errors": [{"message": message} for message in messages]}


def _optional_metadata_error() -> dict[str, object]:
    return {"message": "service error", "path": ["user", "primaryTeam"]}


@pytest.mark.parametrize(
    ("payloads", "count", "error"),
    [
        ([{"data": {"user": {}}}], 0, None),
        (
            [{"errors": [{"message": "service error", "path": ["video", "comments"]}]}],
            0,
            None,
        ),
        ([{"errors": _optional_metadata_error()}], 0, None),
        (
            [
                {
                    "errors": [
                        _optional_metadata_error(),
                        _optional_metadata_error(),
                        {"message": "service error", "path": ["video", "comments"]},
                    ]
                },
                {"errors": [_optional_metadata_error()]},
            ],
            2,
            None,
        ),
        (
            [{"errors": [_optional_metadata_error()]}, _errors("Unauthorized")],
            0,
            LoginRequired,
        ),
        (
            [
                {"errors": [_optional_metadata_error()]},
                _errors("PersistedQueryNotFound"),
            ],
            0,
            ParsingError,
        ),
    ],
    ids=[
        "clean",
        "warning",
        "malformed-errors",
        "once-per-item",
        "later-auth-error",
        "later-hash-error",
    ],
)
def test_result_error_degradation_is_atomic(payloads, count, error):
    record = Mock()
    with pytest.raises(error) if error else nullcontext():
        _handle_result_errors(
            payloads, ["StreamMetadata"], record_optional_degradation=record
        )
    assert record.call_count == count
    assert all(call.args == () and call.kwargs == {} for call in record.mock_calls)


def test_download_gql_does_not_count_degradation_from_rejected_hash_response() -> None:
    post = _post(
        [{"errors": [_optional_metadata_error()]}, _errors("PersistedQueryNotFound")],
        [{"data": {"user": {}}}],
    )
    record_degradation = Mock()

    result = _download_gql(
        post,
        [{"operationName": "StreamMetadata", "variables": {}}],
        record_optional_degradation=record_degradation,
    )

    assert result == [{"data": {"user": {}}}]
    record_degradation.assert_not_called()


@pytest.mark.parametrize("client_id", [None, "custom-client"])
def test_download_gql_adds_hash_without_mutating_input(client_id) -> None:
    op_name = next(iter(OPERATION_HASHES.keys()))
    ops = [{"operationName": op_name, "variables": {"x": 1}}]
    ops_snapshot = [{"operationName": op_name, "variables": {"x": 1}}]

    session_post = _post([{"data": {"ok": True}}])
    kwargs = {} if client_id is None else {"client_id": client_id}
    out = _download_gql(session_post, ops, **kwargs)
    calls = session_post.call_args.kwargs
    assert out == [{"data": {"ok": True}}]

    assert session_post.call_args.args == (GQL_API_URL,)
    assert calls["headers"]["Client-ID"] == (client_id or CLIENT_ID)
    assert (
        calls["json"][0]["extensions"]["persistedQuery"]["sha256Hash"]
        == OPERATION_HASHES[op_name]
    )
    assert ops == ops_snapshot, "Input operations must not be mutated in-place"


def test_download_gql_maps_mobile_global_badge_alias_to_wire_operation() -> None:
    session_post = _post([{"data": {"badges": []}}])
    _download_gql(session_post, [{"operationName": "GlobalBadgesMobile"}])

    operation = session_post.call_args.kwargs["json"][0]
    assert operation["operationName"] == "GlobalBadges"
    assert (
        operation["extensions"]["persistedQuery"]["sha256Hash"]
        == (OPERATION_HASHES["GlobalBadgesMobile"])
    )


@pytest.mark.parametrize(
    ("operation_name", "variables", "fallback_variables"),
    [
        (
            "StreamMetadata",
            {"channelLogin": "caseoh_", "includeIsDJ": True},
            {"channelLogin": "caseoh_"},
        ),
        (
            "VideoMetadata",
            {"channelLogin": "", "videoID": "123"},
            {"videoID": "123"},
        ),
        (
            "VideoCommentsQuery",
            {"vodId": "123", "after": "cursor"},
            {"vodId": "123", "after": "cursor"},
        ),
    ],
)
def test_download_gql_retries_supported_hash_failure_with_full_document(
    operation_name: str,
    variables: dict[str, object],
    fallback_variables: dict[str, object],
) -> None:
    session_post = _post([_errors("PersistedQueryNotFound")], [{"data": {"ok": True}}])

    result = _download_gql(
        session_post,
        [{"operationName": operation_name, "variables": variables}],
    )

    requests = [call.kwargs["json"] for call in session_post.call_args_list]
    assert result == [{"data": {"ok": True}}]
    assert (
        requests[0][0]["extensions"]["persistedQuery"]["sha256Hash"]
        == (OPERATION_HASHES[operation_name])
    )
    assert requests[1] == [
        {
            "operationName": operation_name,
            "variables": fallback_variables,
            "query": _FULL_QUERY_DOCUMENTS[operation_name],
        }
    ]


def test_mobile_replay_document_matches_apk_persisted_hash() -> None:
    query_hash = hashlib.sha256(
        _FULL_QUERY_DOCUMENTS["VideoCommentsQuery"].encode()
    ).hexdigest()

    assert query_hash == OPERATION_HASHES["VideoCommentsQuery"]


@pytest.mark.parametrize(
    ("operation", "messages", "error"),
    [
        ("GlobalBadges", ["PersistedQueryNotFound"], ParsingError),
        (
            "StreamMetadata",
            ["Persisted query not found", "Unauthorized"],
            LoginRequired,
        ),
        ("VideoMetadata", ["Unauthorized"], LoginRequired),
    ],
    ids=["unsupported-fallback", "fallback-auth-error", "non-hash-error"],
)
def test_download_gql_limits_fallback_and_maps_errors(operation, messages, error):
    session_post = _post(*[[_errors(message)] for message in messages])
    with pytest.raises(error):
        _download_gql(session_post, [{"operationName": operation, "variables": {}}])
    assert session_post.call_count == len(messages)


def test_update_badge_info_merges_global_and_channel_badges() -> None:
    badge_info = {}
    subscriber_badge_info = {}

    def badge(set_id, version, channel_id, title):
        raw = f"{set_id};{version};{channel_id}".encode()
        return {"id": base64.b64encode(raw).decode(), "title": title}

    badges = {
        "ChatList_Badges": badge("subscriber", "12", "123", "Sub"),
        "GlobalBadges": badge("moderator", "1", "", "Mod"),
    }

    def download(_session_post, ops, client_id=None):
        return [
            {
                "data": {
                    "badges": [badges[ops[0]["operationName"]]],
                    "user": {"broadcastBadges": []},
                }
            }
        ]

    download_gql_func = Mock(side_effect=download)

    update_badge_info(
        session_post=lambda *a, **k: None,
        channel="xenova",
        download_gql_func=download_gql_func,
        badge_info=badge_info,
        subscriber_badge_info=subscriber_badge_info,
        client_id="custom-client",
    )

    assert [call.kwargs["client_id"] for call in download_gql_func.call_args_list] == [
        "custom-client",
        "custom-client",
    ]
    assert badge_info[("moderator", "1")]["title"] == "Mod"
    assert subscriber_badge_info["123"][("subscriber", "12")]["title"] == "Sub"


def test_get_user_videos_raises_user_not_found_on_empty_user_id() -> None:
    gen = get_user_videos(
        session_post=Mock(),
        download_gql_func=Mock(
            return_value=[{"data": {"user": {"id": "", "videos": None}}}]
        ),
        username="doesnotexist",
        limit=1,
    )

    with pytest.raises(UserNotFound, match="doesnotexist"):
        next(gen)


@pytest.mark.parametrize(
    ("cursor", "offset", "expected"),
    [
        (None, 12.5, {"contentOffsetSeconds": 12.5}),
        ("cursor123", 99.0, {"cursor": "cursor123"}),
    ],
)
def test_get_chat_messages_by_vod_id_selects_cursor_or_offset(cursor, offset, expected):
    download = Mock(return_value=[{"data": {"video": {"comments": {"edges": []}}}}])
    comments, info = get_chat_messages_by_vod_id(
        session_post=Mock(),
        download_gql_func=download,
        vod_id="vod123",
        cursor=cursor,
        content_offset_seconds=offset,
    )
    assert comments == {"edges": []}
    assert info == {"comments": {"edges": []}}
    assert download.call_args.args[0][0]["variables"] == {
        "videoID": "vod123",
        **expected,
    }


@pytest.mark.parametrize(
    ("readbuffer", "benign"),
    [
        (
            (
                "PING :tmi.twitch.tv\r\n"
                "PONG :tmi.twitch.tv\r\n"
                ":tmi.twitch.tv CAP * ACK :twitch.tv/tags twitch.tv/commands\r\n"
                ":tmi.twitch.tv 001 justinfan67420 :Welcome, GLHF!\r\n"
                ":justinfan67420.tmi.twitch.tv 353 justinfan67420 "
                "= #idubbbz :foo bar baz\r\n"
                ":user!user@user.tmi.twitch.tv JOIN #idubbbz\r\n"
                ":user!user@user.tmi.twitch.tv PART #idubbbz\r\n"
            ),
            True,
        ),
        ("THIS IS NOT A TWITCH IRC HOUSEKEEPING LINE\r\n", False),
    ],
)
def test_benign_unmatched_irc_buffer(readbuffer, benign):
    assert _is_benign_unmatched_irc_buffer(readbuffer) is benign


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RequestException("connection refused"), None),
        (ValueError("bad base64"), None),
        (KeyError("missing key"), None),
        (RuntimeError("unexpected"), RuntimeError),
    ],
)
def test_badge_errors_degrade_only_for_expected_exceptions(exc, expected, caplog):
    with pytest.raises(expected) if expected else nullcontext():
        update_badge_info(
            session_post=Mock(),
            channel="testchan",
            download_gql_func=Mock(side_effect=exc),
            badge_info={},
            subscriber_badge_info={},
        )
    if expected is None:
        assert any("testchan" in record.message for record in caplog.records)
        assert any("Continuing without badges" in r.message for r in caplog.records)
