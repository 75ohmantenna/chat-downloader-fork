# SPDX-License-Identifier: MIT

from __future__ import annotations

from pathlib import Path

import pytest

import chat_downloader.debugging as debugging
import chat_downloader.redaction as redaction
from chat_downloader.sites.youtube.continuation import _log_continuation_debug_info
from chat_downloader.sites.youtube.continuations import ContinuationParseResult
from chat_downloader.sites.youtube.parsing.actions_handlers_validation import (
    _emit_parse_diagnostics,
    validate_and_finalize_message,
)
from chat_downloader.sites.youtube.parsing.actions_router import process_action
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
        process_action({action: payload})
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


@pytest.mark.parametrize(
    "family",
    [
        "unknown-action",
        "unknown-continuation",
        "untyped-action",
        "unknown-message-type",
        "empty-action-parse",
        "missing-keys",
    ],
)
def test_one_hundred_novel_youtube_labels_have_fixed_aggregate_quota(
    sample_dir, family
):
    for index in range(100):
        _emit(family, index)
    files = list(sample_dir.glob("*.json"))
    assert len(files) == 50
    assert all(path.name.startswith(f"youtube-{family}-") for path in files)
    # The aggregate reservation happens first: the rejected fifty labels do
    # not enter either the per-label digest map or the warning-key set.
    assert len(redaction._DEBUG_SAMPLE_DIGESTS) == 51
    assert len(redaction._DEBUG_SAMPLE_LIMIT_WARNED) == 1
    assert sum(map(len, redaction._DEBUG_SAMPLE_DIGESTS.values())) == 100
    _emit(family, 0)
    assert list(sample_dir.glob("*.json")) == files


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
