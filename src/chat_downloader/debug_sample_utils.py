# SPDX-License-Identifier: MIT

"""Shared naming helpers for captured debug samples and promoted fixtures."""

import re
from dataclasses import dataclass
from pathlib import Path

_DIGEST_SUFFIX_RE = re.compile(r"-[0-9a-f]{12}$")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_GROUP_RULES: tuple[tuple[str, str], ...] = (
    ("unknown-continuation", "continuations"),
    ("unknown-action", "actions"),
    ("empty-action-parse", "actions"),
    ("missing-keys", "messages"),
    ("unknown-message-type", "messages"),
    # Twitch-specific groups
    ("unknown-irc-action", "messages"),
    ("unknown-irc-tag", "messages"),
    ("unknown-gql-shape", "graphql"),
)


@dataclass(frozen=True)
class DebugSampleHint:
    """Stable naming hints for a captured debug sample."""

    site: str
    group: str
    fixture_name: str


def slugify_debug_label(label: str) -> str:
    """Return a filesystem-friendly identifier for a debug sample label."""
    slug = _NON_ALNUM_RE.sub("-", label.lower()).strip("-")
    return slug or "debug-sample"


def infer_site_from_sample_name(sample_path: Path | str) -> str:
    """Infer the source site from a captured sample filename."""
    stem = Path(sample_path).stem
    prefix, _, _rest = stem.partition("-")
    return prefix or "misc"


def infer_group_from_sample_name(sample_path: Path | str) -> str:
    """Infer the fixture group from a captured sample filename."""
    stem = Path(sample_path).stem
    for marker, group in _GROUP_RULES:
        if marker in stem:
            return group
    return "misc"


def normalize_fixture_name(
    sample_path: Path | str,
    fixture_name: str | None = None,
) -> str:
    """Return a fixture filename without the capture digest suffix."""
    base_name = fixture_name or Path(sample_path).stem
    return _DIGEST_SUFFIX_RE.sub("", base_name)


def describe_debug_sample(sample_path: Path | str) -> DebugSampleHint:
    """Return the stable fixture hint for a debug sample file or stem."""
    return DebugSampleHint(
        site=infer_site_from_sample_name(sample_path),
        group=infer_group_from_sample_name(sample_path),
        fixture_name=normalize_fixture_name(sample_path),
    )


__all__ = [
    "DebugSampleHint",
    "describe_debug_sample",
    "infer_group_from_sample_name",
    "infer_site_from_sample_name",
    "normalize_fixture_name",
    "slugify_debug_label",
]
