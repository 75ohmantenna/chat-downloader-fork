# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
from pathlib import Path

import pytest

import chat_downloader.debugging as debugging
import chat_downloader.redaction as redaction
from chat_downloader.sites.youtube.continuation import _log_continuation_debug_info
from chat_downloader.sites.youtube.continuations import ContinuationParseResult
from chat_downloader.sites.youtube.parsing import actions_router
from chat_downloader.sites.youtube.parsing.actions_handlers_validation import (
    _emit_parse_diagnostics,
    validate_and_finalize_message,
)
from tests.core_third_helpers import restore_loggers


@pytest.fixture
def sample_dir(tmp_path, monkeypatch):
    directory = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(directory))
    monkeypatch.setattr(redaction, "_DEBUG_SAMPLE_DIGESTS", {})
    monkeypatch.setattr(redaction, "_DEBUG_SAMPLE_LIMIT_WARNED", set())
    with restore_loggers():
        debugging.set_log_level("debug")
        yield directory


def _emit(family, index, value=0):
    action = f"futureAction{index}"
    renderer = f"futureRenderer{index}"
    payload = {"value": value}
    if family == "unknown-action":
        actions_router.process_action({action: payload})
    elif family == "unknown-continuation":
        _log_continuation_debug_info(
            ContinuationParseResult(
                debug_info={
                    "unknown": True,
                    "continuation_key": action,
                    "continuation_entry": payload,
                }
            )
        )
    elif family == "untyped-action":
        validate_and_finalize_message({}, payload, None, action)
    elif family == "unknown-message-type":
        validate_and_finalize_message(
            {"timestamp": value}, {renderer: {}}, renderer, "addChatItemAction"
        )
    else:
        _emit_parse_diagnostics(
            {} if family == "empty-action-parse" else {"timestamp": value},
            payload,
            action,
            renderer,
            {"newField"} if family == "missing-keys" else set(),
        )


def test_unknown_action_aggregate_quota_publishes_complete_json_files(
    sample_dir, monkeypatch
):
    _emit("unknown-action", 0)

    def reject_repeat_publication(*_args):
        pytest.fail("Duplicate action reached sample publication")

    with monkeypatch.context() as repeat:
        repeat.setattr(
            redaction, "_write_or_validate_sample", reject_repeat_publication
        )
        _emit("unknown-action", 0)

    for index in range(1, 100):
        _emit("unknown-action", index)
    files = set(sample_dir.iterdir())
    assert len(files) == 50
    actions = set()
    for path in files:
        action = json.loads(path.read_bytes())["action"]
        (name,) = action
        assert action[name] == {"value": 0}
        assert path.match(f"youtube-unknown-action-{name.lower()}-*.json")
        actions.add(name)
    assert actions == {f"futureAction{index}" for index in range(50)}
    # The aggregate reservation happens first: the rejected fifty labels do
    # not enter either the per-label digest map or the warning-key set.
    assert len(redaction._DEBUG_SAMPLE_DIGESTS) == 51
    assert len(redaction._DEBUG_SAMPLE_LIMIT_WARNED) == 1
    assert sum(map(len, redaction._DEBUG_SAMPLE_DIGESTS.values())) == 100

    sanitize = redaction.sanitize_for_log

    def reject_quota_exhausted_payload(value):
        assert not isinstance(value, dict), (
            "Action payload was sanitized after quota filled"
        )
        return sanitize(value)

    # Router debug logs also sanitize dicts. Isolate sample sanitization here.
    monkeypatch.setattr(actions_router, "debug_log", lambda *_: None)
    monkeypatch.setattr(redaction, "sanitize_for_log", reject_quota_exhausted_payload)
    _emit("unknown-action", 0)
    _emit("unknown-action", 100)
    assert set(sample_dir.iterdir()) == files


@pytest.mark.parametrize(
    ("family", "label_stem"),
    [
        ("unknown-continuation", "futureaction"),
        ("untyped-action", "futureaction"),
        ("unknown-message-type", "futurerenderer"),
        ("empty-action-parse", "futureaction"),
        ("missing-keys", "futurerenderer"),
    ],
)
def test_other_youtube_anomalies_respect_label_and_aggregate_quota(
    sample_dir, monkeypatch, family, label_stem
):
    samples = {}

    def record_sample(path, serialized, _directory_fd):
        assert samples.setdefault(path, serialized) == serialized

    monkeypatch.setattr(redaction, "_write_or_validate_sample", record_sample)
    group_name = f"youtube-{family}"
    directory = str(sample_dir.absolute())
    group_key = (directory, group_name)
    first_label = (directory, f"{group_name}-{label_stem}0")
    digests = redaction._DEBUG_SAMPLE_DIGESTS

    _emit(family, 0)
    _emit(family, 0)
    assert len(samples) == len(digests[first_label]) == len(digests[group_key]) == 1

    for value in range(1, 11):
        _emit(family, 0, value)
    assert len(samples) == len(digests[first_label]) == len(digests[group_key]) == 10
    assert {first_label} == redaction._DEBUG_SAMPLE_LIMIT_WARNED

    for index in range(1, 42):
        _emit(family, index)
    assert len(samples) == len(digests[group_key]) == 50
    assert set(digests) == {group_key} | {
        (directory, f"{group_name}-{label_stem}{index}") for index in range(41)
    }
    assert {first_label, group_key} == redaction._DEBUG_SAMPLE_LIMIT_WARNED
    assert {path.stem.rsplit("-", 1)[0] for path in samples} == {
        f"{group_name}-{label_stem}{index}" for index in range(41)
    }
    assert all(path.parent == sample_dir and path.suffix == ".json" for path in samples)
    assert not list(sample_dir.iterdir())


def test_youtube_label_limit_is_independent_of_remaining_group_slots(sample_dir):
    for value in range(11):
        _emit("unknown-action", 0, value)
    _emit("unknown-action", 1)
    assert len(list(sample_dir.glob("*.json"))) == 11
    group = redaction._DEBUG_SAMPLE_DIGESTS[
        (str(sample_dir.absolute()), "youtube-unknown-action")
    ]
    assert len(group) == 11


def test_failed_youtube_writes_release_group_and_novel_label_claims(
    sample_dir, monkeypatch
):
    with monkeypatch.context() as failing:

        def fail_directory(_directory):
            raise OSError("disk full")

        failing.setattr(redaction, "_prepare_sample_directory", fail_directory)
        for index in range(100):
            _emit("unknown-action", index)
    assert redaction._DEBUG_SAMPLE_DIGESTS == {}
    assert not sample_dir.exists()
    for index in range(100):
        _emit("unknown-action", index)
    assert len(list(sample_dir.glob("*.json"))) == 50
    assert len(redaction._DEBUG_SAMPLE_DIGESTS) == 51


@pytest.mark.parametrize("missing_opt_in", ["environment", "debug_logging"])
def test_youtube_anomalies_still_require_explicit_debug_opt_in(
    sample_dir, monkeypatch, missing_opt_in
):
    if missing_opt_in == "environment":
        monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES")
    else:
        debugging.set_log_level("info")
    for index in range(100):
        _emit("unknown-action", index)
    assert not Path(sample_dir).exists()
    assert redaction._DEBUG_SAMPLE_DIGESTS == {}
