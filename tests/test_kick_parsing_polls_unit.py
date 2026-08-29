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
    raw = load_fixture("poll_update_event.json")
    raw["id"] = "kick-poll-update:100"

    message = parse_poll_update_event(raw)

    assert message == {
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


def test_poll_update_preserves_viewer_state() -> None:
    message = parse_poll_update_event(
        {
            "id": "poll",
            "poll": {
                "title": "Vote",
                "has_voted": False,
                "voted_option_id": 0,
            },
        }
    )

    assert message["metadata"] == {"has_voted": False, "voted_option_id": 0}


def test_poll_update_omits_malformed_optional_fields() -> None:
    message = parse_poll_update_event(
        {
            "id": "poll",
            "poll": {
                "title": 7,
                "duration": True,
                "remaining": -1,
                "result_display_duration": "10",
                "options": [
                    None,
                    {},
                    {
                        "id": True,
                        "label": 8,
                        "votes": -1,
                    },
                ],
                "has_voted": "false",
                "voted_option_id": False,
            },
        }
    )

    assert message == {
        "message_id": "poll",
        "message_type": "poll_update",
        "message": "",
    }


@pytest.mark.parametrize("raw", [None, [], "bad"])
def test_poll_update_requires_object(raw: object) -> None:
    with pytest.raises(ParsingError, match="was not a JSON object"):
        parse_poll_update_event(raw)


def test_poll_update_requires_id() -> None:
    with pytest.raises(ParsingError, match="missing an id"):
        parse_poll_update_event({"poll": {"title": "Poll"}})


@pytest.mark.parametrize("poll", [None, [], {}])
def test_poll_update_requires_poll_data(poll: object) -> None:
    with pytest.raises(ParsingError, match="missing poll data"):
        parse_poll_update_event({"id": "poll", "poll": poll})


def test_parse_poll_deleted_event() -> None:
    assert parse_poll_deleted_event({"id": "kick-poll-deleted:101"}) == {
        "message_id": "kick-poll-deleted:101",
        "message_type": "poll_deleted",
        "message": "",
    }


@pytest.mark.parametrize("raw", [None, [], "bad"])
def test_poll_deleted_requires_object(raw: object) -> None:
    with pytest.raises(ParsingError, match="was not a JSON object"):
        parse_poll_deleted_event(raw)


def test_poll_deleted_requires_id() -> None:
    with pytest.raises(ParsingError, match="missing an id"):
        parse_poll_deleted_event({})
