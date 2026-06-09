# SPDX-License-Identifier: MIT

"""Dictionary access and merge helpers used across the downloader."""

from __future__ import annotations

from typing import Any


def multi_get(
    dictionary: dict[Any, Any] | list[Any] | tuple[Any, ...],
    *keys: Any,
    default: Any = None,
) -> Any:
    """Traverse a nested structure by a sequence of keys/indices.

    Args:
        dictionary: The root dict, list, or tuple to traverse.
        *keys: Sequence of keys (for dicts) or integer indices (for
            lists/tuples) to follow in order.
        default: Value returned when a key is missing or a traversal step
            fails.

    Returns:
        The value reached by following all keys, or ``default``.
    """
    current = dictionary
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        elif isinstance(current, (list, tuple)) and isinstance(key, int):
            try:
                current = current[key]
            except IndexError:
                return default
        else:
            return default
    return current


def try_get_first_key(dictionary: dict[Any, Any], default: Any = None) -> Any:
    """Return the first key of ``dictionary``, or ``default`` if empty."""
    try:
        return next(iter(dictionary))
    except (StopIteration, TypeError):
        return default


def try_get_first_value(dictionary: dict[Any, Any], default: Any = None) -> Any:
    """Return the first value of ``dictionary``, or ``default`` if empty."""
    try:
        return next(iter(dictionary.values()))
    except (StopIteration, TypeError, AttributeError):
        return default


def move_to_dict(
    info: dict[str, Any],
    dict_name: str,
    *,
    replace_key: str | None = None,
    create_when_empty: bool = False,
    info_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Collect prefixed keys from *info* into a nested sub-dict.

    All keys in *info* whose name contains *replace_key* (defaults to
    ``dict_name + "_"``) are removed and their values are placed in a new
    dict under *info[dict_name]*.  If *info_keys* are given, only those
    specific keys are checked instead of the entire dict.

    Args:
        info: The source dict, mutated in place.
        dict_name: Name of the sub-dict key to create or update.
        replace_key: Prefix/substring to match and strip from keys.
            Defaults to ``dict_name + "_"``.
        create_when_empty: When ``True``, create the sub-dict even if no
            matching keys were found.
        info_keys: Restrict processing to these specific keys in *info*.

    Returns:
        The new sub-dict that was inserted (or updated) in *info*.
    """
    if replace_key is None:
        replace_key = dict_name + "_"

    new_dict: dict[str, Any] = {}
    keys_to_check = (
        list(info_keys) if info_keys else list(info.keys() if info else [])
    )

    for key in keys_to_check:
        if replace_key in key:
            info_item = info.pop(key, None)
            new_key = key.replace(replace_key, "")
            if info_item not in (None, [], {}):
                new_dict[new_key] = info_item

    if dict_name in info:
        info[dict_name].update(new_dict)
    elif create_when_empty or new_dict != {}:
        info[dict_name] = new_dict

    return new_dict
