# SPDX-License-Identifier: MIT

"""Console script for chat_downloader."""

import argparse
import re
import signal
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
    """Translate SIGTERM into KeyboardInterrupt so the runner's finally
    block flushes writers. A second signal restores the default handler
    so a stuck shutdown can still be force-killed.

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
        try:
            signal.signal(sig, handler)
        except (AttributeError, ValueError, OSError):
            # The signal may be unavailable (e.g. SIGTERM on some Windows
            # configs) or we may not be on the main thread; soft fail so
            # library callers using CLI helpers from a worker keep working.
            pass


def main(cli_args: Sequence[str] | None = None) -> None:
    """Parse CLI arguments and run the chat downloader.

    Args:
        cli_args: Argument list to parse; defaults to ``sys.argv[1:]``.
    """
    _install_cli_signal_handlers()

    parser = argparse.ArgumentParser(
        description=__summary__,
    )
    parser.prog = __program__

    parser.add_argument("--version", action="version", version=__version__)

    _chat_info = _build_field_info(ChatRequest)
    _init_info = _build_field_info(DownloaderConfig)
    _run_info = _build_field_info(RunConfig)

    def add_param(
        param_type: Literal["chat", "init", "run"],
        group: _ArgumentTarget,
        *keys: str,
        **kwargs: object,
    ) -> None:
        if param_type == "chat":
            info = _chat_info
        elif param_type == "init":
            info = _init_info
        else:
            info = _run_info
        key = keys[0].lstrip("-")
        field_info = _lookup_field_info(
            info,
            param_type=param_type,
            key=key,
        )
        arg_names = list(keys)
        for flag in field_info.get("flags", ()):
            if flag not in arg_names:
                arg_names.append(flag)
        arg_kwargs = dict(field_info)
        arg_kwargs.pop("flags", None)
        arg_kwargs.update(kwargs)
        group.add_argument(*arg_names, **arg_kwargs)

    def add_chat_param(
        group: _ArgumentTarget,
        *keys: str,
        **kwargs: object,
    ) -> None:
        add_param("chat", group, *keys, **kwargs)

    def add_init_param(
        group: _ArgumentTarget,
        *keys: str,
        **kwargs: object,
    ) -> None:
        add_param("init", group, *keys, **kwargs)

    def add_run_param(
        group: _ArgumentTarget,
        *keys: str,
        **kwargs: object,
    ) -> None:
        add_param("run", group, *keys, **kwargs)

    add_chat_param(parser, "url")

    time_group = parser.add_argument_group("Timing Arguments")

    add_chat_param(time_group, "--start_time", "-s")
    add_chat_param(time_group, "--end_time", "-e")

    # Specify message types/groups
    type_group = parser.add_argument_group("Message Type Arguments")
    type_options = type_group.add_mutually_exclusive_group()

    add_chat_param(type_options, "--message_types", type=splitter)
    add_chat_param(type_options, "--message_groups", type=splitter)

    retry_group = parser.add_argument_group(
        "Retry Arguments",
    )  # what to do when an error occurs
    add_chat_param(retry_group, "--max_attempts", type=int)
    add_chat_param(retry_group, "--retry_timeout", type=float)
    add_chat_param(
        retry_group,
        "--interruptible_retry",
        type=str2bool,
        nargs="?",
        const=True,
    )

    termination_group = parser.add_argument_group("Termination Arguments")
    add_chat_param(termination_group, "--max_messages", type=int)
    add_chat_param(termination_group, "--inactivity_timeout", type=float)
    add_chat_param(termination_group, "--timeout", type=float)

    # Formatting
    format_group = parser.add_argument_group("Format Arguments")
    add_chat_param(format_group, "--format")
    add_chat_param(format_group, "--format_file")

    youtube_group = parser.add_argument_group(
        "[Site Specific] YouTube Arguments"
    )
    add_chat_param(youtube_group, "--chat_type", choices=["live", "top"])
    add_chat_param(youtube_group, "--ignore", type=splitter)

    twitch_group = parser.add_argument_group("[Site Specific] Twitch Arguments")
    add_chat_param(twitch_group, "--message_receive_timeout", type=float)
    add_chat_param(twitch_group, "--buffer_size", type=int)

    output_group = parser.add_argument_group("Output Arguments")
    add_chat_param(output_group, "--output", "-o", action="append")
    add_chat_param(
        output_group, "--overwrite", type=str2bool, nargs="?", const=True
    )
    add_chat_param(
        output_group, "--sort_keys", type=str2bool, nargs="?", const=True
    )

    # Debugging only available from the CLI
    debug_group = parser.add_argument_group("Debugging/Testing Arguments")

    on_debug_options = debug_group.add_mutually_exclusive_group()
    add_run_param(
        on_debug_options,
        "--pause_on_debug",
        action="store_true",
    )
    add_run_param(
        on_debug_options,
        "--exit_on_debug",
        action="store_true",
    )

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
    add_run_param(
        debug_group,
        "--quiet",
        "-q",
        action="store_true",
    )

    # INIT PARAMS
    init_group = parser.add_argument_group("Initialisation Arguments")
    add_init_param(init_group, "--cookies", "-c")
    add_init_param(init_group, "--proxy", "-p")
    add_init_param(init_group, "--connect_timeout", type=float)
    add_init_param(init_group, "--read_timeout", type=float)
    add_init_param(
        init_group, "--request_profile", choices=sorted(REQUEST_PROFILES)
    )
    add_init_param(
        init_group,
        "--auto_profile_fallback",
        type=str2bool,
        nargs="?",
        const=True,
    )
    add_init_param(init_group, "--twitch_client_id")
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

    _rename_default_argument_groups(parser)

    args = parser.parse_args(args=cli_args)

    # Modify debugging args:
    if args.testing:  # (only for CLI)
        args.logging = "debug"
        args.pause_on_debug = True

    if args.verbose:
        args.logging = "debug"

    if args.logging == "none":
        disable_logger()
    else:
        set_log_level(args.logging)

    # Build headers dict from --user-agent / --header flags
    d = vars(args).copy()
    d.pop("logging", None)
    d.pop("testing", None)
    d.pop("verbose", None)
    request_profile = d.get("request_profile")
    headers: dict[str, str] = get_request_profile_headers(request_profile)
    user_agent = d.pop("user_agent", None)
    headers_list = d.pop("headers_list", None)
    if user_agent:
        headers["User-Agent"] = user_agent
    if headers_list:
        headers.update(headers_list)
    if headers:
        d["headers"] = headers

    # Run with these arguments
    run(**d)
