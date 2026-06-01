# SPDX-License-Identifier: MIT

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


def test_get_chat_messages_by_vod_id_returns_none_for_malformed_or_empty_payloads() -> (  # noqa: E501
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
