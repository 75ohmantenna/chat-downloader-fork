# SPDX-License-Identifier: MIT

"""Command-line entry point for chat-downloader."""

from __future__ import annotations

import argparse
import contextlib
import signal
import sys
from typing import TYPE_CHECKING

from .chat_downloader import run
from .cli_args import (
    _add_chat_args,
    _add_debug_args,
    _add_format_site_output_args,
    _add_init_args,
    _add_retry_args,
    _build_request_headers,
    _ParamRegistrar,
    _rename_default_argument_groups,
)
from .debugging import disable_logger, install_cli_log_handler, log, set_log_level
from .metadata import __program__, __summary__, __version__

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import FrameType


def _install_cli_signal_handlers() -> None:
    """Translate SIGTERM to KeyboardInterrupt so the runner's finally flushes writers.

    SIGINT already raises KeyboardInterrupt. Wrap both to restore the default
    handler on a second signal, allowing stuck shutdowns to be force-killed.
    """
    state = {"triggered": False}

    def handler(signum: int, _frame: FrameType | None) -> None:
        if state["triggered"]:
            signal.signal(signum, signal.SIG_DFL)
            log("warning", "Second signal received; interrupting cleanup.")
            raise KeyboardInterrupt
        state["triggered"] = True
        log(
            "info",
            f"Signal {signum} received; finalizing output (send again to force exit).",
        )
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        # The signal may be unavailable (e.g. SIGTERM on some Windows
        # configs) or we may not be on the main thread; soft fail so
        # library callers using CLI helpers from a worker keep working.
        with contextlib.suppress(AttributeError, ValueError, OSError):
            signal.signal(sig, handler)


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the fully-configured argparse parser for the CLI."""
    parser = argparse.ArgumentParser(description=__summary__)
    parser.prog = __program__
    parser.add_argument("--version", action="version", version=__version__)

    reg = _ParamRegistrar()
    _add_chat_args(reg, parser)
    _add_retry_args(reg, parser)
    _add_format_site_output_args(reg, parser)
    _add_debug_args(reg, parser)
    _add_init_args(reg, parser)
    _rename_default_argument_groups(parser)

    return parser


def main(cli_args: Sequence[str] | None = None) -> None:
    """Parse cli_args (default sys.argv[1:]) and run the chat downloader."""
    _install_cli_signal_handlers()
    install_cli_log_handler()

    parser = _build_arg_parser()
    args = parser.parse_args(args=cli_args)

    if args.testing:  # (only for CLI)
        args.logging = "debug"
        args.pause_on_debug = True

    if args.verbose:
        args.logging = "debug"

    if args.logging == "none":
        disable_logger()
    else:
        set_log_level(args.logging)

    args_dict = vars(args).copy()
    for cli_only in ("logging", "testing", "verbose"):
        args_dict.pop(cli_only, None)

    headers = _build_request_headers(args_dict)
    if headers:
        args_dict["headers"] = headers

    result = run(**args_dict)
    if not result.success or result.interrupted:
        sys.exit(1)
