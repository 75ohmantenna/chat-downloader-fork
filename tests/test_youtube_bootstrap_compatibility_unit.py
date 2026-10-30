# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock

import pytest

from chat_downloader.errors import ParsingError
from chat_downloader.sites.youtube.client_auth import _generate_sapisidhash_header
from chat_downloader.sites.youtube.client_context import (
    _generate_headers,
    _get_innertube_context,
)
from chat_downloader.sites.youtube.client_requests_initial import _get_initial_info
from chat_downloader.sites.youtube.constants_patterns import (
    _YT_CFG_RE,
    _YT_INITIAL_DATA_RE,
    _YT_INITIAL_PLAYER_RESPONSE_RE,
)
from chat_downloader.sites.youtube.helpers import (
    _browse_api_headers,
    _update_browse_visitor_data,
)
from tests.youtube_third_helpers import http_response

HOME = "https://www.youtube.com"
FIXTURE = Path(__file__).parent / "fixtures/youtube/initial_data_script.html"


def _read_page(html):
    return _get_initial_info(
        HOME,
        Mock(return_value=http_response(text=html)),
        None,
        _YT_INITIAL_DATA_RE,
        _YT_CFG_RE,
        _YT_INITIAL_PLAYER_RESPONSE_RE,
    )


def test_dedicated_script_bootstraps_initial_config_and_player():
    initial, config, player = _read_page(FIXTURE.read_text())
    assert initial == {
        "contents": {"label": "Chat & messages", "nested": {"value": "};"}}
    }
    assert config == {"INNERTUBE_API_KEY": "fixture-key"}
    assert player == {"videoDetails": {"videoId": "abcdefghijk"}}


@pytest.mark.parametrize(
    "script",
    [
        '<script data-id="yt-initial-data">{"decoy":true}</script>',
        '<script id="yt-initial-data">broken</script>',
        '<script id="yt-initial-data">[1]</script>',
        '<script id="yt-initial-data">{}</script>',
        '<!-- <script id="yt-initial-data">{"decoy":true}</script> -->',
    ],
)
def test_invalid_or_lookalike_script_falls_back_to_assignment(script):
    initial, _, _ = _read_page(
        script + '<script>ytInitialData = {"valid":true};</script>'
    )
    assert initial == {"valid": True}


@pytest.mark.parametrize("value", ["[1]", '"text"', "null", "{}"])
def test_initial_data_requires_nonempty_object(value):
    with pytest.raises(ParsingError, match="Unable to parse initial video data"):
        _read_page(f'<script id="yt-initial-data">{value}</script>')


def test_dedicated_script_precedence_and_repeated_scripts():
    html = '<script>ytInitialData = {"old":true};</script>'
    html += '<SCRIPT ID="yt-initial-data">{"first":true}</SCRIPT>'
    html += '<script id="yt-initial-data">{"second":true}</script>'
    assert _read_page(html)[0] == {"first": True}


@pytest.mark.parametrize("explicit", ["explicit-user", "", None, 42])
def test_explicit_session_identity_precedes_datasync(monkeypatch, explicit):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.client_auth.time.time", lambda: 123
    )
    config = {
        "USER_SESSION_ID": explicit,
        "DELEGATED_SESSION_ID": "explicit-page",
        "DATASYNC_ID": "old-page||old-user",
    }
    session = Mock()
    session.get_cookie_value.side_effect = lambda name: (
        "sid" if name == "SAPISID" else None
    )
    headers = _generate_headers(config, session, HOME, _generate_sapisidhash_header)
    user = explicit if isinstance(explicit, str) and explicit else "old-user"
    digest = hashlib.sha1(f"{user} 123 sid {HOME}".encode()).hexdigest()
    assert headers["authorization"] == f"SAPISIDHASH 123_{digest}_u"
    assert headers["x-goog-pageid"] == "explicit-page"
    assert headers["x-goog-authuser"] == "0"


def test_explicit_user_without_datasync_is_bound(monkeypatch):
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.client_auth.time.time", lambda: 123
    )
    session = Mock()
    session.get_cookie_value.side_effect = lambda name: (
        "sid" if name == "SAPISID" else None
    )
    header = _generate_sapisidhash_header(session, HOME, {"USER_SESSION_ID": "user"})
    digest = hashlib.sha1(f"user 123 sid {HOME}".encode()).hexdigest()
    assert header == f"SAPISIDHASH 123_{digest}_u"


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"VISITOR_DATA": "top"}, "top"),
        ({"responseContext": {"visitorData": "response"}}, "response"),
        (
            {"VISITOR_DATA": 42, "responseContext": {"visitorData": "response"}},
            "response",
        ),
        (
            {
                "VISITOR_DATA": "top",
                "INNERTUBE_CONTEXT": {"client": {"visitorData": "active"}},
            },
            "active",
        ),
        (
            {
                "VISITOR_DATA": "top",
                "INNERTUBE_CONTEXT": {"client": {"visitorData": []}},
            },
            "top",
        ),
        ({"VISITOR_DATA": []}, None),
    ],
)
def test_visitor_fallback_keeps_headers_and_body_aligned(config, expected):
    original = repr(config)
    headers = _generate_headers(config, object(), HOME, lambda *_: None)
    body = _get_innertube_context(config)
    assert headers.get("x-goog-visitor-id") == expected
    assert body["client"].get("visitorData") == expected
    assert repr(config) == original


def test_browse_refresh_overrides_old_config_token():
    config = {"VISITOR_DATA": "old"}
    params = {"context": _get_innertube_context(config)}
    response = {"responseContext": {"visitorData": "refreshed"}}
    downloader = Mock()
    downloader.get_cookie_value.return_value = None
    _update_browse_visitor_data(params, response)
    headers = _browse_api_headers(downloader, config, params)
    assert headers["x-goog-visitor-id"] == "refreshed"
    assert params["context"]["client"]["visitorData"] == "refreshed"
    assert config == {"VISITOR_DATA": "old"}


@pytest.mark.parametrize("value", ["", None, 42])
def test_invalid_explicit_delegated_id_uses_datasync(value):
    config = {"DELEGATED_SESSION_ID": value, "DATASYNC_ID": "page||user"}
    headers = _generate_headers(config, object(), HOME, lambda *_: None)
    assert headers["x-goog-pageid"] == "page"


@pytest.mark.parametrize("value", ["[1]", '"text"', "true"])
def test_legacy_initial_data_also_requires_object(value):
    with pytest.raises(ParsingError, match="Unable to parse initial video data"):
        _read_page(f"<script>ytInitialData = {value};</script>")
