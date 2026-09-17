# SPDX-License-Identifier: MIT

"""Twitch extractor response handling and request configuration."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest

from chat_downloader.errors import ParsingError, UserNotFound, VideoNotFound
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch.constants import CLIENT_ID, OPERATION_HASHES
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader


def _comment_edge(edge_type="VideoCommentEdge", node_type="Comment"):
    edge = {
        "cursor": "cursor1",
        "node": {
            "id": "msg1",
            "message": {"fragments": [{"text": "Valid message"}]},
            "commenter": {"login": "user1"},
        },
    }
    if edge_type is not None:
        edge["__typename"] = edge_type
    if node_type is not None:
        edge["node"]["__typename"] = node_type
    return edge


@pytest.fixture
def vod_page(monkeypatch):
    fetch = Mock(
        return_value=(
            {"edges": [], "pageInfo": {"hasNextPage": False}},
            {"creator": {"id": "channel123", "channel": {"id": "ch123"}}},
        )
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id",
        fetch,
    )
    return fetch


def _vod_messages():
    return list(
        TwitchChatDownloader()._get_chat_messages_by_vod_id(
            "vod123",
            {"max_attempts": 1, "message_groups": ["messages"]},
            100,
        )
    )


@pytest.mark.parametrize(
    ("edge_type", "node_type"),
    [
        ("VideoCommentEdge", "Comment"),
        (None, None),
        ("UnexpectedEdgeType", "Comment"),
        ("VideoCommentEdge", "UnexpectedNodeType"),
    ],
    ids=["valid", "missing-typenames", "invalid-edge", "invalid-node"],
)
def test_vod_typenames(vod_page, edge_type, node_type, monkeypatch) -> None:
    logger = Mock()
    monkeypatch.setattr("chat_downloader.sites.twitch.extractor.logger", logger)
    edge = _comment_edge(edge_type, node_type)
    edges = [edge]
    if edge_type == "UnexpectedEdgeType" or node_type == "UnexpectedNodeType":
        edge["node"]["id"] = "invalid"
        edge["node"]["message"]["fragments"][0]["text"] = "Should be skipped"
        edges.append(_comment_edge())
    vod_page.return_value[0]["edges"] = edges
    assert [message["message"] for message in _vod_messages()] == ["Valid message"]
    unexpected = {edge_type, node_type} & {"UnexpectedEdgeType", "UnexpectedNodeType"}
    if unexpected:
        assert any(
            value in str(call)
            for value in unexpected
            for call in logger.debug.call_args_list
        )


def test_vod_channel_not_found(vod_page) -> None:
    vod_page.return_value[1]["creator"]["id"] = ""
    with pytest.raises(UserNotFound, match="vod123"):
        _vod_messages()


@pytest.mark.parametrize("method", ["_download_base_gql", "_download_gql"])
@pytest.mark.parametrize("client_id", [None, "client-123"])
def test_extractor_gql_request_headers(monkeypatch, method, client_id) -> None:
    options = {} if client_id is None else {"twitch_client_id": client_id}
    downloader = TwitchChatDownloader(**options)
    response = Mock(status_code=200, text="")
    response.json.return_value = [{"data": {}}]
    post = Mock(return_value=response)
    monkeypatch.setattr(downloader, "_session_post", post)
    monkeypatch.setattr(downloader, "get_cookie_value", lambda _name: "test-token")
    ops = [{"operationName": next(iter(OPERATION_HASHES)), "variables": {}}]
    assert getattr(downloader, method)(ops) == [{"data": {}}]
    headers = post.call_args.kwargs["headers"]
    assert headers["Client-ID"] == (client_id or CLIENT_ID)
    assert headers["Authorization"] == "OAuth test-token"


@pytest.mark.parametrize("client_id", [None, "client-123"])
def test_extractor_gql_records_optional_degradation(monkeypatch, client_id) -> None:
    downloader = TwitchChatDownloader(twitch_client_id=client_id)
    payload = [
        {
            "data": {"user": {}},
            "errors": [
                {"message": "service error", "path": ["user", "primaryTeam"]},
            ],
        }
    ]
    response = Mock(status_code=200, text="")
    response.json.return_value = payload
    monkeypatch.setattr(downloader, "_session_post", Mock(return_value=response))
    callback = Mock()
    result = downloader._download_gql(
        [{"operationName": next(iter(OPERATION_HASHES)), "variables": {}}],
        record_optional_degradation=callback,
    )
    assert result == payload
    callback.assert_called_once_with()


def test_badge_refresh_uses_configured_client_id(monkeypatch) -> None:
    downloader = TwitchChatDownloader(twitch_client_id="client-123")
    response = Mock(status_code=200, text="")
    response.json.return_value = [
        {"data": {"badges": [], "user": {"broadcastBadges": []}}}
    ]
    post = Mock(return_value=response)
    monkeypatch.setattr(downloader, "_session_post", post)
    downloader._update_badge_info("channel-name")
    assert post.call_count == 2
    assert all(
        call.kwargs["headers"]["Client-ID"] == "client-123"
        for call in post.call_args_list
    )


def test_twitch_badge_refresh_reuses_known_channel_id(monkeypatch) -> None:
    downloader = TwitchChatDownloader()
    downloader._session_post = object()
    calls = []
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.update_badge_info",
        lambda *args, **kwargs: calls.append(kwargs),
    )

    downloader._update_badge_info("CaseOh_", "123")
    downloader._update_badge_info("caseoh_")

    assert calls == [{"channel_id": "123"}, {"channel_id": "123"}]


def test_get_user_clips_breaks_when_clips_none() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.sites.twitch.discovery import get_user_clips

    def mock_download_gql(session_post, query):
        return [{"data": {"user": {"clips": None}}}]

    results = list(get_user_clips(MagicMock(), mock_download_gql, "testuser"))
    assert results == []


def test_twitch_contains_challenge_text_non_string() -> None:
    from chat_downloader.sites.twitch.graphql_client import (
        _contains_challenge_text,
    )

    assert _contains_challenge_text(None) is False
    assert _contains_challenge_text(42) is False
    assert _contains_challenge_text([]) is False


def test_download_base_gql_adds_auth_header() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.sites.twitch.graphql_client import _download_base_gql

    captured: dict = {}
    auth_value = "test-value"

    def mock_post(url, json, headers):
        captured["headers"] = headers
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = []
        return response

    _download_base_gql(mock_post, [], auth_token=auth_value)
    assert captured["headers"].get("Authorization") == f"OAuth {auth_value}"


def test_describe_operation_names_empty() -> None:
    from chat_downloader.sites.twitch.graphql_client import (
        _describe_operation_names,
    )

    assert _describe_operation_names(None) == "unknown operation"
    assert _describe_operation_names([]) == "unknown operation"
    assert _describe_operation_names(["One", "Two"]) == "One, Two"


def test_handle_gql_errors_empty_list_is_noop() -> None:
    from chat_downloader.sites.twitch.graphql_client import _handle_gql_errors

    assert _handle_gql_errors([]) is False


def test_download_gql_dict_response_with_errors() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.errors import ParsingError
    from chat_downloader.sites.twitch.constants import OPERATION_HASHES
    from chat_downloader.sites.twitch.graphql_client import _download_gql

    op_name = next(iter(OPERATION_HASHES))

    def mock_post(url, json, headers):
        response = MagicMock()
        response.status_code = 200
        response.json.return_value = {
            "errors": [{"message": "some generic field error", "path": []}]
        }
        return response

    ops = [{"operationName": op_name, "variables": {}}]
    with pytest.raises(ParsingError):
        _download_gql(mock_post, ops)


def test_close_connection_swallows_oserror() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.sites.twitch.irc_transport import TwitchChatIRC

    irc = TwitchChatIRC.__new__(TwitchChatIRC)
    mock_socket = MagicMock()
    irc.socket = mock_socket
    irc.current_channel = None

    # sendall raises OSError → should be swallowed by except OSError: pass
    mock_socket.sendall.side_effect = OSError("connection reset")
    irc.close_connection()  # Must not re-raise


def test_irc_buffer_overflow_truncation() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.sites.twitch.irc_transport import (
        _READBUFFER_MAX_BYTES,
        get_chat_messages_by_stream_id,
    )

    mock_irc = MagicMock()
    # First recv: oversized buffer → triggers overflow truncation
    # Second recv: empty → raises ConnectionError to stop the loop
    mock_irc.recv.side_effect = ["x" * (_READBUFFER_MAX_BYTES + 100), ""]

    gen = get_chat_messages_by_stream_id(mock_irc, "channel", {"max_attempts": 1})
    with pytest.raises(ConnectionError):
        for _ in gen:
            pass


def test_parse_irc_int_flag_returns_default_for_other_types() -> None:
    from chat_downloader.sites.twitch.parsing.message_irc_resolve import (
        _parse_irc_int_flag,
    )

    assert _parse_irc_int_flag(None, default=42) == 42
    assert _parse_irc_int_flag([], default=-1) == -1
    assert _parse_irc_int_flag(3.14, default=0) == 0


def test_iter_vod_chat_messages_with_offset_branch() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.twitch.replay_service import (
        iter_vod_chat_messages,
    )

    mock_downloader = MagicMock()
    request = ChatRequest(url="https://www.twitch.tv/videos/12345")

    # _fetch_vod_page returns empty comments → loop breaks immediately,
    # but offset-branch code (lines 153-154) already ran before the loop.
    with patch(
        "chat_downloader.sites.twitch.replay_service._fetch_vod_page",
        return_value=(None, {}),
    ):
        result = list(
            iter_vod_chat_messages(
                mock_downloader,
                "12345",
                request,
                max_duration=None,
                offset=10.0,
            )
        )
    assert result == []


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"errors": [{"message": "clip not found"}]}, VideoNotFound),
        ({"data": {"clip": None}}, ParsingError),
    ],
    ids=["graphql-error", "missing-clip"],
)
def test_clip_response_errors(payload, error) -> None:
    from chat_downloader.sites.twitch.replay_service import get_chat_by_clip_id

    downloader = MagicMock()
    downloader._download_base_gql.return_value = payload
    request = ChatRequest(url="https://www.twitch.tv/clip/test")
    with pytest.raises(error):
        get_chat_by_clip_id(downloader, "test_clip", request)


def test_extractor_routing_wrappers_delegate(monkeypatch) -> None:
    downloader = TwitchChatDownloader()
    request = ChatRequest(url="https://www.twitch.tv/example")

    class Match:
        def __init__(self, value: str) -> None:
            self.value = value

        def group(self, _name: str) -> str:
            return self.value

    for name, item_id, public_method, wrapper_name in [
        ("build_vod_chat", "v1", "get_chat_by_vod_id", "_get_chat_by_vod_id"),
        ("build_clip_chat", "c1", "get_chat_by_clip_id", "_get_chat_by_clip_id"),
        ("build_stream_chat", "s1", "get_chat_by_stream_id", "_get_chat_by_stream_id"),
    ]:
        seen: dict[str, object] = {}

        def fake_build(owner, seen_id, params, _seen=seen):
            _seen.update(owner=owner, item_id=seen_id, params=params)
            return "chat"

        monkeypatch.setattr(
            f"chat_downloader.sites.twitch.extractor.{name}",
            fake_build,
        )
        assert getattr(downloader, public_method)(item_id, request) == "chat"
        assert seen["item_id"] == item_id
        assert seen["params"] is request
        match = Match(item_id + "2")
        assert getattr(downloader, wrapper_name)(match, request) == "chat"
        assert seen["item_id"] == match.value


def test_extractor_generate_urls_delegates_to_url_generation(monkeypatch) -> None:
    downloader = TwitchChatDownloader()
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.generate_twitch_urls",
        lambda owner, livestream, vod, clip: iter(
            [owner._NAME, livestream, vod, clip],
        ),
    )
    assert list(downloader.generate_urls(1, 2, 3)) == [
        "twitch.tv",
        1,
        2,
        3,
    ]
