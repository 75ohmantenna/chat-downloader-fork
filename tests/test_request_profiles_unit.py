# SPDX-License-Identifier: MIT

from chat_downloader.request_profiles import (
    REQUEST_PROFILES,
    build_request_profile_headers,
    get_next_request_profile,
    get_request_profile_headers,
    normalize_request_profile,
)


def test_get_request_profile_headers_returns_copy() -> None:
    headers = get_request_profile_headers("youtube_android")
    assert headers == REQUEST_PROFILES["youtube_android"]
    headers["User-Agent"] = "mutated"
    assert REQUEST_PROFILES["youtube_android"]["User-Agent"] != "mutated"


def test_get_request_profile_headers_returns_empty_for_unknown() -> None:
    assert get_request_profile_headers("missing") == {}


def test_build_request_profile_headers_merges_and_handles_empty_inputs() -> (
    None
):
    merged = build_request_profile_headers(
        "youtube_web",
        {"X-Test": "1", "Accept-Language": "override"},
    )
    assert merged["X-Test"] == "1"
    assert merged["Accept-Language"] == "override"

    assert build_request_profile_headers("missing", None) == {}


def test_normalize_request_profile_rejects_unknown_value() -> None:
    assert normalize_request_profile("unknown") is None
    assert normalize_request_profile(None) is None


def test_get_next_request_profile_for_youtube_progresses_sequence() -> None:
    assert get_next_request_profile(None, site="youtube") == "youtube_android"
    assert (
        get_next_request_profile("youtube_web", site="youtube")
        == "youtube_android"
    )
    assert (
        get_next_request_profile("youtube_android", site="youtube")
        == "youtube_ios"
    )
    assert get_next_request_profile("youtube_ios", site="youtube") is None


def test_get_next_request_profile_for_twitch_is_terminal() -> None:
    assert get_next_request_profile(None, site="twitch") == "twitch_web"
    assert get_next_request_profile("twitch_web", site="twitch") is None


def test_get_next_request_profile_handles_unknown_site_and_wrong_sequence() -> (
    None
):
    assert get_next_request_profile("youtube_web", site="unknown") is None
    assert (
        get_next_request_profile("twitch_web", site="youtube")
        == "youtube_android"
    )
