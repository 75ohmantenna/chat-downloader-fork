# SPDX-License-Identifier: MIT

"""Shared model marker types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SiteDefault:
    """Marker object used to ask a site for its default value."""

    name: str
