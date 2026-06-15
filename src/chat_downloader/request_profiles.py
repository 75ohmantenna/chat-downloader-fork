# SPDX-License-Identifier: MIT

"""Request-header profiles and fallback sequencing."""

from __future__ import annotations

import copy
from typing import Any, Final

REQUEST_PROFILES: Final[dict[str, dict[str, str]]] = {
    # Derived from Grayjay plugin defaults for improved compatibility.
    "youtube_web": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    },
    "youtube_android": {
        "User-Agent": (
            "com.google.android.youtube/21.03.36"
            "(Linux; U; Android 16; en_US; SM-S908E Build/TP1A.220624.014) gzip"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    },
    "youtube_ios": {
        "User-Agent": (
            "com.google.ios.youtube/21.02.3(iPhone16,2; U; "
            "CPU iOS 18_3_2 like Mac OS X; US)"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    },
    "twitch_web": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    },
}

_YOUTUBE_FALLBACK_ORDER: Final[list[str]] = [
    "youtube_web",
    "youtube_android",
    "youtube_ios",
]
_TWITCH_FALLBACK_ORDER: Final[list[str]] = ["twitch_web"]

REQUEST_PROFILE_INNERTUBE_CONTEXTS: Final[dict[str, dict[str, Any]]] = {
    "youtube_web": {
        "client": {
            "clientName": "WEB",
            "clientVersion": "2.20260206.01.00",
        },
    },
    "youtube_android": {
        "client": {
            "clientName": "ANDROID",
            "clientVersion": "21.03.36",
            "androidSdkVersion": 36,
            "userAgent": REQUEST_PROFILES["youtube_android"]["User-Agent"],
            "osName": "Android",
            "osVersion": "16",
        },
    },
    "youtube_ios": {
        "client": {
            "clientName": "IOS",
            "clientVersion": "21.02.3",
            "deviceMake": "Apple",
            "deviceModel": "iPhone16,2",
            "userAgent": REQUEST_PROFILES["youtube_ios"]["User-Agent"],
            "osName": "iPhone",
            "osVersion": "18.3.2.22D82",
        },
    },
}


def normalize_request_profile(profile_name: object) -> str | None:
    """Return a valid request-profile name or ``None``."""
    if not isinstance(profile_name, str) or profile_name not in REQUEST_PROFILES:
        return None
    return profile_name


def get_request_profile_headers(profile_name: object) -> dict[str, str]:
    """Return a copy of profile headers, or an empty dict if unknown."""
    profile = normalize_request_profile(profile_name)
    if profile is None:
        return {}
    return dict(REQUEST_PROFILES[profile])


def get_request_profile_innertube_context(
    profile_name: object,
) -> dict[str, Any]:
    """Return a deep copy of the Innertube context override for a profile."""
    profile = normalize_request_profile(profile_name)
    if profile is None:
        return {}
    return copy.deepcopy(REQUEST_PROFILE_INNERTUBE_CONTEXTS.get(profile, {}))


def build_request_profile_headers(
    profile_name: object,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return profile headers merged with explicit headers."""
    merged = get_request_profile_headers(profile_name)
    if headers:
        merged.update(headers)
    return merged


def get_next_request_profile(
    current_profile: object,
    *,
    site: str,
) -> str | None:
    """Return the next fallback profile for a site, if any."""
    if site == "youtube":
        sequence = _YOUTUBE_FALLBACK_ORDER
        default_next = "youtube_android"
    elif site == "twitch":
        sequence = _TWITCH_FALLBACK_ORDER
        default_next = "twitch_web"
    else:
        return None

    current = normalize_request_profile(current_profile)
    if current is None:
        return default_next
    if current not in sequence:
        return default_next

    index = sequence.index(current)
    next_index = index + 1
    if next_index >= len(sequence):
        return None
    return sequence[next_index]
