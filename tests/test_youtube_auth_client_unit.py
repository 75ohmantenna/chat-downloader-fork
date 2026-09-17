# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
from json.decoder import JSONDecodeError
from unittest.mock import Mock

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import chat_downloader.sites.youtube.client_auth as auth
import chat_downloader.sites.youtube.client_context as context
from chat_downloader.errors import RetriesExceeded, VideoNotFound
from chat_downloader.sites.youtube.client_requests_continuation import (
    _get_continuation_info,
)
from chat_downloader.sites.youtube.client_requests_initial import _get_initial_info
from tests.youtube_third_helpers import http_response, patch, response, returns, wrap

HOME = "https://www.youtube.com"
CONTINUATION_URL = f"{HOME}/youtubei/v1/live_chat/get_live_chat"
INITIAL_PATTERNS = (r"ytInitialData", r"ytcfg", r"ytInitialPlayerResponse")


class _FakeSession:
    def __init__(self, cookies=None):
        self.cookies = dict(cookies or {})
        self.set_calls = []

    def get_cookie_value(self, name, default=None):
        return self.cookies.get(name, default)

    def set_cookie_value(self, domain, name, value, **kwargs):
        self.set_calls.append((domain, name, value, kwargs))
        self.cookies[name] = value


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, (None, None)),
        ("", (None, None)),
        ("user-session", (None, "user-session")),
        ("delegated||user", ("delegated", "user")),
    ],
)
def test_data_sync_id(value, expected):
    assert auth._parse_data_sync_id(value) == expected


def test_session_binding_authorization():
    parts = auth._session_id_parts({"DATASYNC_ID": "delegated||user-session"})
    assert parts == {"u": "user-session"}
    token = auth._make_sid_authorization("SAPISIDHASH", "sid-value", HOME, 123, parts)
    digest = hashlib.sha1(
        b"user-session 123 sid-value https://www.youtube.com"
    ).hexdigest()
    assert token == f"SAPISIDHASH 123_{digest}_u"


@pytest.mark.parametrize("primary", [False, True])
def test_missing_sid_does_not_create_cookie(primary):
    session = _FakeSession()
    result = (
        auth._ensure_primary_sapisid(session, (None, None, None), 1234)
        if primary
        else auth._generate_sapisidhash_header(session, HOME)
    )
    assert result is None
    assert session.set_calls == []


@pytest.mark.parametrize(
    ("cookies", "ytcfg", "bound"),
    [
        ({"SAPISID": "direct-sapisid"}, None, False),
        ({"__Secure-3PAPISID": "threep"}, None, False),
        ({"__Secure-3PAPISID": "threep"}, {"OTHER_KEY": "value"}, False),
        (
            {"__Secure-1PAPISID": "onep", "__Secure-3PAPISID": "threep"},
            {"DATASYNC_ID": "delegated||user-session"},
            True,
        ),
    ],
    ids=["primary-cookie", "no-config", "no-datasync-id", "session-binding"],
)
def test_sid_header_promotion_and_binding(monkeypatch, cookies, ytcfg, bound):
    returns(monkeypatch, "client_auth.time.time", 1234)
    session = _FakeSession(cookies)
    header = auth._generate_sapisidhash_header(session, HOME, ytcfg)
    assert header is not None
    if "SAPISID" in cookies:
        assert session.set_calls == []
        assert "SAPISIDHASH" in header
    else:
        assert session.set_calls == [
            (".youtube.com", "SAPISID", "threep", {"secure": True, "expire_time": 4834})
        ]
    if bound:
        assert header == " ".join(
            auth._make_sid_authorization(scheme, sid, HOME, 1234, {"u": "user-session"})
            for scheme, sid in [
                ("SAPISIDHASH", "threep"),
                ("SAPISID1PHASH", "onep"),
                ("SAPISID3PHASH", "threep"),
            ]
        )


@pytest.mark.parametrize(
    ("cookies", "expected"),
    [
        ({"__Secure-3PSID": "present"}, []),
        ({"SOCS": "XYZ"}, []),
        ({}, [(".youtube.com", "SOCS", "CAI", {"secure": True})]),
        ({"SOCS": "CAAabc"}, [(".youtube.com", "SOCS", "CAI", {"secure": True})]),
    ],
)
def test_initialize_consent(cookies, expected):
    session = _FakeSession(cookies)
    auth._initialize_consent(session)
    assert session.set_calls == expected


def test_get_sid_cookies_returns_all_variants():
    session = _FakeSession(
        {
            "SAPISID": "sap",
            "__Secure-1PAPISID": "onep",
            "__Secure-3PAPISID": "threep",
        }
    )
    assert auth._get_sid_cookies(session) == ("sap", "onep", "threep")


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"DATASYNC_ID": "delegated||user"}, "delegated"),
        ({"DATASYNC_ID": "user-only"}, None),
        ({"DELEGATED_SESSION_ID": "fallback"}, "fallback"),
    ],
)
def test_account_syncid(config, expected):
    assert context._extract_account_syncid(config) == expected


@pytest.mark.parametrize(
    "mode",
    ["authenticated", "minimal", "empty", "zero"],
)
def test_generate_headers(mode):
    config = (
        {}
        if mode in {"empty", "zero"}
        else {
            "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
            "INNERTUBE_CLIENT_VERSION": "2.0",
        }
    )
    if mode == "zero":
        config["SESSION_INDEX"] = 0
    if mode == "authenticated":
        config.update(
            ID_TOKEN="obsolete-id-token",  # noqa: S106 - literal fake test token
            DATASYNC_ID="delegated||user",
            SESSION_INDEX=5,
            LOGGED_IN=True,
            INNERTUBE_CONTEXT=wrap(
                "client",
                {
                    "visitorData": "visitor-1",
                    "userAgent": "TestAgent/1.0",
                },
            ),
        )
    headers = context._generate_headers(
        config,
        session=object(),
        yt_home=HOME,
        sapisidhash_generator=Mock(
            return_value="AUTH" if mode == "authenticated" else None
        ),
    )
    if mode == "authenticated":
        assert "x-youtube-identity-token" not in headers
        assert (
            headers.items()
            >= {
                "x-goog-pageid": "delegated",
                "x-goog-authuser": "5",
                "x-goog-visitor-id": "visitor-1",
                "user-agent": "TestAgent/1.0",
                "x-youtube-bootstrap-logged-in": "true",
                "authorization": "AUTH",
                "x-origin": HOME,
            }.items()
        )
    elif mode == "minimal":
        assert not {"x-goog-authuser", "x-origin", "authorization", "x-goog-pageid"} & (
            headers.keys()
        )
    elif mode == "empty":
        assert headers == {"origin": HOME}
    else:
        assert headers["x-goog-authuser"] == "0"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("invalid", {}),
        (
            [],
            wrap(
                "client",
                {
                    "hl": "en",
                    "timeZone": "UTC",
                    "utcOffsetMinutes": 0,
                },
            ),
        ),
        (
            {"client": None},
            wrap(
                "client",
                {
                    "hl": "en",
                    "timeZone": "UTC",
                    "utcOffsetMinutes": 0,
                },
            ),
        ),
    ],
)
def test_innertube_context(value, expected):
    assert context._get_innertube_context({"INNERTUBE_CONTEXT": value}) == expected


def test_request_profile_keeps_body_and_headers_aligned():
    original = {
        "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
        "INNERTUBE_CLIENT_VERSION": "old-web-version",
        **wrap(
            "INNERTUBE_CONTEXT.client",
            {
                "clientName": "WEB",
                "clientVersion": "old-web-version",
                "visitorData": "visitor",
            },
        ),
    }
    updated = context.apply_request_profile_to_ytcfg(original, "youtube_android")
    assert updated["INNERTUBE_CONTEXT_CLIENT_NAME"] == 3
    assert updated["INNERTUBE_CLIENT_VERSION"] == "21.26.364"
    assert updated["INNERTUBE_CONTEXT"]["client"]["clientName"] == "ANDROID"
    assert updated["INNERTUBE_CONTEXT"]["client"]["visitorData"] == "visitor"
    assert original["INNERTUBE_CONTEXT"]["client"]["clientName"] == "WEB"


def test_request_profile_unknown_leaves_ytcfg_values_copied():
    original = {"INNERTUBE_CONTEXT_CLIENT_NAME": 1}
    updated = context.apply_request_profile_to_ytcfg(original, "missing")
    assert updated == original
    assert updated is not original


@pytest.mark.parametrize(
    "failure",
    ["http", "json-retry", "json", "network"],
)
def test_continuation_errors_and_recovery(failure):
    reply = http_response(404 if failure == "http" else 200, text="not-json")
    reply.json.side_effect = (
        [ValueError("bad json"), {}]
        if failure == "http"
        else JSONDecodeError("bad json", "doc", 0)
    )
    post = Mock(return_value=reply)
    expected = response()
    if failure == "json-retry":
        post.side_effect = [reply, http_response(payload=expected)]
    if failure == "network":
        post.side_effect = RequestsConnectionError("boom")
    params = {"max_attempts": 2 if failure == "json-retry" else 1}
    if failure in {"json", "network"}:
        with pytest.raises(
            RetriesExceeded,
            match="ConnectionError" if failure == "network" else "Unable to parse JSON",
        ):
            _get_continuation_info(
                CONTINUATION_URL, post, params, json={"continuation": "abc"}
            )
    else:
        result = _get_continuation_info(
            CONTINUATION_URL,
            post,
            params,
            json={"continuation": "abc"},
        )
        assert result == ({} if failure == "http" else expected)
        if failure == "http":
            assert reply.json.call_count == 2
        else:
            assert post.call_count == 2


@pytest.mark.parametrize("initial", [False, True])
def test_requests_reject_disabled_attempts(initial):
    with pytest.raises(ValueError, match="max_attempts"):
        if initial:
            _get_initial_info(
                f"{HOME}/watch?v=test", Mock(), {"max_attempts": 0}, *INITIAL_PATTERNS
            )
        else:
            _get_continuation_info(CONTINUATION_URL, Mock(), {"max_attempts": 0})


@pytest.mark.parametrize("mode", ["missing", "success", "network-retry"])
def test_initial_info(monkeypatch, mode):
    returns(monkeypatch, "client_requests_initial.regex_search", "{}")
    patch(
        monkeypatch,
        "client_requests_initial.try_parse_json",
        lambda _value, default=None: {"contents": {}} if default is None else default,
    )
    returns(monkeypatch, "client_requests_initial.get_title_of_webpage", "Missing")
    reply = http_response(404 if mode == "missing" else 200, text="<html>ok</html>")
    get = Mock(return_value=reply)
    params = None
    if mode == "network-retry":
        get.side_effect = [RequestsConnectionError("boom"), reply]
        params = {"max_attempts": 2}
    if mode == "missing":
        with pytest.raises(VideoNotFound, match="Missing"):
            _get_initial_info(
                f"{HOME}/watch?v=missing", get, {"max_attempts": 1}, *INITIAL_PATTERNS
            )
    else:
        result = _get_initial_info(
            f"{HOME}/watch?v=test", get, params, *INITIAL_PATTERNS
        )
        assert result == ({"contents": {}}, {}, {})
        assert get.call_count == (2 if mode == "network-retry" else 1)


@pytest.mark.parametrize(
    ("existing", "broken"),
    [("f1=val1&f2=val2", False), ("broken", True)],
)
def test_initialize_pref_preserves_or_recovers_cookie(monkeypatch, existing, broken):
    session = _FakeSession({"PREF": existing})
    if broken:
        patch(monkeypatch, "client_auth.parse_qsl", Mock(side_effect=ValueError))
    auth._initialize_pref(session)
    if broken:
        assert session.set_calls == [(".youtube.com", "PREF", "hl=en&tz=UTC", {})]
    else:
        value = session.cookies["PREF"]
        assert all(part in value for part in ("hl=en", "tz=UTC", "f1=val1"))
