# SPDX-License-Identifier: MIT

"""Shared infrastructure for the models package.

Provides the CLI-metadata helper, field-default helper, and the
DEFAULT_* constants that are re-exported via :mod:`chat_downloader.models`.
"""

from __future__ import annotations

import dataclasses
from typing import Any

DEFAULT_MAX_ATTEMPTS: int = 15
DEFAULT_MESSAGE_RECEIVE_TIMEOUT: float = 0.1
DEFAULT_BUFFER_SIZE: int = 4096


def _cli(
    description: str, group: str = "general", flags: list[str] | None = None
) -> dict[str, Any]:
    """Build the ``"cli"`` metadata dict for a dataclass field.

    :param description: Help text shown in ``--help`` output.
    :param group: Argument group name (must match a group in ``cli.main``).
    :param flags: Additional short-form flags, e.g. ``["-s"]``.
    """
    m: dict[str, Any] = {"help": description, "group": group}
    if flags:
        m["flags"] = flags
    return m


def get_field_default(f: dataclasses.Field[Any]) -> Any:
    """Return the default value for a dataclass field.

    Calls ``default_factory`` if present; returns ``None`` for fields with no
    default (which should not appear on public dataclasses here).
    """
    if f.default is not dataclasses.MISSING:
        return f.default
    if f.default_factory is not dataclasses.MISSING:
        return f.default_factory()
    return None
