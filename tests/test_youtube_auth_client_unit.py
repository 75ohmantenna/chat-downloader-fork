# SPDX-License-Identifier: MIT

from __future__ import annotations

import hashlib
from json.decoder import JSONDecodeError

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

import chat_downloader.sites.youtube.client_auth as _yt_auth
import chat_downloader.sites.youtube.client_context as _yt_context
import chat_downloader.sites.youtube.client_requests_continuation as _yt_continuation
import chat_downloader.sites.youtube.client_requests_initial as _yt_initial
from chat_downloader.errors import VideoNotFound

# Convenience aliases that mirror the old ``client`` facade names.
_parse_data_sync_id = _yt_auth._parse_data_sync_id
_make_sid_authorization = _yt_auth._make_sid_authorization
_generate_sapisidhash_header = _yt_auth._generate_sapisidhash_header
_initialize_consent = _yt_auth._initialize_consent
_get_sid_cookies = _yt_auth._get_sid_cookies
_extract_account_syncid = _yt_context._extract_account_syncid
_generate_headers = _yt_context._generate_headers
_get_innertube_context = _yt_context._get_innertube_context
apply_request_profile_to_ytcfg = _yt_context.apply_request_profile_to_ytcfg
_get_continuation_info = _yt_continuation._get_continuation_info
_get_initial_info = _yt_initial._get_initial_info


class _FakeSession:
    def __init__(self, cookies=None) -> None:
        self.cookies = dict(cookies or {})
        self.set_calls = []

    def get_cookie_value(self, name, default=None):
        return self.cookies.get(name, default)

    def set_cookie_value(self, domain, name, value, **kwargs) -> None:
        self.set_calls.append((domain, name, value, kwargs))
        self.cookies[name] = value


class _Resp:
    def __init__(self, status_code, payload=None, text="") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._json_calls = 0

    def json(self):
        self._json_calls += 1
        payload = self._payload
        if callable(payload):
            return payload(self._json_calls)
        return payload


class _PageResp:
    def __init__(self, status_code, text) -> None:
        self.status_code = status_code
        self.text = text


_SUCCESS_CONTINUATION_PAYLOAD = {
    "continuationContents": {"liveChatContinuation": {"actions": []}},
}


def test_parse_data_sync_id_handles_empty_single_and_delegated_values() -> None:
    assert _parse_data_sync_id(None) == (None, None)
    assert _parse_data_sync_id("") == (None, None)
    assert _parse_data_sync_id("user-session") == (None, "user-session")
    assert _parse_data_sync_id("delegated||user") == (
        "delegated",
        "user",
    )


def test_make_sid_authorization_supports_additional_parts() -> None:
    auth = _make_sid_authorization(
        "SAPISIDHASH",
        "sid-value",
        "https://www.youtube.com",
        123,
        {"u": "user-session"},
    )

    expected_hash = hashlib.sha1(
        b"user-session 123 sid-value https://www.youtube.com",
    ).hexdigest()
    assert auth == f"SAPISIDHASH 123_{expected_hash}_u"


def test_session_id_parts_uses_youtube_session_binding_key() -> None:
    assert _yt_auth._session_id_parts({"DATASYNC_ID": "delegated||user-session"}) == {
        "u": "user-session"
    }


def test_generate_sapisidhash_header_returns_none_without_sid_cookies() -> None:
    session = _FakeSession()

    assert _generate_sapisidhash_header(session, "https://www.youtube.com") is None
    assert session.set_calls == []


def test_ensure_primary_sapisid_returns_none_without_promotable_cookie() -> None:
    session = _FakeSession()

    assert _yt_auth._ensure_primary_sapisid(session, (None, None, None), 1234) is None
    assert session.set_calls == []


def test_generate_sapisidhash_header_promotes_cookie_and_uses_datasync_id(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.client_auth.time.time",
        lambda: 1234,
    )
    session = _FakeSession(
        {
            "__Secure-1PAPISID": "onep",
            "__Secure-3PAPISID": "threep",
        },
    )

    header = _generate_sapisidhash_header(
        session,
        "https://www.youtube.com",
        {"DATASYNC_ID": "delegated||user-session"},
    )

    assert session.set_calls == [
        (
            ".youtube.com",
            "SAPISID",
            "threep",
            {"secure": True, "expire_time": 4834},
        ),
    ]
    assert header == " ".join(
        _make_sid_authorization(
            scheme, sid, "https://www.youtube.com", 1234, {"u": "user-session"}
        )
        for scheme, sid in [
            ("SAPISIDHASH", "threep"),
            ("SAPISID1PHASH", "onep"),
            ("SAPISID3PHASH", "threep"),
        ]
    )


@pytest.mark.parametrize(
    ("cookies", "ytcfg", "promoted"),
    [
        ({"SAPISID": "direct-sapisid"}, None, False),
        ({"__Secure-3PAPISID": "threep"}, None, True),
        ({"__Secure-3PAPISID": "threep"}, {"OTHER_KEY": "value"}, True),
    ],
    ids=["primary-cookie", "no-config", "no-datasync-id"],
)
def test_generate_sapisidhash_header_without_session_binding(
    monkeypatch, cookies, ytcfg, promoted
):
    monkeypatch.setattr(_yt_auth.time, "time", lambda: 1234)
    session = _FakeSession(cookies)
    header = _generate_sapisidhash_header(session, "https://www.youtube.com", ytcfg)
    assert header is not None
    if not promoted:
        assert session.set_calls == []
        assert "SAPISIDHASH" in header


@pytest.mark.parametrize(
    ("cookies", "expected"),
    [
        ({"__Secure-3PSID": "present"}, []),
        ({"SOCS": "XYZ"}, []),
        ({}, [(".youtube.com", "SOCS", "CAI", {"secure": True})]),
        ({"SOCS": "CAAabc"}, [(".youtube.com", "SOCS", "CAI", {"secure": True})]),
    ],
    ids=["secure-cookie", "non-consented", "missing", "consented"],
)
def test_initialize_consent(cookies, expected) -> None:
    session = _FakeSession(cookies)
    _initialize_consent(session)
    assert session.set_calls == expected


def test_get_sid_cookies_returns_all_variants() -> None:
    session = _FakeSession(
        {
            "SAPISID": "sap",
            "__Secure-1PAPISID": "onep",
            "__Secure-3PAPISID": "threep",
        },
    )

    assert _get_sid_cookies(session) == ("sap", "onep", "threep")


def test_extract_account_syncid_prefers_delegated_datasync_and_falls_back() -> None:
    assert _extract_account_syncid({"DATASYNC_ID": "delegated||user"}) == ("delegated")
    assert _extract_account_syncid({"DATASYNC_ID": "user-only"}) is None
    assert _extract_account_syncid({"DELEGATED_SESSION_ID": "fallback"}) == ("fallback")


def test_generate_headers_handles_optional_auth_and_minimal_paths() -> None:
    ytcfg = {
        "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
        "INNERTUBE_CLIENT_VERSION": "2.0",
        "ID_TOKEN": "obsolete-id-token",
        "DATASYNC_ID": "delegated||user",
        "SESSION_INDEX": 5,
        "LOGGED_IN": True,
        "INNERTUBE_CONTEXT": {
            "client": {
                "visitorData": "visitor-1",
                "userAgent": "TestAgent/1.0",
            },
        },
    }

    headers = _generate_headers(
        ytcfg,
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: "AUTH",
    )
    minimal_headers = _generate_headers(
        {
            "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
            "INNERTUBE_CLIENT_VERSION": "2.0",
        },
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: None,
    )

    assert "x-youtube-identity-token" not in headers
    assert headers["x-goog-pageid"] == "delegated"
    assert headers["x-goog-authuser"] == "5"
    assert headers["x-goog-visitor-id"] == "visitor-1"
    assert headers["user-agent"] == "TestAgent/1.0"
    assert headers["x-youtube-bootstrap-logged-in"] == "true"
    assert headers["authorization"] == "AUTH"
    assert headers["x-origin"] == "https://www.youtube.com"
    assert "x-goog-authuser" not in minimal_headers
    assert "x-origin" not in minimal_headers
    assert "authorization" not in minimal_headers
    assert "x-goog-pageid" not in minimal_headers


def test_generate_headers_omits_missing_client_metadata() -> None:
    headers = _generate_headers(
        {},
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: None,
    )

    assert headers == {"origin": "https://www.youtube.com"}


def test_generate_headers_preserves_zero_session_index() -> None:
    headers = _generate_headers(
        {"SESSION_INDEX": 0},
        session=object(),
        yt_home="https://www.youtube.com",
        sapisidhash_generator=lambda *_a, **_k: None,
    )

    assert headers["x-goog-authuser"] == "0"


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ("invalid", {}),
        ([], {"client": {"hl": "en", "timeZone": "UTC", "utcOffsetMinutes": 0}}),
        (
            {"client": None},
            {"client": {"hl": "en", "timeZone": "UTC", "utcOffsetMinutes": 0}},
        ),
    ],
)
def test_get_innertube_context_handles_non_dict_and_missing_client(context, expected):
    assert _get_innertube_context({"INNERTUBE_CONTEXT": context}) == expected


def test_apply_request_profile_to_ytcfg_keeps_body_and_headers_aligned() -> None:
    original = {
        "INNERTUBE_CONTEXT_CLIENT_NAME": 1,
        "INNERTUBE_CLIENT_VERSION": "old-web-version",
        "INNERTUBE_CONTEXT": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "old-web-version",
                "visitorData": "visitor",
            },
        },
    }

    updated = apply_request_profile_to_ytcfg(original, "youtube_android")

    assert updated["INNERTUBE_CONTEXT_CLIENT_NAME"] == 3
    assert updated["INNERTUBE_CLIENT_VERSION"] == "21.26.364"
    assert updated["INNERTUBE_CONTEXT"]["client"]["clientName"] == "ANDROID"
    assert updated["INNERTUBE_CONTEXT"]["client"]["visitorData"] == "visitor"
    assert original["INNERTUBE_CONTEXT"]["client"]["clientName"] == "WEB"


def test_get_continuation_info_logs_non_retriable_http_errors_without_json_body() -> (
    None
):
    response = _Resp(
        404,
        payload=lambda call_number: (
            (_ for _ in ()).throw(ValueError("bad json")) if call_number == 1 else {}
        ),
    )

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        lambda *_a, **_k: response,
        {"max_attempts": 1},
        json={"continuation": "abc"},
    )

    assert result == {}
    assert response._json_calls == 2


def test_get_continuation_info_retries_after_json_decode_error() -> None:
    calls = {"count": 0}

    def session_post(_url, **_kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return _Resp(
                200,
                payload=lambda _n: (_ for _ in ()).throw(
                    JSONDecodeError("bad json", "doc", 0),
                ),
                text="not-json",
            )
        return _Resp(200, _SUCCESS_CONTINUATION_PAYLOAD)

    result = _get_continuation_info(
        "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
        session_post,
        {"max_attempts": 2},
        json={"continuation": "abc"},
    )

    assert result == _SUCCESS_CONTINUATION_PAYLOAD
    assert calls["count"] == 2


@pytest.mark.parametrize("network", [False, True], ids=["json-parse", "network"])
def test_get_continuation_info_raises_retries_exceeded(network) -> None:
    from chat_downloader.errors import RetriesExceeded

    def fail(*_args, **_kwargs):
        if network:
            raise RequestsConnectionError("boom")
        raise JSONDecodeError("bad json", "doc", 0)

    response = _Resp(200, payload=fail, text="not-json")
    with pytest.raises(
        RetriesExceeded, match="ConnectionError" if network else "Unable to parse JSON"
    ):
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            fail if network else lambda *_a, **_k: response,
            {"max_attempts": 1},
            json={"continuation": "abc"},
        )


def test_get_continuation_info_raises_when_attempts_disabled() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            lambda *_a, **_k: _Resp(200, _SUCCESS_CONTINUATION_PAYLOAD),
            {"max_attempts": 0},
            json={"continuation": "abc"},
        )


def test_get_initial_info_raises_video_not_found(monkeypatch) -> None:
    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: {"contents": {}} if default is None else default,
    )
    monkeypatch.setattr(_yt_initial, "get_title_of_webpage", lambda _html: "Missing")

    with pytest.raises(VideoNotFound, match="Missing"):
        _get_initial_info(
            "https://www.youtube.com/watch?v=missing",
            lambda _url: _PageResp(404, "<html>missing</html>"),
            {"max_attempts": 1},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )


@pytest.mark.parametrize(
    "retry_network", [False, True], ids=["missing-params", "network-retry"]
)
def test_get_initial_info_success(monkeypatch, retry_network) -> None:
    calls = []

    def session_get(_url):
        calls.append(_url)
        if retry_network and len(calls) == 1:
            raise RequestsConnectionError("boom")
        return _PageResp(200, "<html>ok</html>")

    monkeypatch.setattr(_yt_initial, "regex_search", lambda *_a, **_k: "{}")
    monkeypatch.setattr(
        _yt_initial,
        "try_parse_json",
        lambda _value, default=None: {"contents": {}} if default is None else default,
    )
    result = _get_initial_info(
        "https://www.youtube.com/watch?v=test",
        session_get,
        {"max_attempts": 2} if retry_network else None,
        r"ytInitialData",
        r"ytcfg",
        r"ytInitialPlayerResponse",
    )
    assert result == ({"contents": {}}, {}, {})
    assert len(calls) == (2 if retry_network else 1)


def test_get_initial_info_raises_retries_exceeded_when_attempts_disabled() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        _get_initial_info(
            "https://www.youtube.com/watch?v=test",
            lambda _url: _PageResp(200, "<html>ok</html>"),
            {"max_attempts": 0},
            r"ytInitialData",
            r"ytcfg",
            r"ytInitialPlayerResponse",
        )


@pytest.mark.parametrize(
    ("existing", "broken"), [("f1=val1&f2=val2", False), ("broken", True)]
)
def test_initialize_pref_preserves_or_recovers_existing_cookie(
    monkeypatch, existing, broken
):
    session = _FakeSession({"PREF": existing})
    if broken:

        def invalid_cookie(_value):
            raise ValueError

        monkeypatch.setattr(_yt_auth, "parse_qsl", invalid_cookie)
    _yt_auth._initialize_pref(session)
    if broken:
        assert session.set_calls == [(".youtube.com", "PREF", "hl=en&tz=UTC", {})]
    else:
        value = session.cookies["PREF"]
        assert "hl=en" in value
        assert "tz=UTC" in value
        assert "f1=val1" in value
