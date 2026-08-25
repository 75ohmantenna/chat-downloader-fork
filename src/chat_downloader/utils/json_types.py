# SPDX-License-Identifier: MIT

"""Typed JSON aliases and narrowing accessors for upstream API payloads.

Lets payload-parsing code narrow ``Any`` to concrete types without modeling
entire upstream APIs. Leaf module: imports nothing from chat_downloader.

Usage::

    from chat_downloader.utils.json_types import JSONDict, get_str, get_int

    def parse(payload: JSONDict) -> str:
        return get_str(payload, "title")
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

type JSONScalar = str | int | float | bool | None
type JSONList = list[JSONAny]
type JSONDict = dict[str, JSONAny]
type JSONAny = JSONScalar | JSONList | JSONDict


def get_str(d: Mapping[str, object], key: str, default: str = "") -> str:
    """Return ``d[key]`` as ``str``, or *default* if absent or wrong type."""
    v = d.get(key, default)
    return v if isinstance(v, str) else default


def get_int(d: Mapping[str, object], key: str, default: int = 0) -> int:
    """Return ``d[key]`` as ``int``, or *default* if absent or wrong type.

    ``bool`` is excluded even though it is an ``int`` subclass.
    """
    v = d.get(key, default)
    return v if isinstance(v, int) and not isinstance(v, bool) else default


def get_float(d: Mapping[str, object], key: str, default: float = 0.0) -> float:
    """Return ``d[key]`` as ``float``, or *default* if absent or wrong type."""
    v = d.get(key, default)
    if isinstance(v, bool):
        return default
    return float(v) if isinstance(v, (int, float)) else default


def get_bool(d: Mapping[str, object], key: str, *, default: bool = False) -> bool:
    """Return ``d[key]`` as ``bool``, or *default* if absent or wrong type."""
    v = d.get(key, default)
    return v if isinstance(v, bool) else default


def get_dict(d: Mapping[str, object], key: str) -> JSONDict:
    """Return ``d[key]`` as a ``dict``, or ``{}`` if absent or wrong type."""
    v = d.get(key)
    return v if isinstance(v, dict) else {}


def get_list(d: Mapping[str, object], key: str) -> JSONList:
    """Return ``d[key]`` as a ``list``, or ``[]`` if absent or wrong type."""
    v = d.get(key)
    return v if isinstance(v, list) else []


def dig(d: Mapping[str, object], *path: str) -> JSONAny:
    """Walk nested dicts by key path; return ``None`` if any step is missing.

    Example::

        dig(payload, "header", "renderer", "title")
    """
    cur: object = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cast("JSONAny", cur)
