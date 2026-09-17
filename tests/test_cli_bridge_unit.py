# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.models import RUN_PARAM_NAMES
from chat_downloader.runtime.cli_bridge import categorize_parameters


def test_categorize_parameters_splits_known_kwargs() -> None:
    init = {"headers": {"User-Agent": "UA"}, "proxy": "http://proxy:8080"}
    chat = {"url": "https://example.invalid/watch?v=1", "max_messages": 25}
    run = {"quiet": True, "max_seen_message_ids": 123, "exit_on_debug": True}
    assert categorize_parameters(init | chat | run) == (init, chat, run)


def test_categorize_parameters_rejects_unknown_kwargs() -> None:
    with pytest.raises(TypeError) as excinfo:
        categorize_parameters(
            {"url": "https://example.invalid/watch?v=1", "typo": True}
        )
    for fragment in ("unknown keyword argument", "typo", "init=", "chat=", "run="):
        assert fragment in str(excinfo.value)


def test_runtime_controls_are_owned_by_run_param_names() -> None:
    assert {
        "quiet",
        "max_seen_message_ids",
        "exit_on_debug",
        "pause_on_debug",
    } <= RUN_PARAM_NAMES
