# SPDX-License-Identifier: MIT

"""ItemFormatter: template-driven rendering of chat message dicts."""

from __future__ import annotations

import json
import re
import string
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any

from chat_downloader.errors import FormatFileNotFound, FormatNotFound
from chat_downloader.formatting.summaries import format_goal, format_leaderboard
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_int, get_list, get_str
from chat_downloader.utils.json_utils import nested_update
from chat_downloader.utils.time_utils import (
    microseconds_to_timestamp,
    parse_iso8601,
    seconds_to_time,
    time_to_seconds,
)

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict

BUILTIN_FORMAT_FILE = Path(__file__).parent / "custom_formats.json"


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


def _format_poll_option(
    option: JSONDict, position: int, votes: int, total: int | None
) -> str:
    """Render an option with explicit fallbacks for missing data."""
    option_id = get_int(option, "id", -1)
    label = get_str(option, "label") or (
        f"Option {option_id if option_id >= 0 else position + 1}"
    )
    if votes < 0:
        return f"{label}: votes unknown"
    unit = "vote" if votes == 1 else "votes"
    text = f"{label}: {votes} {unit}"
    if total:
        text += f" ({votes / total:.1%})"
    return text


def _format_poll_metadata(value: object) -> str:
    """Render poll state, deriving totals only from complete valid counts."""
    if not isinstance(value, dict):
        return ""
    options = get_list(value, "options")
    counts = [
        get_int(option, "votes", -1) if isinstance(option, dict) else -1
        for option in options
    ]
    total = sum(counts) if counts and min(counts) >= 0 else None
    fragments = [
        _format_poll_option(option, position, counts[position], total)
        for position, option in enumerate(options)
        if isinstance(option, dict)
    ]
    if total is not None:
        fragments.append(f"Total: {total}")
    remaining = get_int(value, "remaining", -1)
    if remaining >= 0:
        fragments.append(f"Remaining: {remaining}s")
    return "".join(f" | {fragment}" for fragment in fragments)


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
        with BUILTIN_FORMAT_FILE.open(encoding="utf-8") as default_formats:
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

        selected = self._resolve_format(selected, item.get(self.KEY_MESSAGE_TYPE))
        if not selected:
            msg = f'No valid format found for "{format_name}"'
            raise FormatNotFound(msg)

        template = selected.get(self.KEY_TEMPLATE, self.DEFAULT_TEMPLATE)
        keys = selected.get(self.KEY_KEYS, {})

        text = re.sub(
            self._INDEX_REGEX,
            lambda match: self._replace_placeholder(match, item, keys),
            template,
        )
        return (
            self.CONTROL_CHARS_RE.sub(
                "", text.translate(self.LINE_SEPARATOR_TRANSLATION)
            )
            .encode("utf-8", errors="backslashreplace")
            .decode("utf-8")
        )

    def _resolve_format(self, candidates: Any, message_type: object) -> dict[str, Any]:
        """Select matching parents and merge inheritance without mutating presets."""
        layers = []
        seen: set[str] = set()
        while candidates:
            if isinstance(candidates, list):
                candidates = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.get(self.KEY_MATCHING) == self.MATCH_ALL
                        or (
                            message_type in candidate[self.KEY_MATCHING]
                            if isinstance(candidate.get(self.KEY_MATCHING), list)
                            else candidate.get(self.KEY_MATCHING) == message_type
                        )
                    ),
                    self.format_file.get(self.DEFAULT_FORMAT_NAME),
                )
            if not candidates:
                break
            layers.append(candidates)
            inherit = candidates.get(self.KEY_INHERIT)
            if not inherit:
                break
            if inherit in seen:
                msg = "Cyclic format inheritance"
                raise ValueError(msg)
            seen.add(inherit)
            candidates = self.format_file.get(inherit)
        resolved: dict[str, Any] = {}
        for layer in reversed(layers):
            nested_update(resolved, deepcopy(layer))
        return resolved

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
            value = self._convert_field_value(field_path, value, field_config)

        separator = (
            None if format_string == "json" else field_config.get(self.KEY_SEPARATOR)
        )
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

    @classmethod
    def _convert_field_value(
        cls, field_path: str, value: Any, field_config: dict[str, Any]
    ) -> Any:
        """Apply a field's JSON, poll, or time representation before templating."""
        format_string = field_config[cls.KEY_FORMAT]
        if format_string == "poll":
            return _format_poll_metadata(value)
        if format_string == "leaderboard":
            return format_leaderboard(value, get_str(field_config, "unit"))
        if format_string == "goal":
            return format_goal(value)
        if format_string == "iso8601":
            try:
                return microseconds_to_timestamp(parse_iso8601(value))
            except (ValueError, TypeError, OverflowError, OSError):
                return ""
        if format_string == "json":
            return (
                json.dumps(value, ensure_ascii=False, sort_keys=True)
                .encode("utf-8", errors="backslashreplace")
                .decode("utf-8")
            )
        if field_path in {cls.FIELD_TIMESTAMP, cls.FIELD_RECEIVED_TIMESTAMP}:
            return microseconds_to_timestamp(value, format_string)
        if field_path == cls.FIELD_TIME_TEXT:
            return seconds_to_time(
                time_to_seconds(value),
                format=format_string,
                remove_leading_zeroes=bool(
                    field_config.get(cls.KEY_COLLAPSE_LEADING_ZEROES)
                ),
            )
        return value
