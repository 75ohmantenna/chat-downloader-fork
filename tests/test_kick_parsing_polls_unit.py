# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.sites.kick.parsing.polls import (
    parse_poll_deleted_event,
    parse_poll_update_event,
)
from tests.kick_helpers import load_fixture


def test_parse_poll_update_fixture() -> None:
    raw = load_fixture("poll_update_event.json") | {"id": "kick-poll-update:100"}
    assert parse_poll_update_event(raw) == {
        "message_id": "kick-poll-update:100",
        "message_type": "poll_update",
        "message": "Example poll",
        "metadata": {
            "duration": 120,
            "remaining": 119,
            "result_display_duration": 120,
            "options": [
                {"id": 0, "label": "Option A", "votes": 0},
                {"id": 1, "label": "Option B", "votes": 1},
            ],
        },
    }


@pytest.mark.parametrize(
    ("poll", "metadata"),
    [
        (
            {"title": "Vote", "has_voted": False, "voted_option_id": 0},
            {"has_voted": False, "voted_option_id": 0},
        ),
        (
            {
                "title": 7,
                "duration": True,
                "remaining": -1,
                "result_display_duration": "10",
                "options": [None, {}, {"id": True, "label": 8, "votes": -1}],
                "has_voted": "false",
                "voted_option_id": False,
            },
            None,
        ),
    ],
)
def test_poll_update_optional_fields(poll, metadata):
    message = parse_poll_update_event({"id": "poll", "poll": poll})
    if metadata is None:
        assert message == {
            "message_id": "poll",
            "message_type": "poll_update",
            "message": "",
        }
    else:
        assert message["metadata"] == metadata


@pytest.mark.parametrize(
    "parser",
    [parse_poll_update_event, parse_poll_deleted_event],
)
@pytest.mark.parametrize("raw", [None, [], "bad"])
def test_poll_parsers_require_object(parser, raw):
    with pytest.raises(ParsingError):
        parser(raw)


@pytest.mark.parametrize(
    ("parser", "raw"),
    [
        (parse_poll_update_event, {"poll": {"title": "Poll"}}),
        (parse_poll_deleted_event, {}),
        *[
            (parse_poll_update_event, {"id": "poll", "poll": poll})
            for poll in [None, [], {}]
        ],
    ],
)
def test_poll_parsers_require_id_and_poll_data(parser, raw):
    with pytest.raises(ParsingError):
        parser(raw)


def test_parse_poll_deleted_event() -> None:
    assert parse_poll_deleted_event({"id": "kick-poll-deleted:101"}) == {
        "message_id": "kick-poll-deleted:101",
        "message_type": "poll_deleted",
        "message": "",
    }
