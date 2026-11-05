# SPDX-License-Identifier: MIT

"""Stateless helpers shared by site downloaders.

These utilities hold no runtime state and do not depend on
``BaseChatDownloader``; keep them free of session or instance concerns so any
site module can import them without pulling in the base downloader.
"""

from __future__ import annotations

from typing import Any

from .remap import Remapper


def get_mapped_keys(remapping: dict[str, Any]) -> set[Any]:
    """Return the set of destination keys produced by ``remapping``.

    Args:
        remapping: Dict mapping source keys to ``Remapper`` objects or
            plain destination key strings.

    Returns:
        Set of all destination key values.
    """
    mapped_keys = set()
    for raw in remapping.values():
        mapped_keys.add(raw.new_key if isinstance(raw, Remapper) else raw)
    return mapped_keys
