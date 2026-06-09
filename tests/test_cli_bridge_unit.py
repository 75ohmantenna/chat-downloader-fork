# SPDX-License-Identifier: MIT

from __future__ import annotations

import pytest

from chat_downloader.models import RUN_PARAM_NAMES
from chat_downloader.runtime.cli_bridge import categorize_parameters


def test_categorize_parameters_splits_known_kwargs() -> None:
    init_params, chat_params, run_params = categorize_parameters(
        {
            "headers": {"User-Agent": "UA"},
            "proxy": "http://proxy:8080",
            "url": "https://example.invalid/watch?v=1",
            "max_messages": 25,
            "quiet": True,
            "max_seen_message_ids": 123,
            "exit_on_debug": True,
        },
    )

    assert init_params == {
        "headers": {"User-Agent": "UA"},
        "proxy": "http://proxy:8080",
    }
    assert chat_params == {
        "url": "https://example.invalid/watch?v=1",
        "max_messages": 25,
    }
    assert run_params == {
        "quiet": True,
        "max_seen_message_ids": 123,
        "exit_on_debug": True,
    }


def test_categorize_parameters_rejects_unknown_kwargs() -> None:
    with pytest.raises(TypeError) as excinfo:
        categorize_parameters(
            {"url": "https://example.invalid/watch?v=1", "typo": True}
        )

    message = str(excinfo.value)
    assert "unknown keyword argument" in message
    assert "typo" in message
    assert "init=" in message
    assert "chat=" in message
    assert "run=" in message


def test_runtime_controls_are_owned_by_run_param_names() -> None:
    expected = {
        "quiet",
        "max_seen_message_ids",
        "exit_on_debug",
        "pause_on_debug",
    }
    assert expected.issubset(RUN_PARAM_NAMES)
