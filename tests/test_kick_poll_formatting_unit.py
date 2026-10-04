# SPDX-License-Identifier: MIT

from __future__ import annotations

from copy import deepcopy

import pytest

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.output.writers import TextContinuousWriter
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture


@pytest.mark.parametrize("deleted", [False, True])
def test_poll_fixture_dispatch_and_txt_output(tmp_path, deleted):
    fixture = "poll_deleted_event.json" if deleted else "poll_update_event.json"
    event = "PollDeleteEvent" if deleted else "PollUpdateEvent"
    item = dispatch_event(
        {"event": f"App\\Events\\{event}", "data": load_fixture(fixture)},
        received_timestamp=1_577_836_800_000_000,
    )
    assert item is not None
    # The live service attaches the receive timestamp after event dispatch.
    item["received_timestamp"] = 1_577_836_800_000_000
    original = deepcopy(item)
    expected = (
        "[Poll deleted]"
        if deleted
        else (
            "[Poll update] Example poll | Option A: 0 votes (0.0%)"
            " | Option B: 1 vote (100.0%) | Total: 1 | Remaining: 119s"
        )
    )
    rendered = ItemFormatter().format(item, "kick")
    assert rendered == f"2020-01-01 00:00:00 [received] | {expected}"
    path = tmp_path / "poll.txt"
    writer = TextContinuousWriter(str(path))
    try:
        writer.write(rendered)
    finally:
        writer.close()
    assert path.read_text(encoding="utf-8") == rendered + "\n"
    assert item == original
    item["timestamp"] = 1_577_923_200_000_000
    assert ItemFormatter().format(item, "kick") == (f"2020-01-02 00:00:00 | {expected}")


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        (
            {
                "options": [
                    {"label": "Sub5", "votes": 17},
                    {"label": "Subhuman", "votes": 50},
                ],
                "remaining": 3,
            },
            (
                " | Sub5: 17 votes (25.4%) | Subhuman: 50 votes (74.6%)"
                " | Total: 67 | Remaining: 3s"
            ),
        ),
        (
            {
                "options": [
                    {"label": "Sub5", "votes": 0},
                    {"label": "Subhuman", "votes": 0},
                ],
                "remaining": 30,
            },
            " | Sub5: 0 votes | Subhuman: 0 votes | Total: 0 | Remaining: 30s",
        ),
        (
            {"options": [{"label": "Yes", "votes": 5}, {"label": "No"}]},
            " | Yes: 5 votes | No: votes unknown",
        ),
        (
            {"options": [None, {"label": "Yes", "votes": 5}]},
            " | Yes: 5 votes",
        ),
        (
            {"options": [{"id": 0, "votes": 2}, {"votes": 2}], "remaining": 0},
            (
                " | Option 0: 2 votes (50.0%) | Option 2: 2 votes (50.0%)"
                " | Total: 4 | Remaining: 0s"
            ),
        ),
        (
            {
                "options": [{"label": "A\nB\x1b", "votes": 1}],
                "remaining": 1,
            },
            r" | A\nB: 1 vote (100.0%) | Total: 1 | Remaining: 1s",
        ),
        ({}, ""),
        ([], ""),
        (False, ""),
        ({"options": [], "remaining": 0}, " | Remaining: 0s"),
        ({"options": "invalid", "remaining": "0"}, ""),
        ({"options": [None], "remaining": -1}, ""),
    ],
)
def test_poll_metadata_rendering(metadata, expected):
    item = {"message_type": "poll_update", "message": "Rate", "metadata": metadata}
    original = deepcopy(item)
    assert ItemFormatter().format(item, "kick") == f"[Poll update] Rate{expected}"
    assert item == original


@pytest.mark.parametrize("invalid", [None, True, False, -1, "2", 2.5])
def test_poll_invalid_counts_and_countdown_are_not_fabricated(invalid):
    item = {
        "message_type": "poll_update",
        "message": "Rate",
        "metadata": {
            "options": [{"label": "Yes", "votes": invalid}],
            "remaining": invalid,
        },
    }
    assert ItemFormatter().format(item, "kick") == (
        "[Poll update] Rate | Yes: votes unknown"
    )


def test_poll_details_only_render_when_requested_by_template():
    item = {
        "message_type": "poll_update",
        "message": "Rate",
        "metadata": {"options": [{"label": "Yes", "votes": 2}], "remaining": 0},
    }
    formatter = ItemFormatter()
    assert formatter.format(item, "default") == ": Rate"
    assert formatter.format(item, format_object={"template": "{message}"}) == "Rate"


def test_poll_updates_recompute_counts_and_remaining():
    item = {
        "message_type": "poll_update",
        "message": "Rate",
        "metadata": {"options": [{"label": "Yes", "votes": 0}], "remaining": 30},
    }
    formatter = ItemFormatter()
    assert formatter.format(item, "kick") == (
        "[Poll update] Rate | Yes: 0 votes | Total: 0 | Remaining: 30s"
    )
    item["metadata"]["options"][0]["votes"] = 3
    item["metadata"]["remaining"] = 0
    assert formatter.format(item, "kick") == (
        "[Poll update] Rate | Yes: 3 votes (100.0%) | Total: 3 | Remaining: 0s"
    )
