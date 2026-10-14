# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture


@pytest.mark.parametrize(
    ("source", "offline", "payload", "expected"),
    [
        ("public_rest", True, [], "viewer count: offline"),
        ("public_rest", False, [], "viewer count"),
        ("public_rest", "true", [], "viewer count"),
        ("websocket", True, [], "viewer count"),
        ("public_rest", True, [{"viewers": 0}], "viewer count: 0"),
    ],
)
def test_offline_viewer_count_requires_explicit_public_snapshot(
    source, offline, payload, expected
):
    frame = load_fixture("public_rest_viewers_offline.json")
    frame.update(source=source, offline=offline, data=payload)
    item = dispatch_event(frame, received_timestamp=1_577_836_800_000_000)
    assert ItemFormatter().format(item, "kick") == (
        f"2020-01-01 00:00:00 [received] | [channel metadata] {expected.capitalize()}"
    )
    assert item["metadata"]["data"] == payload
    assert item["metadata"].get("offline") is (
        True if expected == "viewer count: offline" else None
    )


@pytest.mark.parametrize("count", [0, 42, 11644])
def test_viewer_count_snapshot_renders_count_and_preserves_payload(count):
    payload = load_fixture("public_rest_viewers.json")
    payload[0]["viewers"] = count
    item = dispatch_event(
        {"event": "kick:viewer_count", "data": payload, "source": "public_rest"},
        received_timestamp=1_577_836_800_000_000,
    )
    assert item["metadata"]["data"] == payload
    assert ItemFormatter().format(item, "kick") == (
        f"2020-01-01 00:00:00 [received] | [channel metadata] Viewer count: {count}"
    )
    assert item["message"] == f"viewer count: {count}"


@pytest.mark.parametrize("metadata", [None, {}, "invalid"])
def test_viewer_count_without_usable_metadata_keeps_label(metadata):
    assert (
        ItemFormatter().format(
            {"message_type": "viewer_count", "metadata": metadata}, "kick"
        )
        == "[channel metadata] Viewer count"
    )


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([{"viewers": 0}], "viewer count: 0"),
        (None, "viewer count"),
        (42, "viewer count"),
        ([], "viewer count"),
        ({"viewers": 42}, "viewer count"),
        ([None], "viewer count"),
        ([{}], "viewer count"),
        ([{"viewers": None}], "viewer count"),
        ([{"viewers": True}], "viewer count"),
        ([{"viewers": -1}], "viewer count"),
        ([{"viewers": "42"}], "viewer count"),
        ([{"viewers": 4.2}], "viewer count"),
        ([{"viewers": 1}, {"viewers": 2}], "viewer count"),
    ],
)
@pytest.mark.parametrize("encoded", [False, True])
def test_viewer_count_summary_handles_zero_and_unknown_shapes(
    payload, expected, encoded
):
    item = dispatch_event(
        {
            "event": "kick:viewer_count",
            "data": json.dumps(payload) if encoded else payload,
        },
        received_timestamp=123,
    )
    assert item["message"] == expected
    assert item["metadata"]["data"] == payload
    assert (
        ItemFormatter()
        .format(item, "kick")
        .endswith(f"[channel metadata] {expected.capitalize()}")
    )
