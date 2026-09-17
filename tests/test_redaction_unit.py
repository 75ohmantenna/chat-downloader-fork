# SPDX-License-Identifier: MIT

"""Unit tests for src/chat_downloader/redaction.py."""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

import chat_downloader.debugging as dbg
import chat_downloader.redaction as red
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.session import ChatDownloaderSession
from tests.core_third_helpers import captured_logs, restore_loggers


@pytest.fixture(autouse=True)
def _restore_logging_state():
    with restore_loggers():
        yield


@pytest.fixture
def sample_dir(tmp_path, monkeypatch):
    path = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(path))
    dbg.set_log_level("debug")
    return path


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("proxy", "http://user:pass@example.invalid:8080", red.REDACTED),
        ("cookies", "/tmp/cookies.txt", red.REDACTED),
        ("connect_timeout", 10.0, 10.0),
    ],
)
def test_redacts_sensitive_init_fields(field, value, expected):
    assert red.sanitize_for_log({field: value}) == {field: expected}


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        (name, value, value)
        for name, value in [
            ("User-Agent", "TestAgent/1.0"),
            ("Content-Type", "application/json"),
            ("Accept", "*/*"),
            ("Accept-Language", "en-US"),
        ]
    ]
    + [
        (name, value, red.REDACTED)
        for name, value in [
            ("X-Auth-Token", "secret-token"),
            ("Api-Key", "secret-key"),
            ("X-Service-Credential", "credential"),
            ("X-Custom", "Bearer embedded-secret"),
            ("X-Custom", "Basic dXNlcjpwYXNz"),
        ]
    ],
)
def test_header_redaction(name, value, expected):
    headers = {name: value, "Authorization": "Bearer tok", "Cookie": "sid=abc"}
    assert red.sanitize_for_log({"headers": headers}) == {
        "headers": {
            name: expected,
            "Authorization": red.REDACTED,
            "Cookie": red.REDACTED,
        }
    }


def test_redacts_nested_sensitive_keys_in_sequences() -> None:
    assert red.sanitize_for_log(
        ({"authorization": "Bearer token"}, [{"cookie": "sid=abc"}])
    ) == ({"authorization": red.REDACTED}, [{"cookie": red.REDACTED}])


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        (key, red.REDACTED)
        for key in ["accessToken", "feedbackToken", "refresh_token", "password"]
    ]
    + [
        (key, "VISIBLE")
        for key in ["author", "authority", "continuationPolicy", "tokenizer", "monkey"]
    ],
)
def test_sensitive_key_classifier(key, expected):
    assert red.sanitize_for_log({key: "VISIBLE", "headers": {key: "VISIBLE"}}) == {
        key: expected,
        "headers": {key: expected},
    }
    if expected == "VISIBLE":
        url = f"https://example.invalid/?{key}=VISIBLE"
        assert f"{key}=VISIBLE" in red.render_for_log(url)


@pytest.mark.parametrize(
    "key",
    ["x-api-key", "x-goog-visitor-id", "continuation"],
)
def test_sensitive_key_classifier_redacts_valid_url_queries(key: str) -> None:
    rendered = red.render_for_log(f"https://example.invalid/?{key}=QUERY_SECRET")

    assert "QUERY_SECRET" not in rendered
    assert "redacted" in rendered


def test_redacts_google_api_key_in_generic_key_query() -> None:
    api_key = "AIza" + "A" * 35

    rendered = red.render_for_log(
        f"https://www.youtube.com/youtubei/v1/live_chat?key={api_key}"
    )

    assert api_key not in rendered
    assert "key=%3Credacted%3E" in rendered


def test_redacts_tokens_from_urllib3_request_target_log() -> None:
    api_key = "AIza" + "A" * 35
    continuation = "opaque-continuation"
    message = (
        'https://www.youtube.com:443 "POST '
        f"/youtubei/v1/live_chat?key={api_key}&continuation={continuation} "
        'HTTP/1.1" 200 None'
    )

    rendered = red.render_for_log(message)

    assert api_key not in rendered
    assert continuation not in rendered
    assert rendered.count(red.REDACTED) == 2


def test_structured_redaction_preserves_control_characters() -> None:
    value = "a\nb\tc\x00"

    assert red.sanitize_for_log(value) == value
    assert red.render_for_log(value) == r"a\nb\tc\x00"


@pytest.mark.parametrize(
    ("serialized", "expected"),
    [
        (text, None)
        for text in [
            '{"Authorization": "Bearer LOG_SECRET"}',
            "{'Authorization': 'Bearer LOG_SECRET'}",
        ]
    ]
    + [
        (text, text)
        for text in [
            '{"author": "VISIBLE"}',
            "https://example.invalid/?key=display-name",
        ]
    ],
)
def test_render_serialized_values(serialized, expected):
    rendered = red.render_for_log(serialized)
    if expected is None:
        assert "LOG_SECRET" not in rendered
        assert red.REDACTED in rendered
    else:
        assert rendered == expected


def test_logging_filter_redacts_urls_and_visitor_data_and_escapes_controls() -> None:
    with captured_logs() as stream:
        dbg.set_log_level("debug")
        child_logger = logging.getLogger("chat_downloader.sites.logging_boundary")
        child_logger.setLevel(logging.DEBUG)
        child_logger.debug(
            "url=https://user:URL_SECRET@example.invalid/watch?"
            "token=TOKEN_SECRET&visitorData=VISITOR_SECRET "
            "visitor=VISITOR_SECRET title=first\nforged\x1b[31mred\x00",
        )

    output = stream.getvalue()
    for secret in ("URL_SECRET", "TOKEN_SECRET", "VISITOR_SECRET"):
        assert secret not in output
    assert "\\n" in output
    assert "\\x1b" in output
    assert "\\x00" in output


def test_logging_filter_redacts_exceptions_and_stack_information() -> None:
    logger = logging.Logger("chat_downloader.sites.exception_boundary", logging.DEBUG)

    def fail_request() -> None:
        raise ValueError("token=EXCEPTION_SECRET")

    with captured_logs(logger, safe=True, formatted=True) as stream:
        try:
            fail_request()
        except ValueError:
            logger.exception("request failed")
        record = logger.makeRecord(
            logger.name, logging.ERROR, __file__, 1, "stack failed", (), None
        )
        record.stack_info = "Stack:\n token=STACK_SECRET"
        record.exc_text = "ValueError: token=PREFORMATTED_SECRET"
        logger.handle(record)

    output = stream.getvalue()
    assert "request failed" in output
    assert "stack failed" in output
    assert "EXCEPTION_SECRET" not in output
    assert "STACK_SECRET" not in output
    assert "PREFORMATTED_SECRET" not in output
    assert red.REDACTED in output


def test_logging_filter_handles_malformed_urls_without_leaking_secrets() -> None:
    messages = (
        "url=http://[broken",
        "url=https://user:SECRET@[broken",
        "url=bad://user:SECRET@",
        "url=https://example.invalid/?token=SECRET%",
        "url=http://[broken/path?author=VISIBLE&token=SECRET",
    )
    logger = logging.Logger("chat_downloader.sites.malformed_url_boundary")
    with captured_logs(logger, safe=True, formatted=True) as stream:
        for message in messages:
            logger.warning(message)

    output = stream.getvalue()
    assert output.count("[WARNING]") == len(messages)
    assert "SECRET" not in output
    assert red.REDACTED in output


def test_proxy_validation_runtime_logging_redacts_malformed_credentials() -> None:
    class ProxyValidationDownloader:
        def __init__(self, *, proxy: str | None = None, **_: object) -> None:
            self.session: ChatDownloaderSession = ChatDownloaderSession(proxy=proxy)

        def get_chat(self, **_: object) -> tuple[()]:
            return ()

        def close(self) -> None:
            self.session.close()

    proxy_values = (
        "user:PROXY_SECRET@example.invalid:8080",
        "http:///user:PROXY_SECRET@example.invalid",
        "https://user:PROXY_SECRET@example.invalid",
        "bad://user:PROXY_SECRET@",
        "http://user:PROXY_SECRET@example.invalid\uff0fx",
    )
    with captured_logs(safe=True) as stream:
        results = [
            execute_run(ProxyValidationDownloader, proxy=proxy)
            for proxy in proxy_values
        ]
        dbg.logger.warning("contact=user@example.invalid")

    output = stream.getvalue()
    assert [result.success for result in results] == [False, False, True, False, False]
    assert all("PROXY_SECRET" not in (result.error_message or "") for result in results)
    assert "PROXY_SECRET" not in output
    assert "contact=user@example.invalid" in output


def test_capture_debug_sample_writes_sanitized_json_deterministically(sample_dir):
    payload = {
        "authorization": "secret",
        "headers": {"Authorization": "Bearer secret"},
        "value": 7,
        "text": "a\nb\tc",
    }
    path = red.capture_debug_sample("Unknown continuation: heartbeat", payload)
    assert path is not None
    assert red.capture_debug_sample("Unknown continuation: heartbeat", payload) == path
    assert json.loads(Path(path).read_text(encoding="utf-8")) == {
        **payload,
        "authorization": red.REDACTED,
        "headers": {"Authorization": red.REDACTED},
    }


def test_bounded_capture_composes_opt_in_attempt_limits_and_sanitization(
    sample_dir, monkeypatch
):
    env_name = "CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES"
    monkeypatch.delenv(env_name, raising=False)
    disabled = red.BoundedSampleCapture(env_name, 2)
    payload = {"authorization": "private-token", "value": 1}
    assert disabled.capture("first", payload) is None
    assert not sample_dir.exists()

    monkeypatch.setenv(env_name, " YES ")
    capture = red.BoundedSampleCapture(env_name, 2)
    monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES")
    assert capture.capture("first", payload) is None
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    assert disabled.capture("first", payload) is None
    first = capture.capture("first", payload)
    assert first is not None
    assert json.loads(Path(first).read_text(encoding="utf-8")) == {
        "authorization": red.REDACTED,
        "value": 1,
    }
    assert capture.capture("first", {"value": 2}) is None
    for value in (1, 2):
        assert capture.capture("second", {"value": value}) is not None
    assert capture.capture("second", {"value": 3}) is None
    assert len(list(sample_dir.glob("*.json"))) == 3


@pytest.mark.parametrize("grouped", [False, True])
def test_capture_debug_sample_limits_unique_payloads(sample_dir, grouped):
    bounds = {"sample_limit": 2}
    if grouped:
        bounds.update(sample_group="events", group_limit=2)

    def capture(value):
        label = f"event-{value}" if grouped else "bounded-label"
        return red.capture_debug_sample(label, {"value": value}, **bounds)

    first, second, dropped, duplicate = [capture(value) for value in (1, 2, 3, 1)]
    assert first is not None
    assert second is not None
    assert dropped is None
    assert duplicate == first
    assert len(list(sample_dir.glob("*.json"))) == 2


def test_capture_debug_sample_releases_group_slot_when_label_is_full(sample_dir):
    bounds = {"sample_group": "events", "group_limit": 1}
    assert (
        red.capture_debug_sample(
            "blocked-label", {"value": 1}, sample_limit=0, **bounds
        )
        is None
    )
    assert (
        red.capture_debug_sample("open-label", {"value": 2}, sample_limit=1, **bounds)
        is not None
    )


@pytest.mark.parametrize(
    "bounds",
    [{"sample_group": "events"}, {"group_limit": 2}, {"sample_limit": -1}],
)
def test_capture_debug_sample_rejects_invalid_limits(sample_dir, bounds):
    assert red.capture_debug_sample("invalid-limit", {"value": 1}, **bounds) is None
    assert not sample_dir.exists()


def test_capture_debug_sample_scrubs_inline_tokens_in_values(sample_dir):
    synthetic_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    payload = {
        "log_line": (
            f"Sent request with Authorization: Bearer {synthetic_jwt} "
            "and SAPISIDHASH 1234567890_abcdef0987654321deadbeef"
        ),
        "ok": "hello world",
    }
    path = red.capture_debug_sample("inline-secret", payload)
    assert path is not None
    contents = Path(path).read_text(encoding="utf-8")
    assert synthetic_jwt not in contents
    assert "1234567890_abcdef0987654321deadbeef" not in contents
    assert "hello world" in contents


@pytest.mark.parametrize(("enabled", "level"), [(False, "debug"), (True, "info")])
def test_sampling_requires_both_opt_ins(sample_dir, monkeypatch, enabled, level):
    if not enabled:
        monkeypatch.delenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", raising=False)
    dbg.set_log_level(level)
    red.preflight_debug_samples()
    assert red.capture_debug_sample("label", {"value": 1}) is None
    assert not sample_dir.exists()


def test_capture_debug_sample_oserror_returns_none(sample_dir):
    with patch.object(Path, "mkdir", side_effect=OSError("disk full")):
        assert red.capture_debug_sample("test-label", {"key": "value"}) is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes required")
def test_capture_debug_sample_uses_private_directory_and_file_modes(sample_dir):
    previous_umask = os.umask(0o022)
    try:
        sample_path = red.capture_debug_sample("mode-probe", {"value": "sample"})
    finally:
        os.umask(previous_umask)
    assert sample_path is not None
    assert sample_dir.stat().st_mode & 0o777 == 0o700
    assert Path(sample_path).stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX filesystem guards required")
@pytest.mark.parametrize("unsafe_kind", ["symlink", "permissions"])
def test_capture_debug_sample_rejects_unsafe_existing_file(sample_dir, unsafe_kind):
    victim = sample_dir.parent / "victim.json"
    victim.write_text("original", encoding="utf-8")
    original_path = red.capture_debug_sample("file-probe", {"value": "sample"})
    assert original_path is not None
    path = Path(original_path)
    if unsafe_kind == "symlink":
        path.unlink()
        path.symlink_to(victim)
    else:
        path.chmod(0o644)
    assert red.capture_debug_sample("file-probe", {"value": "sample"}) is None
    assert victim.read_text(encoding="utf-8") == "original"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not portable")
def test_capture_debug_sample_rejects_symlinked_directory(sample_dir):
    real_directory = sample_dir.parent / "real-samples"
    real_directory.mkdir(mode=0o700)
    sample_dir.symlink_to(real_directory, target_is_directory=True)
    assert red.capture_debug_sample("directory-probe", {"value": "sample"}) is None
    assert list(real_directory.iterdir()) == []


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="ownership checks unavailable")
def test_capture_debug_sample_rejects_foreign_owned_directory(sample_dir, monkeypatch):
    current_uid = os.getuid()
    monkeypatch.setattr(red.os, "getuid", lambda: current_uid + 1)
    assert red.capture_debug_sample("owner-probe", {"value": "sample"}) is None


def test_capture_debug_sample_fails_closed_without_secure_directory_fd(
    sample_dir, monkeypatch
):
    monkeypatch.setattr(red.os, "supports_dir_fd", set())
    assert red.capture_debug_sample("fallback-probe", {"value": "sample"}) is None
    assert list(sample_dir.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="secure directory fds are POSIX-only")
@pytest.mark.parametrize("directory", [False, True])
def test_capture_debug_sample_rechecks_opened_entries(
    sample_dir, monkeypatch, directory
):
    original_fstat = os.fstat
    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG

    def insecure_mode(descriptor):
        entry = original_fstat(descriptor)
        if expected_kind(entry.st_mode):
            values = list(entry)
            values[0] = (entry.st_mode & ~0o777) | (0o755 if directory else 0o644)
            return os.stat_result(values)
        return entry

    monkeypatch.setattr(red.os, "fstat", insecure_mode)
    assert red.capture_debug_sample("fd-probe", {"value": "sample"}) is None
    assert list(sample_dir.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="secure directory fds are POSIX-only")
def test_capture_debug_sample_removes_failed_write_and_retry_succeeds(
    sample_dir, monkeypatch
):
    class FailingSampleFile:
        def __init__(self, descriptor, *args, **kwargs):
            self.descriptor = descriptor

        def __enter__(self):
            return self

        def __exit__(self, *_):
            os.close(self.descriptor)

        def write(self, _value):
            raise OSError("forced sample write failure")

    bounds = {"sample_limit": 1, "sample_group": "write-failures", "group_limit": 1}
    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(red.os, "fdopen", FailingSampleFile)
        failed_path = red.capture_debug_sample(
            "write-failure", {"value": "sample"}, **bounds
        )
    assert failed_path is None
    assert list(sample_dir.iterdir()) == []
    retry_path = red.capture_debug_sample(
        "write-failure", {"value": "sample"}, **bounds
    )
    assert retry_path is not None
    assert json.loads(Path(retry_path).read_text(encoding="utf-8")) == {
        "value": "sample"
    }


def test_preflight_stops_before_session_and_allows_corrected_retry(sample_dir):
    from chat_downloader.models import ChatRequest
    from chat_downloader.runtime.site_dispatch import dispatch_chat

    sample_dir.mkdir(mode=0o755)
    sample_dir.chmod(0o755)

    class Owner:
        def create_session(self, *args, **kwargs):
            pytest.fail("preflight must precede session creation")

    with pytest.raises(OSError):
        dispatch_chat(Owner(), ChatRequest(url="https://kick.com/example"))
    assert list(sample_dir.iterdir()) == []
    sample_dir.chmod(0o700)
    red.preflight_debug_samples()
    assert red.capture_debug_sample("preflight-retry", {"ok": True}, sample_limit=1)


def test_preflight_does_not_cache_directory_safety(sample_dir):
    red.preflight_debug_samples()
    assert stat.S_IMODE(sample_dir.stat().st_mode) == 0o700
    sample_dir.chmod(0o755)
    assert red.capture_debug_sample("unsafe-after-preflight", {"x": 1}) is None
    assert list(sample_dir.iterdir()) == []
