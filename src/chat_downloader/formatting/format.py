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
    """Formatter that disallows attribute and index access in templates.

    Prevents ``{0.attr}`` and ``{0[key]}`` patterns that could expose
    internal object state when templates come from user-supplied files.
    """

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
    KEY_COLLAPSE_LEADING_ZEROES = "collapse_leading_zeroes"

    # Special field names that require custom formatting
    FIELD_TIMESTAMP = "timestamp"
    FIELD_TIME_TEXT = "time_text"
    FIELD_AUTHOR_BADGES = "author.badges"

    # Standard keys
    KEY_MESSAGE_TYPE = "message_type"
    DEFAULT_FORMAT_NAME = "default"
    MATCH_ALL = "all"

    # Default values
    DEFAULT_TEMPLATE = ""
    # Keep plain-printable text output resilient to control chars commonly
    # present in moderation/bot messages (eg. ASCII 0x01).
    CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

    def __init__(self, path: str | None = None) -> None:
        """Create an ItemFormatter object.

        Raises FormatFileNotFound if a custom format file path is given but
        does not exist.
        """
        self.format_file = self._load_format_files(path)

    def _load_format_files(self, custom_path: str | None) -> dict[str, Any]:
        """Load default and optional custom format files, merged."""
        default_path = Path(__file__).parent / "custom_formats.json"

        with default_path.open(encoding="utf-8") as default_formats:
            format_file: dict[str, Any] = json.load(default_formats)

        if custom_path is not None:
            if not Path(custom_path).exists():
                msg = f'Format file not found: "{custom_path}"'
                raise FormatFileNotFound(msg)

            with Path(custom_path).open(encoding="utf-8") as custom_formats:
                format_file.update(json.load(custom_formats))

        return format_file

    def format(
        self,
        item: JSONDict,
        format_name: str = DEFAULT_FORMAT_NAME,
        format_object: dict[str, Any] | None = None,
    ) -> str:
        """Format a chat item according to a format specification.

        Raises FormatNotFound if format_name is not found.
        """
        format_object = self._resolve_format_object(
            format_name, format_object, item
        )

        if not format_object:
            msg = f'No valid format found for "{format_name}"'
            raise FormatNotFound(msg)

        format_object = self._apply_inheritance(format_object)

        return self._sanitize_output(self._apply_template(format_object, item))

    def _sanitize_output(self, text: str) -> str:
        """Remove unsupported control characters from formatted output lines."""
        return self.CONTROL_CHARS_RE.sub("", text)

    def _resolve_format_object(
        self,
        format_name: str,
        format_object: dict[str, Any] | list[Any] | None,
        item: JSONDict,
    ) -> dict[str, Any] | None:
        """Return the format object to use, by name or matched from list."""
        if format_object is None:
            format_object = self._get_format_by_name(format_name)

        if isinstance(format_object, list):
            return self._match_format_from_list(format_object, item)

        return format_object

    def _get_format_by_name(
        self, format_name: str
    ) -> dict[str, Any] | list[Any] | None:
        """Return the format entry for *format_name*, or the default."""
        format_object = self.format_file.get(format_name)

        if not format_object and format_name != self.DEFAULT_FORMAT_NAME:
            msg = f'Format not found: "{format_name}"'
            raise FormatNotFound(msg)

        if not format_object:
            format_object = self._get_default_format()

        return format_object

    def _get_default_format(self) -> dict[str, Any] | None:
        """Return the default format object."""
        return self.format_file.get(self.DEFAULT_FORMAT_NAME)

    def _match_format_from_list(
        self,
        format_list: list[Any],
        item: JSONDict,
    ) -> dict[str, Any] | None:
        """Return the first matching format from *format_list* for *item*."""
        message_type = item.get(self.KEY_MESSAGE_TYPE)

        for format_candidate in format_list:
            if self._does_format_match(format_candidate, message_type):
                return cast("dict[str, Any]", format_candidate)

        return self._get_default_format()

    def _does_format_match(
        self,
        format_object: dict[str, Any],
        message_type: object,
    ) -> bool:
        """Return True when *format_object* matches *message_type*."""
        matching = format_object.get(self.KEY_MATCHING)

        if matching == self.MATCH_ALL:
            return True

        if isinstance(matching, list):
            return message_type in matching

        return matching == message_type

    def _apply_inheritance(
        self, format_object: dict[str, Any]
    ) -> dict[str, Any]:
        """Return *format_object* merged onto its inherited parent, if any."""
        inherit = format_object.get(self.KEY_INHERIT)

        if not inherit:
            return format_object

        parent = self.format_file.get(inherit) or {}
        return nested_update(deepcopy(parent), format_object)

    def _apply_template(
        self, format_object: dict[str, Any], item: JSONDict
    ) -> str:
        """Substitute template placeholders with values from *item*."""
        template = format_object.get(self.KEY_TEMPLATE, self.DEFAULT_TEMPLATE)
        keys = format_object.get(self.KEY_KEYS, {})

        return re.sub(
            self._INDEX_REGEX,
            lambda match: self._replace_placeholder(match, item, keys),
            template,
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

            if value is None:
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

        template = self._extract_template(field_config)
        formatted_value = self._apply_field_formatting(
            field_path, value, field_config
        )

        return _SAFE_FORMATTER.format(template, formatted_value)

    def _extract_template(self, field_config: Any) -> str:
        """Return the template string from a field config entry."""
        if isinstance(field_config, str):
            return field_config

        if isinstance(field_config, dict):
            return cast(
                "str",
                field_config.get(self.KEY_TEMPLATE, self.DEFAULT_TEMPLATE),
            )

        return self.DEFAULT_TEMPLATE

    def _apply_field_formatting(
        self,
        field_path: str,
        value: Any,
        field_config: Any,
    ) -> Any:
        """Apply type-specific formatting and separator logic to *value*."""
        if not isinstance(field_config, dict):
            return value

        value = self._apply_format_by_type(field_path, value, field_config)
        return self._apply_separator(field_path, value, field_config)

    def _apply_format_by_type(
        self,
        field_path: str,
        value: Any,
        field_config: dict[str, Any],
    ) -> Any:
        """Apply timestamp or time-text formatting when configured."""
        format_string = field_config.get(self.KEY_FORMAT)

        if not format_string:
            return value

        if field_path == self.FIELD_TIMESTAMP:
            return microseconds_to_timestamp(value, format_string)

        if field_path == self.FIELD_TIME_TEXT:
            collapse_leading_zeroes: bool = bool(
                field_config.get(self.KEY_COLLAPSE_LEADING_ZEROES)
            )
            return seconds_to_time(
                time_to_seconds(value),
                format_string,
                collapse_leading_zeroes,
            )

        return value

    def _apply_separator(
        self,
        field_path: str,
        value: Any,
        field_config: dict[str, Any],
    ) -> Any:
        """Join list/tuple values with a separator when configured."""
        separator = field_config.get(self.KEY_SEPARATOR)

        if not separator:
            return value

        if field_path == self.FIELD_AUTHOR_BADGES:
            return separator.join(
                filter(None, (badge.get("title") for badge in value))
            )

        if isinstance(value, (tuple, list)):
            return separator.join(map(str, value))

        return value
