# SPDX-License-Identifier: MIT

"""Unit tests for Twitch extractor improvements.

Tests the IRC buffer deduplication (#6) and typename validation (#8)
improvements added in v0.2.33+mod and v0.2.34+mod.
"""

from unittest.mock import Mock, patch

import pytest

from chat_downloader.errors import UserNotFound
from chat_downloader.models import ChatRequest
from chat_downloader.sites.twitch.extractor import TwitchChatDownloader


class TestIRCBufferDeduplication:
    """Test improvement #6: IRC buffer logic deduplication.

    Verifies that the extractor properly delegates buffer parsing to the
    client's get_chat_messages_by_stream_id() function instead of duplicating
    the logic.
    """

    def test_client_generator_function_exists(self) -> None:
        """Test that get_chat_messages_by_stream_id is importable from irc_transport."""
        from chat_downloader.sites.twitch.irc_transport import (
            get_chat_messages_by_stream_id,
        )

        assert callable(get_chat_messages_by_stream_id)

    def test_extractor_imports_client_generator(self) -> None:
        """Test that extractor imports the client's generator function."""
        from chat_downloader.sites.twitch import extractor

        # The extractor module should have imported
        # get_chat_messages_by_stream_id
        assert hasattr(extractor, "get_chat_messages_by_stream_id")

    def test_extractor_method_exists(self) -> None:
        """Test that extractor has the _get_chat_messages_by_stream_id
        method.
        """
        downloader = TwitchChatDownloader()
        assert hasattr(downloader, "_get_chat_messages_by_stream_id")
        assert callable(downloader._get_chat_messages_by_stream_id)

    @patch(
        "chat_downloader.sites.twitch.extractor.get_chat_messages_by_stream_id"
    )
    @patch("chat_downloader.sites.twitch.extractor.TwitchChatIRC")
    def test_extractor_delegates_to_client(
        self, mock_irc_class, mock_client_generator
    ) -> None:
        """Test that extractor calls client's
        get_chat_messages_by_stream_id.
        """
        # Setup mock IRC connection
        mock_irc_instance = Mock()
        mock_irc_instance.join_channel = Mock()
        mock_irc_instance.close_connection = Mock()
        mock_irc_class.return_value = mock_irc_instance

        # Setup mock client generator - return StopIteration immediately
        mock_client_generator.return_value = iter([])

        # Create downloader
        downloader = TwitchChatDownloader()
        params = {
            "max_attempts": 1,
            "message_receive_timeout": 1,
            "message_groups": ["messages"],
        }

        # Call the method (it will create a generator but not yield anything)
        gen = downloader._get_chat_messages_by_stream_id("test_channel", params)

        # Try to get first item (will raise StopIteration since mock returns
        # empty)
        try:
            next(gen)
        except StopIteration:
            pass  # Expected

        # Verify client generator was called with IRC instance
        mock_client_generator.assert_called()
        call_args = mock_client_generator.call_args
        assert call_args[0][0] == mock_irc_instance  # First arg is IRC instance
        assert call_args[0][1] == "test_channel"  # Second arg is stream_id


class TestTypenameValidation:
    """Test improvement #8: GraphQL __typename validation.

    Verifies that VOD pagination properly validates edge and node types,
    skipping unexpected GraphQL response structures.
    """

    @patch("chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id")
    def test_accepts_valid_edge_typename(self, mock_get_vod_messages) -> None:
        """Test that valid VideoCommentEdge typename is accepted."""
        # Mock VOD response with valid typename
        comments = {
            "edges": [
                {
                    "__typename": "VideoCommentEdge",
                    "cursor": "cursor1",
                    "node": {
                        "__typename": "Comment",
                        "id": "msg1",
                        "message": {"fragments": [{"text": "Valid message"}]},
                        "commenter": {"login": "user1"},
                    },
                },
            ],
            "pageInfo": {"hasNextPage": False},
        }

        info = {"creator": {"id": "channel123", "channel": {"id": "ch123"}}}
        mock_get_vod_messages.return_value = (comments, info)

        downloader = TwitchChatDownloader()
        params = {
            "max_attempts": 1,
            "message_groups": ["messages"],
        }

        result = list(
            downloader._get_chat_messages_by_vod_id("vod123", params, 100)
        )

        # Should process the message
        assert len(result) == 1
        assert "Valid message" in str(result[0])

    @patch("chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id")
    @patch("chat_downloader.sites.twitch.extractor.logger")
    def test_skips_invalid_edge_typename(
        self, mock_logger, mock_get_vod_messages
    ) -> None:
        """Test that invalid edge typename is skipped with debug log."""
        # Mock VOD response with invalid edge typename
        comments = {
            "edges": [
                {
                    "__typename": "UnexpectedEdgeType",  # Invalid!
                    "cursor": "cursor1",
                    "node": {
                        "__typename": "Comment",
                        "id": "msg1",
                        "message": {
                            "fragments": [{"text": "Should be skipped"}]
                        },
                    },
                },
                {
                    "__typename": "VideoCommentEdge",  # Valid
                    "cursor": "cursor2",
                    "node": {
                        "__typename": "Comment",
                        "id": "msg2",
                        "message": {"fragments": [{"text": "Valid message"}]},
                        "commenter": {"login": "user1"},
                    },
                },
            ],
            "pageInfo": {"hasNextPage": False},
        }

        info = {"creator": {"id": "channel123", "channel": {"id": "ch123"}}}
        mock_get_vod_messages.return_value = (comments, info)

        downloader = TwitchChatDownloader()
        params = {
            "max_attempts": 1,
            "message_groups": ["messages"],
        }

        result = list(
            downloader._get_chat_messages_by_vod_id("vod123", params, 100)
        )

        # Should only get the valid edge
        assert len(result) == 1
        assert "Valid message" in str(result[0])

        # Should log debug message about skipping
        mock_logger.debug.assert_any_call(
            "Skipping unexpected edge type: UnexpectedEdgeType",
        )

    @patch("chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id")
    @patch("chat_downloader.sites.twitch.extractor.logger")
    def test_skips_invalid_node_typename(
        self, mock_logger, mock_get_vod_messages
    ) -> None:
        """Test that invalid node typename is skipped with debug log."""
        # Mock VOD response with invalid node typename
        comments = {
            "edges": [
                {
                    "__typename": "VideoCommentEdge",
                    "cursor": "cursor1",
                    "node": {
                        "__typename": "UnexpectedNodeType",  # Invalid!
                        "id": "msg1",
                        "message": {
                            "fragments": [{"text": "Should be skipped"}]
                        },
                    },
                },
                {
                    "__typename": "VideoCommentEdge",
                    "cursor": "cursor2",
                    "node": {
                        "__typename": "Comment",  # Valid
                        "id": "msg2",
                        "message": {"fragments": [{"text": "Valid message"}]},
                        "commenter": {"login": "user1"},
                    },
                },
            ],
            "pageInfo": {"hasNextPage": False},
        }

        info = {"creator": {"id": "channel123", "channel": {"id": "ch123"}}}
        mock_get_vod_messages.return_value = (comments, info)

        downloader = TwitchChatDownloader()
        params = {
            "max_attempts": 1,
            "message_groups": ["messages"],
        }

        result = list(
            downloader._get_chat_messages_by_vod_id("vod123", params, 100)
        )

        # Should only get the valid node
        assert len(result) == 1
        assert "Valid message" in str(result[0])

        # Should log debug message about skipping
        mock_logger.debug.assert_any_call(
            "Skipping unexpected node type: UnexpectedNodeType",
        )

    @patch("chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id")
    def test_accepts_none_typename(self, mock_get_vod_messages) -> None:
        """Test that None typename is accepted (backward compatibility)."""
        # Mock VOD response without typename fields
        comments = {
            "edges": [
                {
                    # No __typename field
                    "cursor": "cursor1",
                    "node": {
                        # No __typename field
                        "id": "msg1",
                        "message": {
                            "fragments": [{"text": "Message without typename"}],
                        },
                        "commenter": {"login": "user1"},
                    },
                },
            ],
            "pageInfo": {"hasNextPage": False},
        }

        info = {"creator": {"id": "channel123", "channel": {"id": "ch123"}}}
        mock_get_vod_messages.return_value = (comments, info)

        downloader = TwitchChatDownloader()
        params = {
            "max_attempts": 1,
            "message_groups": ["messages"],
        }

        result = list(
            downloader._get_chat_messages_by_vod_id("vod123", params, 100)
        )

        # Should process the message (None is acceptable)
        assert len(result) == 1
        assert "Message without typename" in str(result[0])

    @patch("chat_downloader.sites.twitch.extractor.get_chat_messages_by_vod_id")
    def test_channel_not_found_detection(self, mock_get_vod_messages) -> None:
        """Test that empty creator.id raises UserNotFound error (improvement
        #5).
        """
        # Mock VOD response with empty creator ID (channel deleted/not found)
        comments = {"edges": [], "pageInfo": {"hasNextPage": False}}

        info = {"creator": {"id": ""}}  # Empty ID means channel not found
        mock_get_vod_messages.return_value = (comments, info)

        downloader = TwitchChatDownloader()
        params = {
            "max_attempts": 1,
            "message_groups": ["messages"],
        }

        # Should raise UserNotFound with clear error message
        with pytest.raises(UserNotFound) as exc_info:
            list(downloader._get_chat_messages_by_vod_id("vod123", params, 100))

        assert "not found" in str(exc_info.value).lower()
        assert "vod123" in str(exc_info.value)


def test_twitch_extractor_delegates_simple_wrappers(monkeypatch) -> None:
    downloader = TwitchChatDownloader()
    downloader._session_post = object()

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor._download_base_gql",
        lambda session_post, ops, auth_token: ("base", session_post, ops),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor._download_gql",
        lambda session_post, ops, auth_token: ("gql", session_post, ops),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.generate_twitch_urls",
        lambda owner, livestream_limit, vod_limit, clip_limit: iter(
            [owner._NAME, livestream_limit, vod_limit, clip_limit]
        ),
    )

    assert downloader._download_base_gql([{"op": 1}]) == (
        "base",
        downloader._session_post,
        [{"op": 1}],
    )
    assert downloader._download_gql([{"op": 2}]) == (
        "gql",
        downloader._session_post,
        [{"op": 2}],
    )
    assert list(downloader.generate_urls(1, 2, 3)) == ["twitch.tv", 1, 2, 3]


def test_twitch_extractor_wrappers_pass_configured_client_id(
    monkeypatch,
) -> None:

    downloader = TwitchChatDownloader()
    downloader._session_post = object()
    downloader._twitch_client_id = "client-123"

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor._download_base_gql",
        lambda session_post, ops, auth_token, client_id: (
            "base",
            session_post,
            ops,
            auth_token,
            client_id,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor._download_gql",
        lambda session_post, ops, auth_token, client_id: (
            "gql",
            session_post,
            ops,
            auth_token,
            client_id,
        ),
    )

    assert downloader._download_base_gql([{"op": 1}]) == (
        "base",
        downloader._session_post,
        [{"op": 1}],
        None,
        "client-123",
    )
    assert downloader._download_gql([{"op": 2}]) == (
        "gql",
        downloader._session_post,
        [{"op": 2}],
        None,
        "client-123",
    )


def test_twitch_update_badge_info_passes_configured_client_id(
    monkeypatch,
) -> None:
    from typing import Any

    downloader = TwitchChatDownloader()
    downloader._session_post = object()
    downloader._twitch_client_id = "client-123"
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.update_badge_info",
        lambda *args, **kwargs: calls.append((*args, kwargs)),
    )

    downloader._update_badge_info("channel-name")

    assert calls == [
        (
            downloader._session_post,
            "channel-name",
            downloader._download_gql.__globals__["_download_gql"],
            downloader.badge_cache.global_badges,
            downloader.badge_cache.channel_badges,
            {"client_id": "client-123"},
        )
    ]


def test_twitch_extractor_routing_wrappers_delegate(monkeypatch) -> None:
    downloader = TwitchChatDownloader()
    request = ChatRequest(url="https://www.twitch.tv/example")

    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.build_vod_chat",
        lambda owner, vod_id, params: ("vod", owner, vod_id, params),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.build_clip_chat",
        lambda owner, clip_id, params: ("clip", owner, clip_id, params),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.twitch.extractor.build_stream_chat",
        lambda owner, stream_id, params: ("stream", owner, stream_id, params),
    )

    class Match:
        def __init__(self, value: str) -> None:
            self.value = value

        def group(self, _name: str) -> str:
            return self.value

    assert downloader.get_chat_by_vod_id("v1", request) == (
        "vod",
        downloader,
        "v1",
        request,
    )
    assert downloader._get_chat_by_vod_id(Match("v2"), request) == (
        "vod",
        downloader,
        "v2",
        request,
    )
    assert downloader.get_chat_by_clip_id("c1", request) == (
        "clip",
        downloader,
        "c1",
        request,
    )
    assert downloader._get_chat_by_clip_id(Match("c2"), request) == (
        "clip",
        downloader,
        "c2",
        request,
    )
    assert downloader.get_chat_by_stream_id("s1", request) == (
        "stream",
        downloader,
        "s1",
        request,
    )
    assert downloader._get_chat_by_stream_id(Match("s2"), request) == (
        "stream",
        downloader,
        "s2",
        request,
    )


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

    _handle_gql_errors([])  # Should not raise


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

    gen = get_chat_messages_by_stream_id(
        mock_irc, "channel", {"max_attempts": 1}
    )
    with pytest.raises(ConnectionError):
        for _ in gen:
            pass


def test_parse_irc_int_flag_returns_default_for_other_types() -> None:
    from chat_downloader.sites.twitch.parsing.messages import (
        _parse_irc_int_flag,
    )

    assert _parse_irc_int_flag(None, default=42) == 42
    assert _parse_irc_int_flag([], default=-1) == -1
    assert _parse_irc_int_flag(3.14, default=0) == 0


def test_iter_vod_chat_messages_with_offset_branch() -> None:
    from unittest.mock import MagicMock, patch

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


def test_get_chat_by_clip_id_dict_errors_raises() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.errors import VideoNotFound
    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.twitch.replay_service import get_chat_by_clip_id

    mock_downloader = MagicMock()
    mock_downloader._download_base_gql.return_value = {
        "errors": [{"message": "clip not found"}]
    }
    request = ChatRequest(url="https://www.twitch.tv/clip/test")

    with pytest.raises(VideoNotFound):
        get_chat_by_clip_id(mock_downloader, "test_clip", request)


def test_get_chat_by_clip_id_clip_is_none_raises() -> None:
    from unittest.mock import MagicMock

    from chat_downloader.errors import ParsingError
    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.twitch.replay_service import get_chat_by_clip_id

    mock_downloader = MagicMock()
    mock_downloader._download_base_gql.return_value = {"data": {"clip": None}}
    request = ChatRequest(url="https://www.twitch.tv/clip/test")

    with pytest.raises(ParsingError, match="Unable to retrieve clip data"):
        get_chat_by_clip_id(mock_downloader, "test_clip", request)


# ---------------------------------------------------------------------------
# twitch_client_id preservation
# ---------------------------------------------------------------------------


def test_twitch_client_id_preserved_after_init() -> None:
    """twitch_client_id kwarg must survive TwitchChatDownloader.__init__."""
    downloader = TwitchChatDownloader(twitch_client_id="my-custom-id")
    assert downloader._twitch_client_id == "my-custom-id"


def test_twitch_client_id_none_when_not_provided() -> None:
    """_twitch_client_id defaults to None when kwarg is absent."""
    downloader = TwitchChatDownloader()
    assert downloader._twitch_client_id is None
