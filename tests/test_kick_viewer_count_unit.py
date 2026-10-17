# SPDX-License-Identifier: MIT

from __future__ import annotations

import json

import pytest

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.sites.kick.parsing.events import dispatch_event
from tests.kick_helpers import load_fixture


def test_viewer_count_snapshot_renders_count_and_preserves_payload():
    payload = load_fixture("public_rest_viewers.json")
    item = dispatch_event(
        {"event": "kick:viewer_count", "data": payload, "source": "public_rest"},
        received_timestamp=1_577_836_800_000_000,
    )
    assert item["metadata"]["data"] == payload
    assert ItemFormatter().format(item, "kick") == (
        "2020-01-01 00:00:00 [received] | [viewer count: 42]"
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
    assert ItemFormatter().format(item, "kick").endswith(f"[{expected}]")
