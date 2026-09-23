# SPDX-License-Identifier: MIT

"""Normalize opening replay order for checkpointable capture."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Generator, Iterable

    from chat_downloader.utils.json_types import JSONDict


def _finite_number(value: object) -> TypeGuard[int | float]:
    return (type(value) is int or type(value) is float) and math.isfinite(value)


def ordered_initial_preroll(
    source: Iterable[JSONDict], boundary_limit: int
) -> Generator[JSONDict, None, None]:
    """Place zero-offset replay notices after any opening negative-time chat."""
    initial_zero: list[JSONDict] = []
    iterator = iter(source)
    for item in iterator:
        offset = item.get("time_in_seconds")
        if _finite_number(offset) and offset == 0:
            if len(initial_zero) >= boundary_limit:
                msg = "Checkpoint timestamp has too many message IDs."
                raise ValueError(msg)
            initial_zero.append(item)
            continue
        if _finite_number(offset) and offset < 0:
            yield item
            continue
        yield from initial_zero
        yield item
        yield from iterator
        return
    yield from initial_zero
