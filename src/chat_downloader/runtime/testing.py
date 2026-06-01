# SPDX-License-Identifier: MIT

"""Testing-mode runtime helpers."""

from typing import Any

from chat_downloader.debugging import TestingModes, set_testing_mode


def setup_testing_mode(kwargs: dict[str, Any]) -> None:
    """Configure testing mode based on provided arguments."""
    if kwargs.get("exit_on_debug"):
        set_testing_mode(TestingModes.EXIT_ON_DEBUG)
    elif kwargs.get("pause_on_debug"):
        set_testing_mode(TestingModes.PAUSE_ON_DEBUG)
    else:
        set_testing_mode(TestingModes.NONE)
