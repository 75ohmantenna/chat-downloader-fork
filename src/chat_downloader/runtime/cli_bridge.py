# SPDX-License-Identifier: MIT

"""Helpers for bridging CLI/runtime kwargs into ChatDownloader calls."""

from typing import Any

from chat_downloader.models import (
    CHAT_PARAM_NAMES,
    INIT_PARAM_NAMES,
    RUN_PARAM_NAMES,
)


def categorize_parameters(
    kwargs: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Separate parameters into init, chat-request, and runtime-control args.

    Raises:
        TypeError: If any supplied keyword is not recognized by the public run()
            surface.
    """
    known = INIT_PARAM_NAMES | CHAT_PARAM_NAMES | RUN_PARAM_NAMES
    unknown = sorted(key for key in kwargs if key not in known)
    if unknown:
        msg = (
            "run() received unknown keyword argument(s): "
            f"{unknown}. Valid names are: "
            f"init={sorted(INIT_PARAM_NAMES)}, "
            f"chat={sorted(CHAT_PARAM_NAMES)}, "
            f"run={sorted(RUN_PARAM_NAMES)}."
        )
        raise TypeError(msg)

    init_params = {k: v for k, v in kwargs.items() if k in INIT_PARAM_NAMES}
    chat_params = {k: v for k, v in kwargs.items() if k in CHAT_PARAM_NAMES}
    run_params = {k: v for k, v in kwargs.items() if k in RUN_PARAM_NAMES}
    return init_params, chat_params, run_params
