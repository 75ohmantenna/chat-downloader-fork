# SPDX-License-Identifier: MIT

"""ItemFormatter: template-driven rendering of chat message dicts."""

from __future__ import annotations

import json
import re
import string
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from chat_downloader.errors import FormatFileNotFound, FormatNotFound
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_utils import nested_update
from chat_downloader.utils.time_utils import (
    microseconds_to_timestamp,
    seconds_to_time,
    time_to_seconds,
)

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict


class _SafeFormatter(string.Formatter):
    """Block {0.attr}/{0[key]} access that exposes state in user-supplied templates."""

    def get_field(self, field_name: str, args: Any, kwargs: Any) -> Any:
        if "." in field_name or "[" in field_name:
            msg = (
                "Attribute/index access not allowed in format template "
                f"field: {field_name!r}"
            )
            raise ValueError(msg)
        return super().get_field(field_name, args, kwargs)


_SAFE_FORMATTER = _SafeFormatter()


class ItemFormatter:
    """Class used to control the formatting of chat items."""

    # Regex pattern for finding placeholder fields in templates
    _INDEX_REGEX = r"(?<!\\){(.+?)(?<!\\)}"

    # Format object keys
    KEY_TEMPLATE = "template"
    KEY_KEYS = "keys"
    KEY_MATCHING = "matching"
    KEY_INHERIT = "inherit"
    KEY_FORMAT = "format"
    KEY_SEPARATOR = "separator"
    KEY_SINGULAR_TEMPLATE = "singular_template"
    KEY_COLLAPSE_LEADING_ZEROES = "collapse_leading_zeroes"
    KEY_OMIT_IF_FALSE = "omit_if_false"

    # Special field names that require custom formatting
    FIELD_TIMESTAMP = "timestamp"
    FIELD_RECEIVED_TIMESTAMP = "received_timestamp"
    FIELD_TIME_TEXT = "time_text"
    FIELD_AUTHOR_BADGES = "author.badges"

    # Standard keys
    KEY_MESSAGE_TYPE = "message_type"
    DEFAULT_FORMAT_NAME = "default"
    MATCH_ALL = "all"

    # Default values
    DEFAULT_TEMPLATE = ""
    # Keep plain-printable text output resilient to control chars commonly
    # present in moderation/bot messages (eg. ASCII 0x01 and C1 CSI/OSC).
    CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]")
    LINE_SEPARATOR_TRANSLATION = str.maketrans(
        {
            "\r": r"\r",
            "\n": r"\n",
            "\x85": r"\u0085",
            "\u2028": r"\u2028",
            "\u2029": r"\u2029",
        }
    )

    def __init__(self, path: str | None = None) -> None:
        """Create an ItemFormatter.

        Raises:
            FormatFileNotFound: The custom format file does not exist.
        """
        default_path = Path(__file__).parent / "custom_formats.json"

        with default_path.open(encoding="utf-8") as default_formats:
            self.format_file: dict[str, Any] = json.load(default_formats)

        if path is not None:
            if not Path(path).exists():
                msg = f'Format file not found: "{path}"'
                raise FormatFileNotFound(msg)

            with Path(path).open(encoding="utf-8") as custom_formats:
                self.format_file.update(json.load(custom_formats))

    def format(
        self,
        item: JSONDict,
        format_name: str = DEFAULT_FORMAT_NAME,
        format_object: dict[str, Any] | None = None,
    ) -> str:
        """Format a chat item according to a format specification.

        Raises:
            FormatNotFound: format_name is not found.
        """
        selected = format_object
        if format_object is None:
            selected = self.format_file.get(format_name)
            if not selected and format_name != self.DEFAULT_FORMAT_NAME:
                msg = f'Format not found: "{format_name}"'
                raise FormatNotFound(msg)

        candidates: Any = selected
        if isinstance(candidates, list):
            message_type = item.get(self.KEY_MESSAGE_TYPE)
            for candidate in candidates:
                matching = candidate.get(self.KEY_MATCHING)
                if matching == self.MATCH_ALL or (
                    message_type in matching
                    if isinstance(matching, list)
                    else matching == message_type
                ):
                    selected = cast("dict[str, Any]", candidate)
                    break
            else:
                selected = self.format_file.get(self.DEFAULT_FORMAT_NAME)

        if not selected:
            msg = f'No valid format found for "{format_name}"'
            raise FormatNotFound(msg)

        inherit = selected.get(self.KEY_INHERIT)

        if inherit:
            parent = self.format_file.get(inherit) or {}
            selected = nested_update(deepcopy(parent), selected)

        template = selected.get(self.KEY_TEMPLATE, self.DEFAULT_TEMPLATE)
        keys = selected.get(self.KEY_KEYS, {})

        text = re.sub(
            self._INDEX_REGEX,
            lambda match: self._replace_placeholder(match, item, keys),
            template,
        )
        return self.CONTROL_CHARS_RE.sub(
            "", text.translate(self.LINE_SEPARATOR_TRANSLATION)
        )

    def _replace_placeholder(
        self,
        match: re.Match[str],
        item: JSONDict,
        format_keys: dict[str, Any],
    ) -> str:
        """Replace a single template placeholder with its formatted value."""
        fallback_keys = match.group(1).split("|")

        for field_path in fallback_keys:
            value = multi_get(item, *field_path.split("."))

            if value is None or value == "":
                continue

            return self._format_field_value(field_path, value, format_keys)

        return ""

    def _format_field_value(
        self,
        field_path: str,
        value: Any,
        format_keys: dict[str, Any],
    ) -> str:
        """Format *value* at *field_path* according to its format spec."""
        field_config = format_keys.get(field_path)

        if field_config is None:
            return str(value)
        if not isinstance(field_config, dict):
            template = field_config if isinstance(field_config, str) else ""
            return _SAFE_FORMATTER.format(template, value)

        omit_if_false = field_config.get(self.KEY_OMIT_IF_FALSE) is True
        if omit_if_false and not value:
            return ""

        template = field_config.get(self.KEY_TEMPLATE, self.DEFAULT_TEMPLATE)
        singular = field_config.get(self.KEY_SINGULAR_TEMPLATE)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value == 1
            and isinstance(singular, str)
        ):
            template = singular

        format_string = field_config.get(self.KEY_FORMAT)
        if format_string:
            if field_path in {self.FIELD_TIMESTAMP, self.FIELD_RECEIVED_TIMESTAMP}:
                value = microseconds_to_timestamp(value, format_string)
            elif field_path == self.FIELD_TIME_TEXT:
                value = seconds_to_time(
                    time_to_seconds(value),
                    format=format_string,
                    remove_leading_zeroes=bool(
                        field_config.get(self.KEY_COLLAPSE_LEADING_ZEROES)
                    ),
                )

        separator = field_config.get(self.KEY_SEPARATOR)
        if separator and field_path == self.FIELD_AUTHOR_BADGES:
            value = separator.join(
                filter(None, (badge.get("title") for badge in value))
            )
        elif separator and isinstance(value, (tuple, list)):
            value = separator.join(map(str, value))

        return (
            ""
            if omit_if_false and not value
            else _SAFE_FORMATTER.format(template, value)
        )
