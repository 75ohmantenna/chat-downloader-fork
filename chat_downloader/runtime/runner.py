# SPDX-License-Identifier: MIT

"""Top-level runtime helpers for executing chat download sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException

from chat_downloader.debugging import TestingException, log
from chat_downloader.errors import (
    ChatDownloaderError,
    ChatGeneratorError,
    ParsingError,
)
from chat_downloader.models import DEFAULT_MAX_SEEN_MESSAGE_IDS, RunConfig
from chat_downloader.sites.models import (
    SUPERCHAT_DEDUP_TYPES,
    _SeenMessageCache,
)

from .cli_bridge import categorize_parameters
from .testing import setup_testing_mode

SITE_CHANGE_ERROR_HINT = (
    "This usually means the site response changed. Re-run with "
    "--logging debug for details."
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.sites.models import Chat


@dataclass(slots=True)
class RunResult:
    """Structured result from :func:`execute_run`."""

    success: bool = False
    message_count: int = 0
    interrupted: bool = False
    error_message: str | None = None


def create_message_callback(
    quiet: bool,
    chat: Chat,
    *,
    max_seen_message_ids: int = DEFAULT_MAX_SEEN_MESSAGE_IDS,
) -> Callable[[dict[str, Any]], None]:
    """Create a callback function for processing retrieved messages."""
    if quiet:
        return lambda message: None

    cache = _SeenMessageCache(max_seen_message_ids)

    def deduplicating_callback(message: dict[str, Any]) -> None:
        message_type = message.get("message_type")
        message_id = message.get("message_id")

        if message_type in SUPERCHAT_DEDUP_TYPES and message_id:
            is_new, _ = cache.register(message_id)
            if not is_new:
                return

        chat.print_formatted(message)

    return deduplicating_callback


def execute_run(
    downloader_cls: type,
    propagate_interrupt: bool = False,
    **kwargs: Any,
) -> RunResult:
    """Execute a complete chat download session with error handling.

    Returns:
        RunResult: Structured execution summary.
    """
    init_params, chat_params, run_params = categorize_parameters(kwargs)
    run_config = RunConfig.from_kwargs(**run_params)

    setup_testing_mode(
        {
            "exit_on_debug": run_config.exit_on_debug,
            "pause_on_debug": run_config.pause_on_debug,
        },
    )
    downloader = None
    result = RunResult()
    chat = None
    primary_error = False

    try:
        downloader = downloader_cls(**init_params)
        chat = downloader.get_chat(**chat_params)
        callback = create_message_callback(
            run_config.quiet,
            chat,
            max_seen_message_ids=run_config.max_seen_message_ids,
        )

        for message in chat:
            result.message_count += 1
            callback(message)

        result.success = True
        log("info", "Finished retrieving chat messages.")

    except (ChatGeneratorError, ParsingError, TestingException) as e:
        primary_error = True
        result.error_message = f"{e}. {SITE_CHANGE_ERROR_HINT}"
        log("error", result.error_message)
    except ChatDownloaderError as e:
        primary_error = True
        result.error_message = str(e)
        log("error", e)
    except RequestsConnectionError as e:
        primary_error = True
        result.error_message = (
            f"Unable to establish a connection. Please check your "
            f"internet connection. {e}"
        )
        log("error", result.error_message)
    except RequestException as e:
        primary_error = True
        result.error_message = str(e)
        log("error", e)
    except KeyboardInterrupt:
        primary_error = True
        result.interrupted = True
        result.error_message = "Keyboard Interrupt"
        if propagate_interrupt:
            raise
        log("error", result.error_message)

    finally:
        if chat is not None and hasattr(chat, "close"):
            try:
                chat.close()
            except (OSError, ValueError) as e:
                log("warning", f"Error finalizing chat output: {e}")
            except Exception as e:
                if primary_error:
                    log("warning", f"Error finalizing chat output: {e}")
                else:
                    raise

        if downloader is not None:
            try:
                downloader.close()
            except Exception as e:
                if primary_error:
                    log("warning", f"Error closing downloader session(s): {e}")
                else:
                    raise

    return result
