# SPDX-License-Identifier: MIT

from __future__ import annotations

from functools import partial

from chat_downloader.sites.twitch.graphql_client import (
    _download_gql,
    _PersistedQueryUnavailable,
)
from chat_downloader.sites.twitch.replay_transport import (
    get_chat_messages_by_vod_id,
)


def test_get_chat_messages_by_vod_id_uses_cursor_when_present() -> None:
    captured = []

    def fake_download(query):
        captured.append(query)
        return [{"data": {"video": {"comments": {"edges": []}, "id": "vod"}}}]

    comments, info = get_chat_messages_by_vod_id(
        session_post=None,
        download_gql_func=fake_download,
        vod_id="123",
        cursor="cursor-1",
        content_offset_seconds=99,
    )

    assert captured == [
        [
            {
                "operationName": "VideoCommentsByOffsetOrCursor",
                "variables": {"videoID": "123", "cursor": "cursor-1"},
            },
        ],
    ]
    assert comments == {"edges": []}
    assert info == {"comments": {"edges": []}, "id": "vod"}


def test_get_chat_messages_by_vod_id_uses_zero_offset_without_cursor() -> None:
    captured = []

    def fake_download(query):
        captured.append(query)
        return [{"data": {"video": {"comments": {"edges": [1]}}}}]

    comments, info = get_chat_messages_by_vod_id(
        session_post=None,
        download_gql_func=fake_download,
        vod_id="456",
        cursor=None,
        content_offset_seconds=None,
    )

    assert captured[0][0]["variables"] == {
        "videoID": "456",
        "contentOffsetSeconds": 0,
    }
    assert comments == {"edges": [1]}
    assert info == {"comments": {"edges": [1]}}


def test_get_chat_messages_by_vod_id_returns_none_for_malformed_or_empty_payloads() -> (
    None
):
    bad_payloads = [
        [],
        [{}],
        [{"data": {}}],
        [{"data": {"video": None}}],
        [{"data": {"video": {"comments": None}}}],
    ]

    for payload in bad_payloads:
        comments, info = get_chat_messages_by_vod_id(
            session_post=None,
            download_gql_func=lambda _query, payload=payload: payload,
            vod_id="789",
            cursor=None,
            content_offset_seconds=1.5,
        )
        assert comments is None
        assert info is None


def test_get_chat_messages_by_vod_id_falls_back_to_mobile_operation() -> None:
    captured = []

    def fake_download(query):
        captured.append(query)
        if len(captured) == 1:
            raise _PersistedQueryUnavailable("rotated")
        return [
            {
                "data": {
                    "video": {
                        "comments": {"edges": [{"cursor": "mobile-cursor", "node": {}}]}
                    }
                }
            }
        ]

    comments, _info = get_chat_messages_by_vod_id(
        session_post=None,
        download_gql_func=fake_download,
        vod_id="123",
        cursor="legacy-cursor",
        content_offset_seconds=4.5,
    )

    assert captured[1] == [
        {
            "operationName": "VideoCommentsQuery",
            "variables": {"vodId": "123", "after": "legacy-cursor"},
        }
    ]
    assert comments == {
        "edges": [{"cursor": "mobile-cursor", "node": {}}],
        "pageInfo": {"hasNextPage": True},
    }


def test_replay_fallback_composes_persisted_and_full_document_requests() -> None:
    payloads = iter(
        [
            [{"errors": [{"message": "PersistedQueryNotFound"}]}],
            [{"errors": [{"message": "Persisted query not found"}]}],
            [
                {
                    "data": {
                        "video": {
                            "comments": {
                                "edges": [{"cursor": "mobile-cursor", "node": {}}]
                            }
                        }
                    }
                }
            ],
        ]
    )
    requests = []

    class Response:
        def json(self):
            return next(payloads)

    def session_post(_url, json, headers):
        _ = headers
        requests.append(json)
        return Response()

    comments, _info = get_chat_messages_by_vod_id(
        session_post=session_post,
        download_gql_func=partial(_download_gql, session_post),
        vod_id="123",
        cursor="legacy-cursor",
        content_offset_seconds=4.5,
    )

    assert [request[0]["operationName"] for request in requests] == [
        "VideoCommentsByOffsetOrCursor",
        "VideoCommentsQuery",
        "VideoCommentsQuery",
    ]
    assert "extensions" in requests[0][0]
    assert "extensions" in requests[1][0]
    assert "extensions" not in requests[2][0]
    assert requests[2][0]["variables"] == {
        "vodId": "123",
        "after": "legacy-cursor",
    }
    assert comments == {
        "edges": [{"cursor": "mobile-cursor", "node": {}}],
        "pageInfo": {"hasNextPage": True},
    }


def test_mobile_replay_terminal_page_uses_empty_cursor() -> None:
    def fake_download(_query):
        raise _PersistedQueryUnavailable("rotated")

    calls = 0

    def fallback_download(query):
        nonlocal calls
        calls += 1
        if calls == 1:
            return fake_download(query)
        return [
            {"data": {"video": {"comments": {"edges": [{"cursor": "", "node": {}}]}}}}
        ]

    comments, _info = get_chat_messages_by_vod_id(
        session_post=None,
        download_gql_func=fallback_download,
        vod_id="123",
        cursor=None,
        content_offset_seconds=4.9,
    )

    assert comments is not None
    assert comments["pageInfo"] == {"hasNextPage": False}


def test_mobile_replay_returns_none_for_malformed_payload() -> None:
    calls = 0

    def fake_download(_query):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _PersistedQueryUnavailable("rotated")
        return [{"data": {"video": {"comments": None}}}]

    assert get_chat_messages_by_vod_id(
        session_post=None,
        download_gql_func=fake_download,
        vod_id="123",
        cursor=None,
        content_offset_seconds=0,
    ) == (None, None)


def test_mobile_replay_normalizes_empty_edges_as_terminal() -> None:
    calls = 0

    def fake_download(_query):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _PersistedQueryUnavailable("rotated")
        return [{"data": {"video": {"comments": {"edges": []}}}}]

    comments, _info = get_chat_messages_by_vod_id(
        session_post=None,
        download_gql_func=fake_download,
        vod_id="123",
        cursor=None,
        content_offset_seconds=0,
    )

    assert comments == {"edges": [], "pageInfo": {"hasNextPage": False}}
