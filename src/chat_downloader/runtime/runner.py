# SPDX-License-Identifier: MIT

"""Top-level runtime helpers for executing chat download sessions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from requests.exceptions import ConnectionError as RequestsConnectionError
from requests.exceptions import RequestException

from chat_downloader.debugging import (
    TestingException,
    TestingModes,
    log,
    set_testing_mode,
)
from chat_downloader.errors import (
    ChatDownloaderError,
    ChatGeneratorError,
    ParsingError,
)
from chat_downloader.models import DEFAULT_MAX_SEEN_MESSAGE_IDS, RunConfig
from chat_downloader.sites._message_dedup import _FormattedMessageDeduplicator

from .cli_bridge import categorize_parameters

SITE_CHANGE_ERROR_HINT = (
    "This usually means the site response changed. Re-run with "
    "--logging debug for details."
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from chat_downloader.sites.models import Chat


class _ClosableDownloader(Protocol):
    def close(self) -> None: ...


def _configure_testing_mode(run_config: RunConfig) -> None:
    """Apply the debug testing mode represented by a typed run config."""
    if run_config.exit_on_debug:
        set_testing_mode(TestingModes.EXIT_ON_DEBUG)
    elif run_config.pause_on_debug:
        set_testing_mode(TestingModes.PAUSE_ON_DEBUG)
    else:
        set_testing_mode(TestingModes.NONE)


def _classify_run_error(e: Exception) -> str:
    """Return the user-facing error string for a run-loop exception.

    ChatGeneratorError/ParsingError are ChatDownloaderError subclasses, so
    they must be tested before the parent class.
    """
    if isinstance(e, (ChatGeneratorError, ParsingError, TestingException)):
        return f"{e}. {SITE_CHANGE_ERROR_HINT}"
    if isinstance(e, RequestsConnectionError):
        return (
            "Unable to establish a connection. Please check your "
            f"internet connection. {e}"
        )
    return str(e)


def _finalize_run(
    chat: Chat | None,
    downloader: _ClosableDownloader | None,
    *,
    primary_error: bool,
) -> None:
    """Close chat and downloader.

    Suppress errors only when a primary error already occurred so the
    original exception is not obscured.
    """
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

    if chat is not None and not primary_error:
        write_error_count = getattr(chat, "write_error_count", 0)
        if write_error_count > 0:
            msg = f"{write_error_count} output writer(s) reported errors during close"
            raise ChatDownloaderError(msg)

    if downloader is not None:
        try:
            downloader.close()
        except Exception as e:
            if primary_error:
                log("warning", f"Error closing downloader session(s): {e}")
            else:
                raise


@dataclass(slots=True)
class RunResult:
    """Structured result from :func:`execute_run`."""

    success: bool = False
    message_count: int = 0
    interrupted: bool = False
    error_message: str | None = None


def create_message_callback(
    *,
    quiet: bool,
    chat: Chat,
    max_seen_message_ids: int = DEFAULT_MAX_SEEN_MESSAGE_IDS,
) -> Callable[[dict[str, Any]], None]:
    """Create a callback function for processing retrieved messages."""
    if quiet:
        return lambda _: None

    deduplicator = _FormattedMessageDeduplicator(max_seen_message_ids)

    def deduplicating_callback(message: dict[str, Any]) -> None:
        if deduplicator.should_emit(message):
            chat.print_formatted(message)

    return deduplicating_callback


def execute_run(
    downloader_cls: type,
    *,
    propagate_interrupt: bool = False,
    **kwargs: Any,
) -> RunResult:
    """Execute a complete chat download session with error handling.

    Returns:
        RunResult: Structured execution summary.
    """
    init_params, chat_params, run_params = categorize_parameters(kwargs)
    run_config = RunConfig.from_kwargs(**run_params)

    _configure_testing_mode(run_config)
    downloader = None
    result = RunResult()
    chat = None
    primary_error = False

    try:
        downloader = downloader_cls(**init_params)
        chat = downloader.get_chat(**chat_params)
        callback = create_message_callback(
            quiet=run_config.quiet,
            chat=chat,
            max_seen_message_ids=run_config.max_seen_message_ids,
        )

        for message in chat:
            result.message_count += 1
            callback(message)

        result.success = True
        log("info", "Finished retrieving chat messages.")

    except (
        ChatDownloaderError,
        RequestException,
        TestingException,
        OSError,
        ValueError,
    ) as e:
        primary_error = True
        result.error_message = _classify_run_error(e)
        log("error", result.error_message)
    except KeyboardInterrupt:
        primary_error = True
        result.interrupted = True
        result.error_message = "Keyboard Interrupt"
        if propagate_interrupt:
            raise
        log("error", result.error_message)

    finally:
        try:
            _finalize_run(chat, downloader, primary_error=primary_error)
        except ChatDownloaderError:
            primary_error = True
            result.success = False
            result.error_message = (
                "One or more output writers reported errors during close"
            )

    return result
