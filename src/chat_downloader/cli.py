# SPDX-License-Identifier: MIT

"""Console script for chat_downloader."""

import argparse
import contextlib
import re
import signal
import sys
from collections.abc import Sequence
from dataclasses import fields as dc_fields
from types import FrameType
from typing import Any, Literal, Protocol, TypedDict

from .chat_downloader import run
from .debugging import disable_logger, log, set_log_level
from .metadata import __program__, __summary__, __version__
from .models import ChatRequest, DownloaderConfig, RunConfig, get_field_default
from .request_profiles import REQUEST_PROFILES, get_request_profile_headers

HEADER_NAME_PATTERN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class _CliFieldInfo(TypedDict):
    """CLI metadata derived from a dataclass field."""

    help: str
    default: object
    flags: list[str]


class _ArgumentTarget(Protocol):
    """Small argparse surface used by parsers, groups, and mutex groups."""

    def add_argument(
        self,
        *name_or_flags: str,
        **kwargs: Any,
    ) -> argparse.Action: ...


def splitter(s: str) -> list[str]:
    """Split a whitespace-, comma-, or semicolon-delimited string.

    Returns a list of stripped tokens.
    """
    return [item.strip() for item in re.split(r"[\s,;]+", s)]


def parse_header(value: str) -> tuple[str, str]:
    """Parse a ``NAME:VALUE`` string into a (name, value) tuple.

    Args:
        value: Raw header string in ``NAME:VALUE`` format.

    Returns:
        A (name, value) tuple with whitespace stripped from both parts.

    Raises:
        argparse.ArgumentTypeError: If the format is invalid or the name
            contains characters forbidden by RFC 7230.
    """
    key, sep, header_value = value.partition(":")
    if not sep:
        msg = f'Invalid header {value!r}. Expected the format "NAME:VALUE".'
        raise argparse.ArgumentTypeError(
            msg,
        )

    key = key.strip()
    header_value = header_value.strip()

    if not key:
        msg = f'Invalid header {value!r}. Expected the format "NAME:VALUE".'
        raise argparse.ArgumentTypeError(
            msg,
        )

    if (
        "\n" in key
        or "\r" in key
        or "\n" in header_value
        or "\r" in header_value
    ):
        err_msg = "Header keys and values cannot contain newline characters."
        raise argparse.ArgumentTypeError(err_msg)

    if not HEADER_NAME_PATTERN.fullmatch(key):
        err_msg = (
            f"Invalid header name {key!r}. Only RFC 7230 token characters "
            "are allowed."
        )
        raise argparse.ArgumentTypeError(err_msg)

    return key.title(), header_value


def str2bool(value: str | bool) -> bool:
    """Convert a CLI boolean string to a Python bool.

    Args:
        value: A bool or a string such as ``"true"``, ``"yes"``, ``"1"``,
            ``"false"``, ``"no"``, or ``"0"``.

    Returns:
        The corresponding bool value.

    Raises:
        argparse.ArgumentTypeError: If the string is not a recognised boolean.
    """
    if isinstance(value, bool):
        return value
    value = value.lower()
    match value:
        case "true" | "yes" | "t" | "y" | "1" | "enable":
            return True
        case "false" | "no" | "f" | "n" | "0" | "disable":
            return False
        case _:
            msg = f"Boolean value expected: {value} is not a boolean"
            raise argparse.ArgumentTypeError(
                msg,
            )


def _build_field_info(dc_class: type[Any]) -> dict[str, _CliFieldInfo]:
    """Build ``{field_name: {help, default}}`` from dataclass fields.

    Only fields with a ``"cli"`` key in their metadata are included.
    """
    result: dict[str, _CliFieldInfo] = {}
    for f in dc_fields(dc_class):
        meta = f.metadata.get("cli")
        if meta is None:
            continue
        result[f.name] = {
            "help": meta.get("help", ""),
            "default": get_field_default(f),
            "flags": list(meta.get("flags", ())),
        }
    return result


def _lookup_field_info(
    info: dict[str, _CliFieldInfo],
    *,
    param_type: Literal["chat", "init", "run"],
    key: str,
) -> _CliFieldInfo:
    """Return CLI field metadata, failing fast on wiring mistakes."""
    try:
        return info[key]
    except KeyError as exc:
        valid = ", ".join(sorted(info))
        msg = (
            f"CLI argument {key!r} is registered as a {param_type} parameter "
            f"but has no matching dataclass CLI metadata. Valid {param_type} "
            f"parameters: {valid}"
        )
        raise RuntimeError(msg) from exc


def _rename_default_argument_groups(parser: argparse.ArgumentParser) -> None:
    """Name argparse's built-in groups consistently in one isolated place."""
    parser._positionals.title = "Mandatory Arguments"
    parser._optionals.title = "General Arguments"


def _install_cli_signal_handlers() -> None:
    """Translate SIGTERM into KeyboardInterrupt so writers get flushed.

    The runner's finally block flushes writers on KeyboardInterrupt. A
    second signal restores the default handler so a stuck shutdown can
    still be force-killed.

    SIGINT is already raised as KeyboardInterrupt by the Python runtime,
    so we only wrap it to support the second-signal escape hatch.
    """
    state = {"triggered": False}

    def handler(signum: int, _frame: FrameType | None) -> None:
        if state["triggered"]:
            signal.signal(signum, signal.SIG_DFL)
            log("warning", "Second signal received; exiting immediately.")
            raise KeyboardInterrupt
        state["triggered"] = True
        log(
            "info",
            f"Signal {signum} received; finalizing output "
            "(send again to force exit).",
        )
        raise KeyboardInterrupt

    for sig in (signal.SIGINT, signal.SIGTERM):
        # The signal may be unavailable (e.g. SIGTERM on some Windows
        # configs) or we may not be on the main thread; soft fail so
        # library callers using CLI helpers from a worker keep working.
        with contextlib.suppress(AttributeError, ValueError, OSError):
            signal.signal(sig, handler)


def _build_request_headers(args_dict: dict[str, Any]) -> dict[str, str]:
    """Assemble the request headers dict from parsed CLI args.

    Mutates ``args_dict`` to remove the CLI-only ``user_agent`` and
    ``headers_list`` keys so they are not forwarded to :func:`run`. Header
    precedence: request-profile headers, then ``--user-agent``, then
    ``--header`` entries (later wins).
    """
    headers = get_request_profile_headers(args_dict.get("request_profile"))
    user_agent = args_dict.pop("user_agent", None)
    headers_list = args_dict.pop("headers_list", None)
    if user_agent:
        headers["User-Agent"] = user_agent
    if headers_list:
        headers.update(headers_list)
    return headers


class _ParamRegistrar:
    """Thin wrapper routing add_argument calls to the right field-info dict."""

    def __init__(self) -> None:
        """Initialise field-info caches for all three parameter groups."""
        self._chat = _build_field_info(ChatRequest)
        self._init = _build_field_info(DownloaderConfig)
        self._run = _build_field_info(RunConfig)

    def add(
        self,
        param_type: Literal["chat", "init", "run"],
        group: _ArgumentTarget,
        *keys: str,
        **kwargs: object,
    ) -> None:
        """Register one argument, merging dataclass metadata with overrides."""
        if param_type == "chat":
            info = self._chat
        elif param_type == "init":
            info = self._init
        else:
            info = self._run
        key = keys[0].lstrip("-")
        field_info = _lookup_field_info(info, param_type=param_type, key=key)
        arg_names = list(keys)
        for flag in field_info.get("flags", ()):
            if flag not in arg_names:
                arg_names.append(flag)
        arg_kwargs = dict(field_info)
        arg_kwargs.pop("flags", None)
        arg_kwargs.update(kwargs)
        group.add_argument(*arg_names, **arg_kwargs)

    def chat(
        self, group: _ArgumentTarget, *keys: str, **kwargs: object
    ) -> None:
        """Register a ChatRequest parameter."""
        self.add("chat", group, *keys, **kwargs)

    def init(
        self, group: _ArgumentTarget, *keys: str, **kwargs: object
    ) -> None:
        """Register a DownloaderConfig parameter."""
        self.add("init", group, *keys, **kwargs)

    def run(self, group: _ArgumentTarget, *keys: str, **kwargs: object) -> None:
        """Register a RunConfig parameter."""
        self.add("run", group, *keys, **kwargs)


def _add_chat_args(
    reg: _ParamRegistrar, parser: argparse.ArgumentParser
) -> None:
    """Register top-level chat and timing argument groups."""
    reg.chat(parser, "url")

    time_group = parser.add_argument_group("Timing Arguments")
    reg.chat(time_group, "--start_time", "-s")
    reg.chat(time_group, "--end_time", "-e")

    type_group = parser.add_argument_group("Message Type Arguments")
    type_options = type_group.add_mutually_exclusive_group()
    reg.chat(type_options, "--message_types", type=splitter)
    reg.chat(type_options, "--message_groups", type=splitter)


def _add_retry_args(
    reg: _ParamRegistrar, parser: argparse.ArgumentParser
) -> None:
    """Register retry and termination argument groups."""
    retry_group = parser.add_argument_group("Retry Arguments")
    reg.chat(retry_group, "--max_attempts", type=int)
    reg.chat(retry_group, "--retry_timeout", type=float)
    reg.chat(
        retry_group,
        "--interruptible_retry",
        type=str2bool,
        nargs="?",
        const=True,
    )

    termination_group = parser.add_argument_group("Termination Arguments")
    reg.chat(termination_group, "--max_messages", type=int)
    reg.chat(termination_group, "--inactivity_timeout", type=float)
    reg.chat(termination_group, "--timeout", type=float)


def _add_format_site_output_args(
    reg: _ParamRegistrar, parser: argparse.ArgumentParser
) -> None:
    """Register format, site-specific, and output argument groups."""
    format_group = parser.add_argument_group("Format Arguments")
    reg.chat(format_group, "--format")
    reg.chat(format_group, "--format_file")

    youtube_group = parser.add_argument_group(
        "[Site Specific] YouTube Arguments"
    )
    reg.chat(youtube_group, "--chat_type", choices=["live", "top"])
    reg.chat(youtube_group, "--ignore", type=splitter)

    twitch_group = parser.add_argument_group("[Site Specific] Twitch Arguments")
    reg.chat(twitch_group, "--message_receive_timeout", type=float)
    reg.chat(twitch_group, "--buffer_size", type=int)

    output_group = parser.add_argument_group("Output Arguments")
    reg.chat(output_group, "--output", "-o", action="append")
    reg.chat(output_group, "--overwrite", type=str2bool, nargs="?", const=True)
    reg.chat(output_group, "--sort_keys", type=str2bool, nargs="?", const=True)


def _add_debug_args(
    reg: _ParamRegistrar, parser: argparse.ArgumentParser
) -> None:
    """Register debugging/testing argument group."""
    debug_group = parser.add_argument_group("Debugging/Testing Arguments")

    on_debug_options = debug_group.add_mutually_exclusive_group()
    reg.run(on_debug_options, "--pause_on_debug", action="store_true")
    reg.run(on_debug_options, "--exit_on_debug", action="store_true")

    debug_options = debug_group.add_mutually_exclusive_group()
    debug_options.add_argument(
        "--logging",
        choices=["none", "debug", "info", "warning", "error", "critical"],
        help="Level of logging to display, defaults to info",
        default="info",
    )
    debug_options.add_argument(
        "--testing",
        action="store_true",
        help="Enable testing mode. This is equivalent to setting logging "
        "to debug and enabling pause_on_debug. Defaults to False",
    )
    debug_options.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print various debugging information. This is equivalent to "
        "setting logging to debug. Defaults to False",
    )
    reg.run(debug_group, "--quiet", "-q", action="store_true")


def _add_init_args(
    reg: _ParamRegistrar, parser: argparse.ArgumentParser
) -> None:
    """Register initialisation argument group."""
    init_group = parser.add_argument_group("Initialisation Arguments")
    reg.init(init_group, "--cookies", "-c")
    reg.init(init_group, "--proxy", "-p")
    reg.init(init_group, "--connect_timeout", type=float)
    reg.init(init_group, "--read_timeout", type=float)
    reg.init(init_group, "--request_profile", choices=sorted(REQUEST_PROFILES))
    reg.init(
        init_group,
        "--auto_profile_fallback",
        type=str2bool,
        nargs="?",
        const=True,
    )
    reg.init(init_group, "--twitch_client_id")
    init_group.add_argument(
        "--user-agent",
        dest="user_agent",
        default=None,
        metavar="UA",
        help="Override the User-Agent header for HTTP requests",
    )
    init_group.add_argument(
        "--header",
        dest="headers_list",
        action="append",
        default=None,
        type=parse_header,
        metavar="NAME:VALUE",
        help=(
            "Custom HTTP header (repeatable), e.g. --header "
            '"Accept-Language: en"'
        ),
    )


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
    """Parse CLI arguments and run the chat downloader.

    Args:
        cli_args: Argument list to parse; defaults to ``sys.argv[1:]``.
    """
    _install_cli_signal_handlers()

    parser = _build_arg_parser()
    args = parser.parse_args(args=cli_args)

    # Resolve CLI-only debugging flags
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

    # Run with these arguments
    result = run(**args_dict)
    if not result.success or result.interrupted:
        sys.exit(1)
