# SPDX-License-Identifier: MIT

from pathlib import Path

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


def test_describe_debug_sample_returns_stable_fixture_hint() -> None:
    hint = describe_debug_sample(
        Path("youtube-unknown-continuation-heartbeat-abc123def456.json"),
    )

    assert hint.site == "youtube"
    assert hint.group == "continuations"
    assert hint.fixture_name == "youtube-unknown-continuation-heartbeat"


def test_describe_debug_sample_falls_back_to_misc_for_unknown_label() -> None:
    hint = describe_debug_sample(Path("label-abc123def456.json"))

    assert hint.site == "label"
    assert hint.group == "misc"
    assert hint.fixture_name == "label"


def test_shared_name_inference_helpers_match_promoter_behavior() -> None:
    sample_path = Path(
        "youtube-missing-keys-liveChatMadeUpRenderer-abc123def456.json"
    )

    assert infer_site_from_sample_name(sample_path) == "youtube"
    assert infer_group_from_sample_name(sample_path) == "messages"
    assert normalize_fixture_name(sample_path) == (
        "youtube-missing-keys-liveChatMadeUpRenderer"
    )
