# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

import pytest

from chat_downloader.debug_sample_utils import (
    describe_debug_sample,
    infer_group_from_sample_name,
    infer_site_from_sample_name,
    normalize_fixture_name,
    slugify_debug_label,
)


def test_slugify_debug_label_normalizes_human_label() -> None:
    assert slugify_debug_label("Unknown continuation: heartbeat") == (
        "unknown-continuation-heartbeat"
    )


@pytest.mark.parametrize(
    ("stem", "site", "group"),
    [
        ("youtube-unknown-continuation-heartbeat", "youtube", "continuations"),
        ("youtube-continuation-response", "youtube", "continuations"),
        ("label", "label", "misc"),
        ("youtube-missing-keys-liveChatMadeUpRenderer", "youtube", "messages"),
        ("twitch-unknown-irc-shape", "twitch", "messages"),
        ("twitch-irc-frame", "twitch", "messages"),
        ("twitch-irc-event-message-resubscription-7dce7b9831c9", "twitch", "messages"),
    ],
)
def test_sample_name_inference(stem, site, group):
    sample_path = Path(f"{stem}-abc123def456.json")
    hint = describe_debug_sample(sample_path)
    assert (hint.site, hint.group, hint.fixture_name) == (site, group, stem)
    assert infer_site_from_sample_name(sample_path) == site
    assert infer_group_from_sample_name(sample_path) == group
    assert normalize_fixture_name(sample_path) == stem


@pytest.mark.parametrize(
    ("sample_name", "group"),
    [
        ("kick-unknown-event-abc123def456.json", "events"),
        ("kick-malformed-event-abc123def456.json", "events"),
        ("kick-pusher-error-abc123def456.json", "events"),
        ("kick-unknown-message-type-abc123def456.json", "messages"),
        ("kick-malformed-preloaded-message-abc123def456.json", "messages"),
        ("kick-malformed-preloaded-pin-abc123def456.json", "events"),
        ("kick-websocket-frame-abc123def456.json", "events"),
        ("kick-websocket-frame-user-banned-abc123def456.json", "events"),
        ("kick-unknown-websocket-shape-abc123def456.json", "transport"),
    ],
)
def test_kick_samples_map_to_provider_fixture_groups(
    sample_name: str,
    group: str,
) -> None:
    assert infer_group_from_sample_name(sample_name) == group
