# SPDX-License-Identifier: MIT

"""Recorded prediction lifecycle details and adversarial formatter contracts."""

from __future__ import annotations

import json
from contextlib import closing
from copy import deepcopy

import pytest

from chat_downloader.formatting import ItemFormatter
from chat_downloader.output.capture_parity import audit_capture
from chat_downloader.output.writers import (
    JsonLinesContinuousWriter,
    TextContinuousWriter,
)
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture


def render(prediction, kind="prediction_updated"):
    return ItemFormatter().format(
        {
            "message_type": kind,
            "message": kind.replace("_", " "),
            "metadata": {"data": {"prediction": prediction}},
        },
        "kick",
    )


@pytest.mark.parametrize("encoded", [False, True])
def test_recorded_lifecycle_details_dispatch_and_exact_file_parity(tmp_path, encoded):
    fixture = load_fixture("predictions_recorded.json")
    expected_details = [
        (
            "Question: Who wins? | State: ACTIVE | Option A: 3,586 votes"
            " | Option B: 6,224 votes | Total: 9,810 votes"
        ),
        (
            "Question: Who wins? | State: LOCKED | Option A: 3,913 votes"
            " | Option B: 6,708 votes | Total: 10,621 votes"
        ),
        (
            "Question: Who wins? | State: RESOLVED | Option A: 3,915 votes"
            " | Option B: 6,709 votes | Total: 10,624 votes | Winner: Option B"
        ),
        (
            "Question: Who wins? | State: ACTIVE | Option A: 0 votes"
            " | Option B: 0 votes | Total: 0 votes"
        ),
    ]
    formatter = ItemFormatter()
    jsonl, txt = tmp_path / "capture.jsonl", tmp_path / "capture.txt"
    with (
        closing(JsonLinesContinuousWriter(str(jsonl))) as raw_writer,
        closing(TextContinuousWriter(str(txt))) as text_writer,
    ):
        for event, expected in zip(fixture["events"], expected_details, strict=True):
            frame = deepcopy(event["frame"])
            original = deepcopy(frame["data"])
            if encoded:
                frame["data"] = json.dumps(frame["data"])
            frame_before = deepcopy(frame)
            item = dispatch_event(frame, received_timestamp=event["received_timestamp"])
            assert item is not None
            before = deepcopy(item)
            rendered = formatter.format(item, "kick")
            label = item["message_type"].replace("_", " ")
            assert rendered.endswith(f"[{label}] | {expected}")
            assert "[received]" in rendered
            assert item == before
            assert item["metadata"]["data"] == original
            assert frame == frame_before
            raw_writer.write(item)
            text_writer.write(rendered)
    stats = audit_capture(jsonl, txt, formatter=formatter, format_name="kick")
    assert not stats.failed
    assert stats.jsonl_records == stats.txt_lines == 4


@pytest.mark.parametrize("payload", [None, [], False, 0, "invalid", {}])
@pytest.mark.parametrize("kind", ["prediction_created", "prediction_updated"])
def test_unusable_prediction_keeps_its_notice(payload, kind):
    assert render(payload, kind) == f"[{kind.replace('_', ' ')}]"


@pytest.mark.parametrize(
    ("payload", "suffix"),
    [
        ({"title": "Who?", "state": "LOCKED"}, " | Question: Who? | State: LOCKED"),
        ({"title": False, "state": {}, "outcomes": "invalid"}, ""),
        (
            {"outcomes": [{"title": "A", "vote_count": 1}]},
            " | A: 1 vote | Total: 1 vote",
        ),
        (
            {
                "outcomes": [
                    {"title": "A", "vote_count": 0},
                    {"title": "B", "vote_count": 0},
                ]
            },
            " | A: 0 votes | B: 0 votes | Total: 0 votes",
        ),
        (
            {"outcomes": [{"title": "A", "vote_count": 5}, {"title": "B"}]},
            " | A: 5 votes | B: votes unknown",
        ),
        ({"outcomes": [None, {"title": "B", "vote_count": 2}]}, " | B: 2 votes"),
        (
            {"outcomes": [{"vote_count": 2}, {"title": " ", "vote_count": 1}]},
            " | Outcome 1: 2 votes | Outcome 2: 1 vote | Total: 3 votes",
        ),
        ({"outcomes": [None, False, "invalid"]}, ""),
        (
            {
                "outcomes": [
                    {"title": "A", "vote_count": 5},
                    {"title": "B", "vote_count": 3},
                    {"title": "C", "vote_count": 1},
                ]
            },
            " | A: 5 votes | B: 3 votes | C: 1 vote | Total: 9 votes",
        ),
    ],
)
def test_partial_outcomes_and_totals_are_not_guessed(payload, suffix):
    before = deepcopy(payload)
    assert render(payload) == f"[prediction updated]{suffix}"
    assert payload == before


@pytest.mark.parametrize("count", [None, True, False, -1, "10", 1.5, {}, []])
def test_invalid_vote_count_remains_unknown(count):
    assert render({"outcomes": [{"title": "A", "vote_count": count}]}) == (
        "[prediction updated] | A: votes unknown"
    )


@pytest.mark.parametrize(
    ("state", "winner", "outcomes", "suffix"),
    [
        (
            "RESOLVED",
            "b",
            [
                {"id": "a", "title": "Popular", "vote_count": 9},
                {"id": "b", "title": "Actual winner", "vote_count": 1},
            ],
            " | Winner: Actual winner",
        ),
        ("ACTIVE", "b", [{"id": "b", "title": "Not resolved"}], ""),
        ("LOCKED", "b", [{"id": "b", "title": "Not resolved"}], ""),
        ("RESOLVED", "missing", [{"id": "a"}], ""),
        ("RESOLVED", True, [{"id": "true"}], ""),
        ("RESOLVED", "same", [{"id": "same"}, {"id": "same"}], ""),
        ("RESOLVED", " b ", [{"id": "b"}], ""),
        ("RESOLVED", "b", [{"id": "b", "title": " "}], " | Winner: Outcome 1"),
        ("RESOLVED", " ", [{"id": " "}], ""),
    ],
)
def test_winner_requires_unambiguous_explicit_resolved_identity(
    state, winner, outcomes, suffix
):
    text = render({"state": state, "winning_outcome_id": winner, "outcomes": outcomes})
    assert (" | Winner:" in text) == bool(suffix)
    if suffix:
        assert text.endswith(suffix)


def test_text_sanitization_and_payload_preservation():
    payload = {
        "title": "Question\nForged\x1b\ud800",
        "state": "ACTIVE\r\nSpoof",
        "outcomes": [{"title": "A\u2028B", "vote_count": 0}],
    }
    original = deepcopy(payload)
    rendered = render(payload)
    assert "\n" not in rendered
    assert "\r" not in rendered
    assert "\u2028" not in rendered
    assert r"Question\nForged\ud800" in rendered
    assert r"ACTIVE\r\nSpoof" in rendered
    assert r"A\u2028B: 0 votes" in rendered
    rendered.encode("utf-8")
    assert payload == original


def test_custom_templates_choose_whether_to_include_details():
    item = {
        "message_type": "prediction_updated",
        "message": "prediction updated",
        "metadata": {"data": {"prediction": {"title": "Who?", "state": "ACTIVE"}}},
    }
    formatter = ItemFormatter()
    assert (
        formatter.format(item, format_object={"template": "{message}"})
        == "prediction updated"
    )
    item["metadata"]["data"]["prediction"]["state"] = "LOCKED"
    assert render(item["metadata"]["data"]["prediction"]).endswith(" | State: LOCKED")
