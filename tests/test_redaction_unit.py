# SPDX-License-Identifier: MIT

"""Unit tests for src/chat_downloader/redaction.py."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

import chat_downloader.debugging as dbg
import chat_downloader.redaction as red


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


def test_redacts_nested_sensitive_keys_in_sequences() -> None:
    assert red.sanitize_for_log(
        ({"authorization": "Bearer token"}, [{"cookie": "sid=abc"}])
    ) == ({"authorization": red.REDACTED}, [{"cookie": red.REDACTED}])


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
            },
        )
        path2 = red.capture_debug_sample(
            "Unknown continuation: heartbeat",
            {
                "authorization": "secret",
                "headers": {"Authorization": "Bearer secret"},
                "value": 7,
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
            "Captured debug sample: "
            f"path={path} "
            "suggested_fixture_site=youtube "
            "suggested_fixture_group=continuations "
            "suggested_fixture_name=youtube-unknown-continuation-heartbeat",
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
