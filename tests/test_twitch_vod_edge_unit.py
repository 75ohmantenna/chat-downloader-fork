# SPDX-License-Identifier: MIT

"""Unit tests for the extracted _process_vod_edge helper in replay_service."""

import logging
from typing import Any


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
