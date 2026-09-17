# SPDX-License-Identifier: MIT

"""Twitch extractor response handling and request configuration."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest

from chat_downloader.errors import ParsingError, UserNotFound, VideoNotFound
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch import graphql_client as gql
from chat_downloader.sites.twitch.constants import CLIENT_ID, OPERATION_HASHES
from chat_downloader.sites.twitch.discovery import get_user_clips
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader
from chat_downloader.sites.twitch.irc_transport import (
    _READBUFFER_MAX_BYTES,
    TwitchChatIRC,
    get_chat_messages_by_stream_id,
)
from chat_downloader.sites.twitch.parsing.message_irc_resolve import _parse_irc_int_flag
from chat_downloader.sites.twitch.replay_service import (
    get_chat_by_clip_id,
    iter_vod_chat_messages,
)


def _post(payload):
    response = Mock(status_code=200, text="")
    response.json.return_value = payload
    return Mock(return_value=response)


def _operations():
    return [{"operationName": next(iter(OPERATION_HASHES)), "variables": {}}]


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
        "chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id", fetch
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
)
def test_vod_typenames(vod_page, edge_type, node_type, monkeypatch):
    logger = Mock()
    monkeypatch.setattr("chat_downloader.sites.twitch.extractor.logger", logger)
    edge = _comment_edge(edge_type, node_type)
    unexpected = {edge_type, node_type} & {"UnexpectedEdgeType", "UnexpectedNodeType"}
    if unexpected:
        edge["node"]["id"] = "invalid"
        edge["node"]["message"]["fragments"][0]["text"] = "Should be skipped"
    vod_page.return_value[0]["edges"] = [edge] + (
        [_comment_edge()] if unexpected else []
    )
    assert [message["message"] for message in _vod_messages()] == ["Valid message"]
    if unexpected:
        assert any(
            value in str(call)
            for value in unexpected
            for call in logger.debug.call_args_list
        )


def test_vod_channel_not_found(vod_page):
    vod_page.return_value[1]["creator"]["id"] = ""
    with pytest.raises(UserNotFound, match="vod123"):
        _vod_messages()


@pytest.mark.parametrize(
    ("method", "degraded"),
    [("_download_base_gql", False), ("_download_gql", False), ("_download_gql", True)],
)
@pytest.mark.parametrize("client_id", [None, "client-123"])
def test_extractor_gql_headers_and_degradation(
    monkeypatch, method, degraded, client_id
):
    downloader = TwitchChatDownloader(
        **({} if client_id is None else {"twitch_client_id": client_id})
    )
    payload = [{"data": {"user": {}}}]
    callback = Mock()
    if degraded:
        payload[0]["errors"] = [
            {"message": "service error", "path": ["user", "primaryTeam"]}
        ]
    post = _post(payload)
    monkeypatch.setattr(downloader, "_session_post", post)
    monkeypatch.setattr(downloader, "get_cookie_value", lambda _name: "test-token")
    kwargs = {"record_optional_degradation": callback} if degraded else {}
    assert getattr(downloader, method)(_operations(), **kwargs) == payload
    headers = post.call_args.kwargs["headers"]
    assert headers["Client-ID"] == (client_id or CLIENT_ID)
    assert headers["Authorization"] == "OAuth test-token"
    assert callback.call_count == int(degraded)


def test_badge_refresh_uses_configured_client_id(monkeypatch):
    downloader = TwitchChatDownloader(twitch_client_id="client-123")
    post = _post([{"data": {"badges": [], "user": {"broadcastBadges": []}}}])
    monkeypatch.setattr(downloader, "_session_post", post)
    downloader._update_badge_info("channel-name")
    assert post.call_count == 2
    assert all(
        call.kwargs["headers"]["Client-ID"] == "client-123"
        for call in post.call_args_list
    )


def test_twitch_badge_refresh_reuses_known_channel_id(monkeypatch):
    downloader = TwitchChatDownloader()
    downloader._session_post = object()
    update = Mock()
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.update_badge_info", update
    )
    downloader._update_badge_info("CaseOh_", "123")
    downloader._update_badge_info("caseoh_")
    assert [call.kwargs for call in update.call_args_list] == [
        {"channel_id": "123"}
    ] * 2


def test_get_user_clips_breaks_when_clips_none():
    download = Mock(return_value=[{"data": {"user": {"clips": None}}}])
    assert list(get_user_clips(Mock(), download, "testuser")) == []


@pytest.mark.parametrize("value", [None, 42, []])
def test_twitch_contains_challenge_text_non_string(value):
    assert gql._contains_challenge_text(value) is False


def test_download_base_gql_adds_auth_header():
    post = _post([])
    gql._download_base_gql(post, [], auth_token="test-value")  # noqa: S106
    assert post.call_args.kwargs["headers"]["Authorization"] == "OAuth test-value"


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (None, "unknown operation"),
        ([], "unknown operation"),
        (["One", "Two"], "One, Two"),
    ],
)
def test_describe_operation_names(names, expected):
    assert gql._describe_operation_names(names) == expected


def test_handle_gql_errors_empty_list_is_noop():
    assert gql._handle_gql_errors([]) is False


def test_download_gql_dict_response_with_errors():
    post = _post({"errors": [{"message": "some generic field error", "path": []}]})
    with pytest.raises(ParsingError):
        gql._download_gql(post, _operations())


def test_close_connection_swallows_oserror():
    irc = TwitchChatIRC.__new__(TwitchChatIRC)
    irc.socket = MagicMock()
    irc.current_channel = None
    irc.socket.sendall.side_effect = OSError("connection reset")
    irc.close_connection()


def test_irc_buffer_overflow_truncation():
    irc = MagicMock()
    irc.recv.side_effect = ["x" * (_READBUFFER_MAX_BYTES + 100), ""]
    with pytest.raises(ConnectionError):
        list(get_chat_messages_by_stream_id(irc, "channel", {"max_attempts": 1}))


@pytest.mark.parametrize(("value", "default"), [(None, 42), ([], -1), (3.14, 0)])
def test_parse_irc_int_flag_returns_default_for_other_types(value, default):
    assert _parse_irc_int_flag(value, default=default) == default


def test_iter_vod_chat_messages_with_offset_branch():
    request = ChatRequest(url="https://www.twitch.tv/videos/12345")
    with patch(
        "chat_downloader.sites.twitch.replay_service._fetch_vod_page",
        return_value=(None, {}),
    ):
        assert (
            list(
                iter_vod_chat_messages(
                    MagicMock(), "12345", request, max_duration=None, offset=10.0
                )
            )
            == []
        )


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"errors": [{"message": "clip not found"}]}, VideoNotFound),
        ({"data": {"clip": None}}, ParsingError),
    ],
)
def test_clip_response_errors(payload, error):
    downloader = MagicMock()
    downloader._download_base_gql.return_value = payload
    with pytest.raises(error):
        get_chat_by_clip_id(
            downloader, "test_clip", ChatRequest(url="https://www.twitch.tv/clip/test")
        )


@pytest.mark.parametrize("kind", ["vod", "clip", "stream"])
def test_extractor_routing_wrappers_delegate(monkeypatch, kind):
    downloader = TwitchChatDownloader()
    request = ChatRequest(url="https://www.twitch.tv/example")
    build = Mock(return_value="chat")
    monkeypatch.setattr(
        f"chat_downloader.sites.twitch.extractor.build_{kind}_chat", build
    )
    assert getattr(downloader, f"get_chat_by_{kind}_id")("id1", request) == "chat"
    match = Mock()
    match.group.return_value = "id2"
    assert getattr(downloader, f"_get_chat_by_{kind}_id")(match, request) == "chat"


def test_extractor_generate_urls_delegates_to_url_generation(monkeypatch):
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.generate_twitch_urls",
        lambda owner, livestream, vod, clip: iter([owner._NAME, livestream, vod, clip]),
    )
    assert list(TwitchChatDownloader().generate_urls(1, 2, 3)) == ["twitch.tv", 1, 2, 3]
