# SPDX-License-Identifier: MIT

"""String manipulation helpers."""

import re
from typing import Any


def regex_search(
    text: str,
    pattern: str,
    group: int = 1,
    default: Any = None,
) -> str | Any:
    """Search ``text`` for ``pattern`` and return the matched group.

    Args:
        text: String to search.
        pattern: Regular expression pattern.
        group: Match group to return.
        default: Value returned when there is no match.

    Returns:
        The matched group string, or ``default``.
    """
    match = re.search(pattern, text)
    return match.group(group) if match else default


def get_title_of_webpage(html: str) -> str | None:
    """Extract the text content of the first ``<title>`` tag in ``html``."""
    return regex_search(html, "<title(?:[^>]*)>(.*?)</title>")


def wrap_as_list(item: Any) -> list | tuple:
    """Wraps an item in a list, if it is not already iterable.

    :param item: The item to wrap
    :type item: object
    :return: The wrapped item
    :rtype: list | tuple
    """
    if not isinstance(item, (list, tuple)):
        item = [item]
    return item


def remove_prefixes(
    text: str, prefixes: str | list[str] | tuple[str, ...]
) -> str:
    """Remove each prefix in ``prefixes`` from the start of ``text`` in
    order.
    """
    for prefix in wrap_as_list(prefixes):
        text = text.removeprefix(prefix)
    return text


def remove_suffixes(
    text: str, suffixes: str | list[str] | tuple[str, ...]
) -> str:
    """Remove each suffix in ``suffixes`` from the end of ``text`` in order."""
    for suffix in wrap_as_list(suffixes):
        text = text.removesuffix(suffix)
    return text


def camel_case_split(word: str) -> str:
    """Convert a camelCase or PascalCase string to lowercase
    underscore_separated form.
    """
    return "_".join(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)", word)).lower()


def replace_with_underscores(text: str, sep: str = "-") -> str:
    """Replace all occurrences of ``sep`` in ``text`` with underscores."""
    return text.replace(sep, "_")


def contains_any_hint(text: str, hints: tuple[str, ...]) -> bool:
    """Return True if any hint string appears in the lowercased ``text``."""
    return any(hint in text.lower() for hint in hints)
