# SPDX-License-Identifier: MIT
from __future__ import annotations

import pytest

from chat_downloader.request_profiles import (
    REQUEST_PROFILE_INNERTUBE_CONTEXTS,
    REQUEST_PROFILES,
    build_request_profile_headers,
    get_next_request_profile,
    get_request_profile_headers,
    get_request_profile_innertube_client_id,
    normalize_request_profile,
)


def test_profile_headers_are_independent_and_overridable():
    headers = get_request_profile_headers("youtube_android")
    assert headers == REQUEST_PROFILES["youtube_android"]
    headers["User-Agent"] = "mutated"
    assert REQUEST_PROFILES["youtube_android"]["User-Agent"] != "mutated"
    assert get_request_profile_headers("missing") == {}
    merged = build_request_profile_headers(
        "youtube_web", {"X-Test": "1", "Accept-Language": "override"}
    )
    assert merged["X-Test"] == "1"
    assert merged["Accept-Language"] == "override"
    assert build_request_profile_headers("missing", None) == {}


@pytest.mark.parametrize("profile", ["unknown", None])
def test_unknown_profile(profile):
    assert normalize_request_profile(profile) is None


@pytest.mark.parametrize(
    ("site", "profile", "expected"),
    [
        ("youtube", None, "youtube_android"),
        ("youtube", "youtube_web", "youtube_android"),
        ("youtube", "youtube_android", "youtube_ios"),
        ("youtube", "youtube_ios", None),
        ("twitch", None, "twitch_web"),
        ("twitch", "twitch_web", None),
        ("unknown", "youtube_web", None),
        ("youtube", "twitch_web", "youtube_android"),
    ],
)
def test_profile_progression(site, profile, expected):
    assert get_next_request_profile(profile, site=site) == expected


def test_youtube_profiles_match_current_innertube_clients():
    for profile, client_id, version in [
        ("youtube_web", 1, "2.20260708.00.00"),
        ("youtube_android", 3, "21.26.364"),
        ("youtube_ios", 5, "21.26.4"),
    ]:
        assert get_request_profile_innertube_client_id(profile) == client_id
        assert (
            REQUEST_PROFILE_INNERTUBE_CONTEXTS[profile]["client"]["clientVersion"]
            == version
        )
    assert get_request_profile_innertube_client_id("missing") is None
