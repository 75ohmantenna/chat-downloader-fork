# SPDX-License-Identifier: MIT

"""Runtime helpers for ChatDownloader orchestration."""

from __future__ import annotations

from .chat_pipeline import (
    apply_message_limit,
    configure_chat,
    configure_formatter,
    configure_output_writer,
    configure_timeouts,
)
from .cli_bridge import categorize_parameters
from .config_guards import check_proxy_cookie_safety
from .runner import RunResult, create_message_callback, execute_run
from .session_lifecycle import (
    build_cookie,
    clear_all_cookies,
    close_sessions,
    create_session,
    get_cookie_value,
    propagate_cookie,
)
from .site_dispatch import (
    create_chat_for_site,
    execute_chat_generator,
    handle_unsupported_url,
    resolve_site_defaults,
    try_create_chat_from_sites,
    validate_url,
)
from .testing import setup_testing_mode

__all__ = [
    "RunResult",
    "apply_message_limit",
    "build_cookie",
    "categorize_parameters",
    "check_proxy_cookie_safety",
    "clear_all_cookies",
    "close_sessions",
    "configure_chat",
    "configure_formatter",
    "configure_output_writer",
    "configure_timeouts",
    "create_chat_for_site",
    "create_message_callback",
    "create_session",
    "execute_chat_generator",
    "execute_run",
    "get_cookie_value",
    "handle_unsupported_url",
    "propagate_cookie",
    "resolve_site_defaults",
    "setup_testing_mode",
    "try_create_chat_from_sites",
    "validate_url",
]
