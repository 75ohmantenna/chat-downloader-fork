# SPDX-License-Identifier: MIT

"""Unit tests for extracted helpers in client_requests_continuation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_downloader.errors import (
    CaptchaChallengeRequired,
    RetriesExceeded,
)
from chat_downloader.utils.retry_utils import RetryPolicy


def _make_policy(max_attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts,
        retry_timeout=0,
        interruptible_retry=False,
    )


def _make_response(
    status_code: int = 200,
    json_body: object = None,
    text: str = "",
) -> SimpleNamespace:
    resp = SimpleNamespace(status_code=status_code, text=text)
    if json_body is not None:
        resp.json = lambda: json_body
    else:
        resp.json = dict
    return resp


# ---------------------------------------------------------------------------
# _handle_http_error tests
# ---------------------------------------------------------------------------


class TestHandleHttpError:
    def _import(self):
        from chat_downloader.sites.youtube.client_requests_errors import (
            _handle_http_error,
        )

        return _handle_http_error

    def test_captcha_in_error_message_raises(self) -> None:
        """HTTP 403 with captcha text raises CaptchaChallengeRequired."""
        _handle_http_error = self._import()
        resp = _make_response(
            status_code=403,
            json_body={"error": {"message": "captcha required"}},
        )
        policy = _make_policy(max_attempts=3)
        with pytest.raises(CaptchaChallengeRequired):
            _handle_http_error(resp, "https://example.com/", 1, 3, policy)

    def test_captcha_in_response_body_text_raises(self) -> None:
        """HTTP 200 with a challenge hint raises CaptchaChallengeRequired."""
        _handle_http_error = self._import()
        resp = _make_response(
            status_code=503,
            text="Please verify you are human to continue.",
        )
        policy = _make_policy(max_attempts=3)
        with pytest.raises(CaptchaChallengeRequired):
            _handle_http_error(resp, "https://example.com/", 1, 3, policy)

    def test_429_retryable_returns_true(self) -> None:
        """HTTP 429 within the retry budget returns True to continue."""
        _handle_http_error = self._import()
        resp = _make_response(status_code=429)
        policy = MagicMock(spec=RetryPolicy)
        policy.can_retry.return_value = True
        result = _handle_http_error(resp, "https://example.com/", 1, 3, policy)
        assert result is True
        policy.wait.assert_called_once()

    def test_500_retryable_raises_after_exhaustion(self) -> None:
        """HTTP 500 with no retries left raises RetriesExceeded."""
        _handle_http_error = self._import()
        resp = _make_response(status_code=500)
        policy = _make_policy(max_attempts=1)
        with pytest.raises(RetriesExceeded):
            # attempt_number == max_attempts means no retries left
            _handle_http_error(resp, "https://example.com/", 1, 1, policy)

    def test_400_terminal_logs_error_and_returns_false(self) -> None:
        """HTTP 400 is terminal: log and return False before JSON parse."""
        _handle_http_error = self._import()
        resp = _make_response(status_code=400)
        policy = _make_policy(max_attempts=3)
        result = _handle_http_error(resp, "https://example.com/", 1, 3, policy)
        assert result is False


# ---------------------------------------------------------------------------
# _handle_json_api_error tests
# ---------------------------------------------------------------------------


class TestHandleJsonApiError:
    def _import(self):
        from chat_downloader.sites.youtube.client_requests_errors import (
            _handle_json_api_error,
        )

        return _handle_json_api_error

    def test_captcha_in_json_error_message_raises(self) -> None:
        """JSON error with captcha text raises CaptchaChallengeRequired."""
        _handle_json_api_error = self._import()
        error = {"code": 403, "message": "recaptcha challenge required"}
        policy = _make_policy(max_attempts=3)
        with pytest.raises(CaptchaChallengeRequired):
            _handle_json_api_error(error, "https://example.com/", 1, 3, policy)

    def test_retryable_error_code_returns_true(self) -> None:
        """JSON error code 429 within retry budget returns True."""
        _handle_json_api_error = self._import()
        error = {"code": 429, "message": "Rate limit exceeded"}
        policy = MagicMock(spec=RetryPolicy)
        policy.can_retry.return_value = True
        result = _handle_json_api_error(error, "https://example.com/", 1, 3, policy)
        assert result is True
        policy.wait.assert_called_once()

    def test_unknown_error_string_returns_true(self) -> None:
        """A JSON 'unknown error' triggers the incomplete-retry path."""
        _handle_json_api_error = self._import()
        error = {"code": 0, "message": "Unknown error occurred"}
        policy = MagicMock(spec=RetryPolicy)
        policy.can_retry.return_value = True
        result = _handle_json_api_error(error, "https://example.com/", 1, 3, policy)
        assert result is True
        policy.wait.assert_called_once()


# ---------------------------------------------------------------------------
# _apply_retry_or_raise tests
# ---------------------------------------------------------------------------


class TestApplyRetryOrRaise:
    def _import(self):
        from chat_downloader.sites.youtube.client_requests_errors import (
            _apply_retry_or_raise,
        )

        return _apply_retry_or_raise

    def test_waits_and_returns_when_retry_is_allowed(self) -> None:
        """Returns normally (allows caller to continue) when retries remain."""
        _apply_retry_or_raise = self._import()
        policy = MagicMock(spec=RetryPolicy)
        policy.can_retry.return_value = True
        # Should not raise — just wait and return
        _apply_retry_or_raise(
            policy, 1, "https://example.com/", "some error", RetriesExceeded
        )
        policy.wait.assert_called_once_with(1, interruptible=False)

    def test_raises_exc_cls_when_retries_exhausted(self) -> None:
        """Raises the supplied exception class when no retries remain."""
        _apply_retry_or_raise = self._import()
        policy = _make_policy(max_attempts=1)
        with pytest.raises(RetriesExceeded, match="some error"):
            # attempt_number == max_attempts means can_retry returns False
            _apply_retry_or_raise(
                policy, 1, "https://example.com/", "some error", RetriesExceeded
            )

    def test_raised_message_includes_url(self) -> None:
        """The raised exception message embeds the endpoint URL."""
        _apply_retry_or_raise = self._import()
        policy = _make_policy(max_attempts=1)
        with pytest.raises(RetriesExceeded, match=r"my\.endpoint"):
            _apply_retry_or_raise(
                policy,
                1,
                "https://my.endpoint/chat",
                "timeout",
                RetriesExceeded,
            )


def test_yt_contains_challenge_text_non_string() -> None:
    from chat_downloader.sites.youtube.client_requests_errors import (
        _contains_challenge_text,
    )

    assert _contains_challenge_text(None) is False
    assert _contains_challenge_text(42) is False


def test_yt_is_retryable_status_non_int() -> None:
    from chat_downloader.sites.youtube.client_requests_errors import (
        _is_retryable_status,
    )

    assert _is_retryable_status(None) is False
    assert _is_retryable_status("403") is False


def test_extract_next_continuation_skips_empty_dict_entry() -> None:
    from chat_downloader.sites.youtube.continuations import (
        _extract_next_continuation,
    )

    # Empty dict → try_get_first_key returns None → continue
    result = _extract_next_continuation({"continuations": [{}]})
    assert result == (None, None, None, {})


def test_extract_next_continuation_skips_non_dict_value() -> None:
    from chat_downloader.sites.youtube.continuations import (
        _extract_next_continuation,
    )

    # Value is a string, not a dict → continue
    result = _extract_next_continuation({"continuations": [{"someKey": "not_a_dict"}]})
    assert result == (None, None, None, {})


def test_extract_timeout_ms_rejects_bool_payload() -> None:
    """``True``/``False`` are ``int`` subclasses but never valid timeouts."""
    from chat_downloader.sites.youtube.continuations import _extract_timeout_ms

    assert _extract_timeout_ms(True) is None
    assert _extract_timeout_ms(False) is None


def test_extract_timeout_ms_rejects_non_numeric_string() -> None:
    from chat_downloader.sites.youtube.continuations import _extract_timeout_ms

    assert _extract_timeout_ms("not-a-number") is None


def test_get_initial_info_raises_parsing_error_when_html_unparseable() -> None:
    """200 OK with HTML missing the initial-data blob → ``ParsingError``."""
    from chat_downloader.errors import ParsingError
    from chat_downloader.sites.youtube.client_requests_initial import (
        _get_initial_info,
    )

    response = SimpleNamespace(text="<html>nothing useful</html>", status_code=200)
    session_get = MagicMock(return_value=response)

    with pytest.raises(ParsingError, match="Unable to parse initial video data"):
        _get_initial_info(
            url="https://www.youtube.com/watch?v=abc",
            session_get=session_get,
            params=None,
            yt_initial_data_re=r"ytInitialData\s*=\s*({.*?});",
            yt_cfg_re=r"ytcfg\.set\(({.*?})\);",
            yt_initial_player_response_re=(r"ytInitialPlayerResponse\s*=\s*({.*?});"),
        )


def test_initial_contains_challenge_text_non_string() -> None:
    from chat_downloader.sites.youtube.client_requests_initial import (
        _contains_challenge_text,
    )

    assert _contains_challenge_text(None) is False
    assert _contains_challenge_text(42) is False
    assert _contains_challenge_text([]) is False
