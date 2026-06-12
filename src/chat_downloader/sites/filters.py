# SPDX-License-Identifier: MIT

"""Shared message and time-range filtering helpers."""

from __future__ import annotations

from typing import Any, Literal

from chat_downloader.errors import InvalidParameter


class MessageFilter:
    """Determine which messages to include based on types and groups."""

    def __init__(
        self,
        message_groups_dict: dict[str, list[str]],
        groups_to_add: list[str] | None = None,
        types_to_add: list[str] | None = None,
    ) -> None:
        """Build the allowed message-type set from groups and explicit types."""
        self._valid_types: set[str] | None = None

        if (groups_to_add and "all" in groups_to_add) or (
            types_to_add and "all" in types_to_add
        ):
            return

        types: set[str] = set()
        has_filter = False

        if types_to_add:
            has_filter = True
            types.update(types_to_add)

        if groups_to_add:
            invalid_groups = set(groups_to_add) - message_groups_dict.keys()
            if "all" not in groups_to_add and invalid_groups:
                msg = f"Invalid groups specified: {invalid_groups}"
                raise InvalidParameter(msg)
            has_filter = True
            for group_name in groups_to_add:
                types.update(message_groups_dict.get(group_name, []))

        if has_filter:
            self._valid_types = types

    def should_add(self, item: dict[str, Any]) -> bool:
        """Return True if the item passes the filter."""
        if self._valid_types is None:
            return True
        return item.get("message_type") in self._valid_types


class TimeRangeFilter:
    """Determine whether messages fall within a given time window."""

    def __init__(
        self,
        start_time: float | None = None,
        end_time: float | None = None,
        offset: float | None = None,
        skip_mode: Literal["none", "always", "first_page"] = "none",
    ) -> None:
        """Initialize a time window and page-skipping policy."""
        self.start_time = start_time
        self.end_time = end_time
        self.offset = offset or 0
        self._skip_mode = skip_mode
        self._is_first_page = True

    def check(self, data: dict[str, Any]) -> Literal["yield", "skip", "stop"]:
        """Check if the message's time is within range."""
        time_in_seconds = data.get("time_in_seconds", 0) + self.offset

        before_start = self.start_time is not None and time_in_seconds < self.start_time
        after_end = self.end_time is not None and time_in_seconds > self.end_time

        if after_end:
            return "stop"

        if before_start:
            if self._skip_mode == "always":
                return "skip"
            if self._skip_mode == "first_page" and self._is_first_page:
                return "skip"
            return "stop"

        return "yield"

    def end_page(self) -> None:
        """Signal that the current page of results is complete."""
        self._is_first_page = False
