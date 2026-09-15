# SPDX-License-Identifier: MIT

"""Top-level runtime helpers for executing chat download sessions."""

from __future__ import annotations

from collections import Counter
from contextlib import ExitStack
from dataclasses import dataclass, field
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
from chat_downloader.redaction import sanitize_for_log
from chat_downloader.sites._message_dedup import _FormattedMessageDeduplicator

from .capture_checkpoint import CaptureCheckpoint, checkpoint_lock
from .capture_verification import capture_paths, validate_verification, verify_capture
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
    message_type_counts: dict[str, int] = field(default_factory=dict)
    parity_status: str = "not_requested"
    termination_reason: str = "error"


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


def _log_run_summary(
    chat: Chat | None,
    message_count: int,
    message_type_counts: dict[str, int],
    result: RunResult | None = None,
) -> None:
    """Log status and final message/writer counts, including failed runs."""
    output_dispatcher = getattr(chat, "_output_dispatcher", None)
    writer_summaries = (
        output_dispatcher.writer_summaries if output_dispatcher is not None else []
    )
    formatted_duplicates_suppressed = (
        output_dispatcher.formatted_duplicates_suppressed
        if output_dispatcher is not None
        else 0
    )
    chat_iterator = getattr(chat, "chat", None)
    deadline_prefetch_summary = getattr(
        chat_iterator,
        "deadline_prefetch_summary",
        None,
    )
    if callable(deadline_prefetch_summary):
        prefetched_after_deadline_count, deadline_prefetch_count_complete = (
            deadline_prefetch_summary()
        )
    else:
        prefetched_after_deadline_count = 0
        deadline_prefetch_count_complete = True
    summary = sanitize_for_log(
        {
            "success": result.success if result is not None else True,
            "termination_reason": result.termination_reason
            if result is not None
            else "completed",
            "parity_status": result.parity_status
            if result is not None
            else "not_requested",
            "message_count": message_count,
            "message_type_counts": message_type_counts,
            "formatted_duplicates_suppressed": formatted_duplicates_suppressed,
            "prefetched_after_deadline_count": prefetched_after_deadline_count,
            "deadline_prefetch_count_complete": deadline_prefetch_count_complete,
            "provider_diagnostics": getattr(chat, "diagnostics", {}),
            "output_writers": writer_summaries,
        }
    )
    for writer_summary in writer_summaries:
        if (
            not writer_summary["file_created"]
            and writer_summary["records_written"] == 0
        ):
            file_name = sanitize_for_log(writer_summary["file_name"])
            log(
                "info",
                "Lazy output file was not created because no records were "
                f"retrieved: {file_name}",
            )
    log("debug", f"Run summary: {summary}")


def execute_run(  # noqa: C901 — one capture error/finalization lifecycle
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
    message_type_counts: Counter[str] = Counter()
    chat = None
    primary_error = False
    checkpoint = None
    checkpoint_resources = ExitStack()
    checkpoint_bound = False
    if run_config.verify_output:
        result.parity_status = "not_run"

    try:
        if run_config.verify_output:
            validate_verification(chat_params, resume=bool(run_config.resume))
        if run_config.resume:
            checkpoint_resources.enter_context(checkpoint_lock(run_config.resume))
            checkpoint = CaptureCheckpoint(run_config.resume, chat_params)
        downloader = downloader_cls(**init_params)
        chat = downloader.get_chat(**chat_params)
        if run_config.verify_output:
            capture_paths(chat)
        if checkpoint is not None:
            checkpoint.bind(chat)
            checkpoint_bound = True
        callback = create_message_callback(
            quiet=run_config.quiet,
            chat=chat,
            max_seen_message_ids=run_config.max_seen_message_ids,
        )

        for message in chat:
            result.message_count += 1
            if checkpoint is not None:
                checkpoint.observe(message)
            message_type = message.get("message_type")
            counter_key = message_type if isinstance(message_type, str) else "<missing>"
            message_type_counts[counter_key] += 1
            callback(message)

        result.success = True
        result.termination_reason = str(
            getattr(chat, "diagnostics", {}).get("termination_reason", "completed")
        )
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
        result.termination_reason = "interrupted"
        result.error_message = "Keyboard Interrupt"
        if propagate_interrupt:
            raise
        log("error", result.error_message)

    finally:
        with checkpoint_resources:
            try:
                _finalize_run(chat, downloader, primary_error=primary_error)
            except ChatDownloaderError:
                primary_error = True
                result.success = False
                result.termination_reason = "error"
                result.error_message = (
                    "One or more output writers reported errors during close"
                )
            try:
                if run_config.verify_output and result.success and chat is not None:
                    resets = (
                        tuple(
                            checkpoint.resets
                            + (
                                [checkpoint.total + 1]
                                if checkpoint.total and result.message_count
                                else []
                            )
                        )
                        if checkpoint is not None
                        else ()
                    )
                    result.parity_status = "failed"
                    verify_capture(
                        chat,
                        resets=resets,
                        allow_existing=checkpoint is not None and checkpoint.loaded,
                    )
                    result.parity_status = "passed"
                if checkpoint is not None and checkpoint_bound and chat is not None:
                    checkpoint.save(chat, result.message_count)
            except (ChatDownloaderError, OSError, ValueError) as error:
                result.success = False
                result.termination_reason = "error"
                result.error_message = str(error)
                log("error", result.error_message)

        result.message_type_counts = dict(sorted(message_type_counts.items()))
        _log_run_summary(chat, result.message_count, result.message_type_counts, result)

    return result
