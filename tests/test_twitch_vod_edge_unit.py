# SPDX-License-Identifier: MIT

"""Unit tests for the extracted _process_vod_edge helper in replay_service."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

from chat_downloader.sites.twitch.validation_keys import (
    find_unexpected_vod_edge_paths,
)


def _import() -> Any:
    from chat_downloader.sites.twitch.replay_service import _process_vod_edge

    return _process_vod_edge


def _make_edge(
    *,
    typename: str | None = "VideoCommentEdge",
    cursor: str = "cursor-abc",
    node: dict[str, Any] | None = None,
) -> dict[str, Any]:
    edge: dict[str, Any] = {}
    if typename is not None:
        edge["__typename"] = typename
    if cursor:
        edge["cursor"] = cursor
    if node is not None:
        edge["node"] = node
    return edge


def _make_node(
    *,
    typename: str | None = "Comment",
    content_offset_seconds: float = 10.0,
) -> dict[str, Any]:
    node: dict[str, Any] = {
        "contentOffsetSeconds": content_offset_seconds,
        "message": {"fragments": []},
        "commenter": {"login": "user1"},
    }
    if typename is not None:
        node["__typename"] = typename
    return node


def _pass_time_filter(data: dict[str, Any]) -> str:
    return "yield"


def _pass_msg_filter(data: dict[str, Any]) -> bool:
    return True


class _AlwaysYieldTimeFilter:
    def check(self, data: dict[str, Any]) -> str:
        return "yield"


class _StopTimeFilter:
    def check(self, data: dict[str, Any]) -> str:
        return "stop"


class _SkipTimeFilter:
    def check(self, data: dict[str, Any]) -> str:
        return "skip"


class _AllowMsgFilter:
    def should_add(self, data: dict[str, Any]) -> bool:
        return True


class _RejectMsgFilter:
    def should_add(self, data: dict[str, Any]) -> bool:
        return False


_LOGGER = logging.getLogger("test")


class TestProcessVodEdge:
    def test_raw_shape_validation_is_debug_only(self) -> None:
        _process_vod_edge = _import()
        edge = _make_edge(node=_make_node())
        quiet_logger = logging.getLogger("test-quiet-twitch-vod")
        quiet_logger.setLevel(logging.INFO)

        with (
            patch(
                "chat_downloader.sites.twitch.replay_service."
                "find_unexpected_vod_edge_paths"
            ) as validate_shape,
            patch(
                "chat_downloader.sites.twitch.replay_service._parse_item",
                return_value={"message_type": "text_message"},
            ),
        ):
            result_data, disposition = _process_vod_edge(
                edge,
                offset=0.0,
                creator_channel_id="chan-1",
                badge_set=None,
                time_filter=_AlwaysYieldTimeFilter(),
                msg_filter=_AllowMsgFilter(),
                logger_obj=quiet_logger,
            )

        assert disposition == "yield"
        assert result_data == {"message_type": "text_message"}
        validate_shape.assert_not_called()

    def test_raw_provider_field_is_captured_before_remapping(self) -> None:
        """Raw GraphQL drift is visible even when remapping drops the field."""
        _process_vod_edge = _import()
        node = _make_node()
        node["newProviderField"] = "provider-value"
        edge = _make_edge(node=node)
        debug_logger = logging.getLogger("test-raw-twitch-vod-drift")
        debug_logger.setLevel(logging.DEBUG)

        with (
            patch(
                "chat_downloader.sites.twitch.replay_service.capture_debug_sample"
            ) as capture,
            patch("chat_downloader.sites.twitch.replay_service.debug_log") as debug,
        ):
            result_data, disposition = _process_vod_edge(
                edge,
                offset=0.0,
                creator_channel_id="chan-1",
                badge_set=None,
                time_filter=_AlwaysYieldTimeFilter(),
                msg_filter=_AllowMsgFilter(),
                logger_obj=debug_logger,
            )

        assert disposition == "yield"
        assert result_data is not None
        capture.assert_called_once_with(
            "twitch-unknown-gql-shape",
            {
                "raw": edge,
                "unexpected_paths": ["edge.node.newProviderField"],
            },
            sample_limit=10,
        )
        debug.assert_called_once()

    def test_valid_edge_yields_data(self) -> None:
        """A well-formed edge with passing filters returns (data, 'yield')."""
        _process_vod_edge = _import()
        node = _make_node()
        edge = _make_edge(node=node)

        parsed_data = {"message_type": "text_message", "time_in_seconds": 10.0}

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "chat_downloader.sites.twitch.replay_service._parse_item",
            return_value=parsed_data,
        ):
            result_data, disposition = _process_vod_edge(
                edge,
                offset=0.0,
                creator_channel_id="chan-1",
                badge_set=None,
                time_filter=_AlwaysYieldTimeFilter(),
                msg_filter=_AllowMsgFilter(),
                logger_obj=_LOGGER,
            )

        assert disposition == "yield"
        assert result_data == parsed_data

    def test_edge_with_badges_and_no_commenter_yields_data(self) -> None:
        """Badge-bearing anonymous comments survive the real edge parser."""
        _process_vod_edge = _import()
        node = _make_node()
        node.pop("commenter")
        node["message"] = {
            "userBadges": [{"setID": "moderator", "version": "1"}],
            "fragments": [{"text": "hello"}],
        }

        result_data, disposition = _process_vod_edge(
            _make_edge(node=node),
            offset=0.0,
            creator_channel_id="chan-1",
            badge_set=None,
            time_filter=_AlwaysYieldTimeFilter(),
            msg_filter=_AllowMsgFilter(),
            logger_obj=_LOGGER,
        )

        assert disposition == "yield"
        assert result_data is not None
        assert result_data["author"] == {
            "badges": [{"name": "moderator", "version": 1}]
        }

    def test_unexpected_edge_typename_skips(self) -> None:
        """An edge with an unexpected __typename returns (None, 'skip')."""
        _process_vod_edge = _import()
        edge = _make_edge(typename="UnknownEdgeType", node=_make_node())

        result_data, disposition = _process_vod_edge(
            edge,
            offset=0.0,
            creator_channel_id=None,
            badge_set=None,
            time_filter=_AlwaysYieldTimeFilter(),
            msg_filter=_AllowMsgFilter(),
            logger_obj=_LOGGER,
        )

        assert disposition == "skip"
        assert result_data is None

    def test_time_filter_stop_returns_stop(self) -> None:
        """When the time filter returns 'stop', disposition is 'stop'."""
        _process_vod_edge = _import()
        node = _make_node()
        edge = _make_edge(node=node)

        parsed_data = {"message_type": "text_message", "time_in_seconds": 999.0}

        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "chat_downloader.sites.twitch.replay_service._parse_item",
            return_value=parsed_data,
        ):
            result_data, disposition = _process_vod_edge(
                edge,
                offset=0.0,
                creator_channel_id=None,
                badge_set=None,
                time_filter=_StopTimeFilter(),
                msg_filter=_AllowMsgFilter(),
                logger_obj=_LOGGER,
            )

        assert disposition == "stop"
        assert result_data is None


def test_vod_edge_shape_validation_reports_nested_drift() -> None:
    edge = {
        "__typename": 123,
        "extraEdge": True,
        "node": {
            "__typename": "MysteryComment",
            "extraNode": True,
            "commenter": {"login": "user", "extraCommenter": True},
            "message": {
                "extraMessage": True,
                "userBadges": [
                    {"setID": "subscriber", "version": "1", "extraBadge": True},
                    "invalid-badge",
                ],
                "fragments": [
                    {
                        "text": "hello",
                        "extraFragment": True,
                        "emote": {"emoteID": "25", "id": "x;0;4", "extra": 1},
                    },
                    "invalid-fragment",
                ],
            },
        },
    }

    assert set(find_unexpected_vod_edge_paths(edge)) == {
        "edge.__typename=123",
        "edge.extraEdge",
        "edge.node.__typename='MysteryComment'",
        "edge.node.extraNode",
        "edge.node.commenter.extraCommenter",
        "edge.node.message.extraMessage",
        "edge.node.message.userBadges[0].extraBadge",
        "edge.node.message.userBadges[1] (expected object)",
        "edge.node.message.fragments[0].extraFragment",
        "edge.node.message.fragments[0].emote.extra",
        "edge.node.message.fragments[1] (expected object)",
    }


def test_vod_edge_shape_validation_reports_invalid_containers() -> None:
    assert find_unexpected_vod_edge_paths({"node": []}) == [
        "edge.node (expected object or null)"
    ]
    assert set(
        find_unexpected_vod_edge_paths(
            {
                "node": {
                    "commenter": [],
                    "message": [],
                }
            }
        )
    ) == {
        "edge.node.commenter (expected object or null)",
        "edge.node.message (expected object or null)",
    }
    assert set(
        find_unexpected_vod_edge_paths(
            {
                "node": {
                    "message": {
                        "userBadges": {},
                        "fragments": {},
                    }
                }
            }
        )
    ) == {
        "edge.node.message.userBadges (expected list or null)",
        "edge.node.message.fragments (expected list or null)",
    }
    assert find_unexpected_vod_edge_paths(
        {
            "node": {
                "message": {
                    "fragments": [{"text": "hello", "emote": []}],
                }
            }
        }
    ) == ["edge.node.message.fragments[0].emote (expected object or null)"]
