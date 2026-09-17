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


@pytest.mark.parametrize(("cursor", "offset", "variables", "edges", "metadata"),[
        ("cursor-1", 99, {"cursor": "cursor-1"}, [], {"id": "vod"}),
        (None, None, {"contentOffsetSeconds": 0}, [1], {}),
    ],
    ids=["cursor-precedes-offset", "missing-offset-defaults-to-zero"],
)
def test_replay_request_position(cursor, offset, variables, edges, metadata) -> None:
    expected = {"edges": edges}
    download = Mock(return_value=_video_payload(expected, **metadata))
    comments, info = _replay(download, cursor=cursor, offset=offset)
    download.assert_called_once_with(
        [
            {
                "operationName": "VideoCommentsByOffsetOrCursor",
                "variables": {"videoID": "123", **variables},
            }
        ]
    )
    assert comments == expected
    assert info == {"comments": expected, **metadata}


@pytest.mark.parametrize(
    "payload",
    [[], [{}], [{"data": {}}], [{"data": {"video": None}}], _video_payload(None)],
    ids=["empty", "missing-data", "missing-video", "null-video", "null-comments"],
)
def test_replay_returns_none_for_malformed_or_empty_payloads(payload) -> None:
    assert _replay(Mock(return_value=payload), offset=1.5) == (None, None)


@pytest.mark.parametrize(("edges", "cursor", "offset", "variables", "has_next"),[
        (
            [{"cursor": "mobile-cursor", "node": {}}],
            "legacy-cursor",
            4.5,
            {"after": "legacy-cursor"},
            True,
        ),
        ([{"cursor": "", "node": {}}], None, 4.9, {"contentOffsetSeconds": 4}, False),
        ([], None, 0, {"contentOffsetSeconds": 0}, False),
    ],
    ids=["continuing-cursor", "empty-cursor-terminal", "empty-edges-terminal"],
)
def test_mobile_replay_normalizes_page(
    edges, cursor, offset, variables, has_next
) -> None:
    download = Mock(
        side_effect=[
            _PersistedQueryUnavailable("rotated"),
            _video_payload({"edges": edges}),
        ]
    )
    comments, _info = _replay(download, cursor=cursor, offset=offset)
    assert download.call_args.args[0] == [
        {
            "operationName": "VideoCommentsQuery",
            "variables": {"vodId": "123", **variables},
        }
    ]
    assert comments == {"edges": edges, "pageInfo": {"hasNextPage": has_next}}


def test_mobile_replay_returns_none_for_malformed_payload() -> None:
    download = Mock(
        side_effect=[_PersistedQueryUnavailable("rotated"), _video_payload(None)]
    )
    assert _replay(download) == (None, None)


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
