# SPDX-License-Identifier: MIT

"""Unit tests for src/chat_downloader/redaction.py."""

from __future__ import annotations

import io
import json
import logging
import os
import stat
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

import chat_downloader.debugging as dbg
import chat_downloader.redaction as red
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.session import ChatDownloaderSession


@pytest.fixture(autouse=True)
def _restore_loggers():
    yield
    for configured_logger in dbg.loggers:
        configured_logger.disabled = False


@pytest.fixture(autouse=True)
def _restore_logger_levels():
    original_levels = [logger.level for logger in dbg.loggers]
    yield
    for logger, original_level in zip(dbg.loggers, original_levels, strict=True):
        logger.setLevel(original_level)


# ---------------------------------------------------------------------------
# sanitize_for_log()
# ---------------------------------------------------------------------------


def test_redacts_sensitive_init_fields_and_header_values() -> None:
    assert red.sanitize_for_log(
        {
            "headers": {
                "Authorization": "Bearer secret-token",
                "User-Agent": "TestAgent/1.0",
            },
            "proxy": "http://user:pass@example.invalid:8080",
            "cookies": "/tmp/cookies.txt",
            "connect_timeout": 10.0,
        }
    ) == {
        "headers": {
            "Authorization": red.REDACTED,
            "User-Agent": "TestAgent/1.0",
        },
        "proxy": red.REDACTED,
        "cookies": red.REDACTED,
        "connect_timeout": 10.0,
    }


def test_non_sensitive_headers_are_not_redacted() -> None:
    result = red.sanitize_for_log(
        {
            "headers": {
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Accept-Language": "en-US",
                "Authorization": "Bearer tok",
                "Cookie": "sid=abc",
            }
        }
    )
    assert result["headers"]["Content-Type"] == "application/json"
    assert result["headers"]["Accept"] == "*/*"
    assert result["headers"]["Accept-Language"] == "en-US"
    assert result["headers"]["Authorization"] == red.REDACTED
    assert result["headers"]["Cookie"] == red.REDACTED


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("X-Auth-Token", "secret-token"),
        ("Api-Key", "secret-key"),
        ("X-Service-Credential", "credential"),
        ("X-Custom", "Bearer embedded-secret"),
        ("X-Custom", "Basic dXNlcjpwYXNz"),
    ],
)
def test_redacts_custom_authentication_headers(name: str, value: str) -> None:
    result = red.sanitize_for_log({"headers": {name: value}})
    assert result == {"headers": {name: red.REDACTED}}


def test_redacts_nested_sensitive_keys_in_sequences() -> None:
    assert red.sanitize_for_log(
        ({"authorization": "Bearer token"}, [{"cookie": "sid=abc"}])
    ) == ({"authorization": red.REDACTED}, [{"cookie": red.REDACTED}])


@pytest.mark.parametrize(
    "key",
    ["accessToken", "feedbackToken", "refresh_token", "password"],
)
def test_sensitive_key_classifier_redacts_structured_values_and_headers(
    key: str,
) -> None:
    assert red.sanitize_for_log(
        {
            key: "STRUCTURED_SECRET",
            "headers": {key: "HEADER_SECRET"},
        }
    ) == {
        key: red.REDACTED,
        "headers": {key: red.REDACTED},
    }


@pytest.mark.parametrize("key", ["x-api-key", "x-goog-visitor-id"])
def test_sensitive_key_classifier_redacts_valid_url_queries(key: str) -> None:
    rendered = red.render_for_log(f"https://example.invalid/?{key}=QUERY_SECRET")

    assert "QUERY_SECRET" not in rendered
    assert "redacted" in rendered


@pytest.mark.parametrize("key", ["author", "authority", "tokenizer", "monkey"])
def test_sensitive_key_classifier_avoids_substring_false_positives(key: str) -> None:
    value = {
        key: "VISIBLE",
        "headers": {key: "VISIBLE"},
    }

    assert red.sanitize_for_log(value) == value
    assert f"{key}=VISIBLE" in red.render_for_log(
        f"https://example.invalid/?{key}=VISIBLE"
    )


def test_structured_redaction_preserves_control_characters() -> None:
    value = "a\nb\tc\x00"

    assert red.sanitize_for_log(value) == value
    assert red.render_for_log(value) == r"a\nb\tc\x00"


@pytest.mark.parametrize(
    "serialized",
    [
        '{"Authorization": "Bearer LOG_SECRET"}',
        "{'Authorization': 'Bearer LOG_SECRET'}",
    ],
)
def test_render_for_log_redacts_quoted_serialized_fields(serialized: str) -> None:
    rendered = red.render_for_log(serialized)

    assert "LOG_SECRET" not in rendered
    assert red.REDACTED in rendered


def test_render_for_log_preserves_non_sensitive_quoted_fields() -> None:
    serialized = '{"author": "VISIBLE"}'

    assert red.render_for_log(serialized) == serialized


def test_logging_filter_redacts_urls_and_visitor_data_and_escapes_controls() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    dbg.logger.addHandler(handler)
    try:
        dbg.set_log_level("debug")
        child_logger = logging.getLogger("chat_downloader.sites.logging_boundary")
        child_logger.setLevel(logging.DEBUG)
        child_logger.debug(
            "url=https://user:URL_SECRET@example.invalid/watch?"
            "token=TOKEN_SECRET&visitorData=VISITOR_SECRET "
            "visitor=VISITOR_SECRET title=first\nforged\x1b[31mred\x00",
        )
    finally:
        dbg.logger.removeHandler(handler)

    output = stream.getvalue()
    for secret in ("URL_SECRET", "TOKEN_SECRET", "VISITOR_SECRET"):
        assert secret not in output
    assert "\\n" in output
    assert "\\x1b" in output
    assert "\\x00" in output


def test_logging_filter_redacts_exceptions_and_stack_information() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    handler.addFilter(dbg._SafeLogFilter())
    logger = logging.Logger("chat_downloader.sites.exception_boundary", logging.DEBUG)
    logger.addHandler(handler)

    def fail_request() -> None:
        raise ValueError("token=EXCEPTION_SECRET")

    try:
        fail_request()
    except ValueError:
        logger.exception("request failed")

    record = logger.makeRecord(
        logger.name,
        logging.ERROR,
        __file__,
        1,
        "stack failed",
        (),
        None,
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
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    handler.addFilter(dbg._SafeLogFilter())
    logger = logging.Logger("chat_downloader.sites.malformed_url_boundary")
    logger.addHandler(handler)
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
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(dbg._SafeLogFilter())
    dbg.logger.addHandler(handler)
    try:
        results = [
            execute_run(ProxyValidationDownloader, proxy=proxy)
            for proxy in proxy_values
        ]
        dbg.logger.warning("contact=user@example.invalid")
    finally:
        dbg.logger.removeHandler(handler)

    output = stream.getvalue()
    assert [result.success for result in results] == [False, False, True, False, False]
    assert all("PROXY_SECRET" not in (result.error_message or "") for result in results)
    assert "PROXY_SECRET" not in output
    assert "contact=user@example.invalid" in output


# ---------------------------------------------------------------------------
# capture_debug_sample()
# ---------------------------------------------------------------------------


def test_capture_debug_sample_writes_sanitized_json_deterministically() -> None:
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path1 = red.capture_debug_sample(
            "Unknown continuation: heartbeat",
            {
                "authorization": "secret",
                "headers": {"Authorization": "Bearer secret"},
                "value": 7,
                "text": "a\nb\tc",
            },
        )
        path2 = red.capture_debug_sample(
            "Unknown continuation: heartbeat",
            {
                "authorization": "secret",
                "headers": {"Authorization": "Bearer secret"},
                "value": 7,
                "text": "a\nb\tc",
            },
        )

        assert path1 == path2
        assert path1 is not None
        with open(path1, encoding="utf-8") as fh:
            data = json.load(fh)
        assert data == {
            "authorization": red.REDACTED,
            "headers": {"Authorization": red.REDACTED},
            "value": 7,
            "text": "a\nb\tc",
        }


def test_capture_debug_sample_logs_fixture_hint() -> None:
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        with patch.object(dbg.logger, "debug") as mock_debug:
            path = red.capture_debug_sample(
                "youtube-unknown-continuation-heartbeat",
                {"continuation_key": "heartbeat"},
            )

        assert path is not None
        mock_debug.assert_called_with(
            "Captured debug sample: path=%s suggested_fixture_site=%s "
            "suggested_fixture_group=%s suggested_fixture_name=%s",
            Path(path),
            "youtube",
            "continuations",
            "youtube-unknown-continuation-heartbeat",
        )


def test_capture_debug_sample_scrubs_inline_tokens_in_values() -> None:
    """Tokens inside string values are redacted even without a sensitive key."""
    synthetic_jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    )
    payload = {
        # Key not in allowlist -> the inline header value passes the key
        # check but must still be scrubbed by the regex pass.
        "log_line": (
            f"Sent request with Authorization: Bearer {synthetic_jwt} "
            "and SAPISIDHASH 1234567890_abcdef0987654321deadbeef"
        ),
        "ok": "hello world",
    }

    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path = red.capture_debug_sample("inline-secret", payload)
        assert path is not None
        with open(path, encoding="utf-8") as fh:
            contents = fh.read()

    # The literal secret bytes must not appear anywhere in the sample.
    assert synthetic_jwt not in contents
    assert "1234567890_abcdef0987654321deadbeef" not in contents
    # Untouched values survive.
    assert "hello world" in contents


def test_capture_debug_sample_is_disabled_without_env_flag() -> None:
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {"CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir},
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path = red.capture_debug_sample("label", {"value": 1})
        assert path is None
        assert os.listdir(temp_dir) == []


def test_capture_debug_sample_digest_is_fips_safe() -> None:
    """sha1 must use usedforsecurity=False for FIPS-enabled Python 3.12+."""
    with (
        tempfile.TemporaryDirectory() as temp_dir,
        patch.dict(
            os.environ,
            {
                "CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES": "1",
                "CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR": temp_dir,
            },
            clear=False,
        ),
    ):
        dbg.set_log_level("debug")
        path = red.capture_debug_sample("fips_probe", {"x": 1})
        assert path is not None
        assert path.endswith(".json")


def test_capture_debug_sample_oserror_returns_none(tmp_path, monkeypatch) -> None:
    import logging
    from unittest.mock import patch as stdlib_patch

    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "true")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(tmp_path))

    original_level = dbg.logger.level
    dbg.logger.setLevel(logging.DEBUG)
    try:
        with stdlib_patch(
            "chat_downloader.redaction.Path.mkdir",
            side_effect=OSError("disk full"),
        ):
            result = red.capture_debug_sample("test-label", {"key": "value"})
        assert result is None
    finally:
        dbg.logger.setLevel(original_level)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes required")
def test_capture_debug_sample_uses_private_directory_and_file_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    previous_umask = os.umask(0o022)
    try:
        dbg.set_log_level("debug")
        sample_path = red.capture_debug_sample("mode-probe", {"value": "sample"})
    finally:
        os.umask(previous_umask)

    assert sample_path is not None
    assert sample_dir.stat().st_mode & 0o777 == 0o700
    assert Path(sample_path).stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not portable")
def test_capture_debug_sample_rejects_existing_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_dir = tmp_path / "samples"
    victim = tmp_path / "victim.json"
    victim.write_text("original", encoding="utf-8")
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    dbg.set_log_level("debug")
    original_path = red.capture_debug_sample("symlink-probe", {"value": "sample"})
    assert original_path is not None
    Path(original_path).unlink()
    Path(original_path).symlink_to(victim)

    sample_path = red.capture_debug_sample("symlink-probe", {"value": "sample"})

    assert sample_path is None
    assert victim.read_text(encoding="utf-8") == "original"


@pytest.mark.skipif(os.name == "nt", reason="symlink creation is not portable")
def test_capture_debug_sample_rejects_symlinked_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_directory = tmp_path / "real-samples"
    real_directory.mkdir(mode=0o700)
    sample_dir = tmp_path / "samples"
    sample_dir.symlink_to(real_directory, target_is_directory=True)
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    dbg.set_log_level("debug")

    sample_path = red.capture_debug_sample("directory-probe", {"value": "sample"})

    assert sample_path is None
    assert list(real_directory.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes required")
def test_capture_debug_sample_rejects_insecure_existing_file_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    dbg.set_log_level("debug")
    original_path = red.capture_debug_sample("mode-probe", {"value": "sample"})
    assert original_path is not None
    Path(original_path).chmod(0o644)

    sample_path = red.capture_debug_sample("mode-probe", {"value": "sample"})

    assert sample_path is None


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="ownership checks unavailable")
def test_capture_debug_sample_rejects_foreign_owned_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    current_uid = os.getuid()
    monkeypatch.setattr(
        "chat_downloader.redaction.os.getuid",
        lambda: current_uid + 1,
    )
    dbg.set_log_level("debug")

    sample_path = red.capture_debug_sample("owner-probe", {"value": "sample"})

    assert sample_path is None


def test_capture_debug_sample_fails_closed_without_secure_directory_fd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    monkeypatch.setattr("chat_downloader.redaction.os.supports_dir_fd", set())
    dbg.set_log_level("debug")

    sample_path = red.capture_debug_sample("fallback-probe", {"value": "sample"})

    assert sample_path is None
    assert list(sample_dir.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="secure directory fds are POSIX-only")
def test_capture_debug_sample_rechecks_opened_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    original_fstat = os.fstat

    def insecure_directory_mode(descriptor: int) -> os.stat_result:
        entry = original_fstat(descriptor)
        if stat.S_ISDIR(entry.st_mode):
            values = list(entry)
            values[0] = (entry.st_mode & ~0o777) | 0o755
            return os.stat_result(values)
        return entry

    monkeypatch.setattr("chat_downloader.redaction.os.fstat", insecure_directory_mode)
    dbg.set_log_level("debug")

    sample_path = red.capture_debug_sample("directory-fd-probe", {"value": "sample"})

    assert sample_path is None


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission modes required")
def test_capture_debug_sample_closes_rejected_new_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    original_fstat = os.fstat

    def insecure_file_mode(descriptor: int) -> os.stat_result:
        entry = original_fstat(descriptor)
        if stat.S_ISREG(entry.st_mode):
            values = list(entry)
            values[0] = (entry.st_mode & ~0o777) | 0o644
            return os.stat_result(values)
        return entry

    monkeypatch.setattr("chat_downloader.redaction.os.fstat", insecure_file_mode)
    dbg.set_log_level("debug")

    sample_path = red.capture_debug_sample("file-fd-probe", {"value": "sample"})

    assert sample_path is None
    assert list(sample_dir.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="secure directory fds are POSIX-only")
def test_capture_debug_sample_removes_failed_write_and_retry_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingSampleFile:
        def __init__(self, descriptor: int) -> None:
            self.descriptor: int = descriptor

        def __enter__(self) -> FailingSampleFile:
            return self

        def __exit__(self, *_: object) -> None:
            os.close(self.descriptor)

        def write(self, _value: str) -> int:
            raise OSError("forced sample write failure")

    def failing_fdopen(
        descriptor: int,
        *_: object,
        **_kwargs: object,
    ) -> FailingSampleFile:
        return FailingSampleFile(descriptor)

    sample_dir = tmp_path / "samples"
    monkeypatch.setenv("CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES", "1")
    monkeypatch.setenv("CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR", str(sample_dir))
    dbg.set_log_level("debug")
    with monkeypatch.context() as failure_patch:
        failure_patch.setattr(
            "chat_downloader.redaction.os.fdopen",
            failing_fdopen,
        )
        failed_path = red.capture_debug_sample(
            "write-failure",
            {"value": "sample"},
        )

    assert failed_path is None
    assert list(sample_dir.iterdir()) == []

    retry_path = red.capture_debug_sample("write-failure", {"value": "sample"})

    assert retry_path is not None
    assert json.loads(Path(retry_path).read_text(encoding="utf-8")) == {
        "value": "sample"
    }
