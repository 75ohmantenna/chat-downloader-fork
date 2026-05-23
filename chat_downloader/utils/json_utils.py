# SPDX-License-Identifier: MIT

"""JSON parsing, flattening, and deep-merge helpers."""

import collections.abc
import json
from typing import Any

_MAX_FLATTEN_DEPTH = 50
_TRUNCATED_KEY = "__chat_downloader_truncated__"


def try_parse_json(text: str, default: Any = None) -> Any:
    """Parse ``text`` as JSON, returning ``default`` on any parse error."""
    try:
        return json.loads(text)
    except (json.decoder.JSONDecodeError, TypeError):
        return default


def flatten_json(original_json: dict | list) -> dict:
    """Flatten a nested dict/list into a single-level dict with dot-separated
    keys.

    Args:
        original_json: Nested dictionary or list to flatten.

    Returns:
        A flat dict where nested paths are joined with ``"."``.
    """
    final: dict[str, Any] = {}

    def flatten(item: Any, prefix: str = "", depth: int = 0) -> None:
        if depth > _MAX_FLATTEN_DEPTH:
            final[prefix[:-1] if prefix else _TRUNCATED_KEY] = str(item)
            return
        if isinstance(item, dict):
            for key in item:
                flatten(item[key], f"{prefix}{key}.", depth + 1)
        elif isinstance(item, list):
            for index in range(len(item)):
                flatten(item[index], f"{prefix}{index}.", depth + 1)
        else:
            final[prefix[:-1]] = item

    flatten(original_json)

    return final


def nested_update(
    d: dict[str, Any],
    u: collections.abc.Mapping[str, Any],
) -> dict[str, Any]:
    """Recursively merge mapping ``u`` into dict ``d``, returning ``d``.

    Nested dicts are merged; all other value types in ``u`` overwrite ``d``.

    Args:
        d: Target dict updated in place.
        u: Mapping of values to merge into ``d``.

    Returns:
        The updated ``d``.
    """
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            a = d.get(k, {})
            if isinstance(a, dict):
                d[k] = nested_update(a, v)
            else:
                d[k] = v
        else:
            d[k] = v
    return d
