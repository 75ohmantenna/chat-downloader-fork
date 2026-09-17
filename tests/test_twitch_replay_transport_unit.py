# SPDX-License-Identifier: MIT

from __future__ import annotations

from functools import partial
from unittest.mock import Mock

import pytest

from chat_downloader.sites.twitch.graphql_client import (
    _download_gql,
    _PersistedQueryUnavailable,
)
from chat_downloader.sites.twitch.replay_transport import get_chat_messages_by_vod_id


def _video_payload(comments, **metadata):
    return [{"data": {"video": {"comments": comments, **metadata}}}]


def _replay(download, *, cursor=None, offset=0, session_post=None):
    return get_chat_messages_by_vod_id(
        session_post=session_post,
        download_gql_func=download,
        vod_id="123",
        cursor=cursor,
        content_offset_seconds=offset,
    )


@pytest.mark.parametrize(
    ("mobile", "cursor", "offset", "variables", "edges", "metadata", "has_next"),
    [
        (False, "cursor-1", 99, {"cursor": "cursor-1"}, [], {"id": "vod"}, None),
        (False, None, None, {"contentOffsetSeconds": 0}, [1], {}, None),
        (
            True,
            "legacy-cursor",
            4.5,
            {"after": "legacy-cursor"},
            [{"cursor": "mobile-cursor", "node": {}}],
            {},
            True,
        ),
        (
            True,
            None,
            4.9,
            {"contentOffsetSeconds": 4},
            [{"cursor": "", "node": {}}],
            {},
            False,
        ),
        (True, None, 0, {"contentOffsetSeconds": 0}, [], {}, False),
    ],
    ids=[
        "cursor-precedes-offset",
        "missing-offset-defaults-to-zero",
        "continuing-cursor",
        "empty-cursor-terminal",
        "empty-edges-terminal",
    ],
)
def test_replay_request_position(
    mobile, cursor, offset, variables, edges, metadata, has_next
) -> None:
    expected = {"edges": edges}
    replies = [_video_payload(expected, **metadata)]
    if mobile:
        replies.insert(0, _PersistedQueryUnavailable("rotated"))
    download = Mock(side_effect=replies)
    comments, info = _replay(download, cursor=cursor, offset=offset)
    assert download.call_count == (2 if mobile else 1)
    assert download.call_args.args[0] == [
        {
            "operationName": (
                "VideoCommentsQuery" if mobile else "VideoCommentsByOffsetOrCursor"
            ),
            "variables": {"vodId" if mobile else "videoID": "123", **variables},
        }
    ]
    if mobile:
        assert comments == {"edges": edges, "pageInfo": {"hasNextPage": has_next}}
    else:
        assert comments == expected
        assert info == {"comments": expected, **metadata}


@pytest.mark.parametrize("mobile", [False, True])
@pytest.mark.parametrize(
    "payload",
    [
        [],
        [{}],
        [{"data": {}}],
        [{"data": {"video": None}}],
        _video_payload(None),
    ],
    ids=["empty", "missing-data", "missing-video", "null-video", "null-comments"],
)
def test_replay_returns_none_for_malformed_or_empty_payloads(payload, mobile):
    responses = (
        [_PersistedQueryUnavailable("rotated"), payload] if mobile else [payload]
    )
    assert _replay(Mock(side_effect=responses), offset=1.5) == (None, None)


def test_replay_fallback_composes_persisted_and_full_document_requests() -> None:
    edges = [{"cursor": "mobile-cursor", "node": {}}]
    payloads = [
        [{"errors": [{"message": "PersistedQueryNotFound"}]}],
        [{"errors": [{"message": "Persisted query not found"}]}],
        _video_payload({"edges": edges}),
    ]
    session_post = Mock(
        side_effect=[
            Mock(status_code=200, text="", json=Mock(return_value=p)) for p in payloads
        ],
    )
    comments, _info = _replay(
        partial(_download_gql, session_post),
        session_post=session_post,
        cursor="legacy-cursor",
        offset=4.5,
    )
    requests = [call.kwargs["json"][0] for call in session_post.call_args_list]
    assert [request["operationName"] for request in requests] == [
        "VideoCommentsByOffsetOrCursor",
        "VideoCommentsQuery",
        "VideoCommentsQuery",
    ]
    assert ["extensions" in request for request in requests] == [True, True, False]
    assert requests[2]["variables"] == {"vodId": "123", "after": "legacy-cursor"}
    assert comments == {"edges": edges, "pageInfo": {"hasNextPage": True}}
