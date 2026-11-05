# SPDX-License-Identifier: MIT

from __future__ import annotations

import threading

import pytest

from chat_downloader.utils.timed_generator import TimedGenerator, polling_sleep


@pytest.mark.parametrize("option", ["timeout", "inactivity_timeout"])
def test_deadline_cancels_provider_wait_and_finalizes_accounting(option):
    entered, closed = threading.Event(), threading.Event()
    calls = []

    def provider():
        try:
            entered.set()
            try:
                polling_sleep(60)
            except Exception:
                calls.append("ordinary-error-handler")
            calls.append("post-wait-request")
            yield "late"
        finally:
            closed.set()

    source = TimedGenerator(provider(), **{option: 0.1})
    try:
        assert entered.wait(1)
        assert list(source) == []
        assert closed.is_set()
        assert not source._worker.is_alive()
        assert source.deadline_prefetch_summary() == (0, True)
        assert calls == []
    finally:
        source.close()


def test_cancellation_is_isolated_between_timed_workers():
    entered = [threading.Event(), threading.Event()]
    closed = [threading.Event(), threading.Event()]

    def provider(index):
        try:
            entered[index].set()
            polling_sleep(60 if index == 0 else 0.1)
            yield index
        finally:
            closed[index].set()

    first = TimedGenerator(provider(0))
    second = TimedGenerator(provider(1))
    try:
        assert all(event.wait(1) for event in entered)
        first.close()
        assert closed[0].is_set()
        assert list(second) == [1]
        assert closed[1].is_set()
        polling_sleep(0.001)
    finally:
        first.close()
        second.close()


def test_youtube_poll_wait_cancellation_prevents_another_request(monkeypatch):
    from unittest.mock import Mock

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.youtube import continuation
    from tests.youtube_third_helpers import Downloader, http_response, response

    entered = threading.Event()
    original_sleep = polling_sleep

    def sleep(seconds):
        entered.set()
        original_sleep(seconds)

    post = Mock(
        return_value=http_response(
            payload=response(
                continuations=[
                    {
                        "timedContinuationData": {
                            "continuation": "next",
                            "timeoutMs": 8000,
                        }
                    }
                ]
            )
        )
    )
    downloader = Downloader()
    downloader._session_post = post
    monkeypatch.setattr(continuation, "_generate_headers", lambda *_: {})
    monkeypatch.setattr(continuation, "_generate_sapisidhash_header", lambda *_: None)
    monkeypatch.setattr(continuation, "polling_sleep", sleep)
    source = TimedGenerator(
        continuation._ContinuationLoop(
            downloader,
            {"status": "live", "continuation_info": {"Live chat": "initial"}},
            {"INNERTUBE_API_KEY": "fixture"},
            ChatRequest(message_groups=["all"]),
        ).run(),
        timeout=0.1,
    )
    try:
        assert entered.wait(1)
        assert list(source) == []
        assert post.call_count == 1
        assert source.deadline_prefetch_summary() == (0, True)
    finally:
        source.close()


@pytest.mark.parametrize("stop_reason", ["timeout", "inactivity_timeout", "close"])
def test_youtube_retry_cancellation_prevents_second_post_with_open_session(
    monkeypatch, stop_reason
):
    from unittest.mock import Mock

    from chat_downloader.models import ChatRequest
    from chat_downloader.sites.youtube.client_requests_continuation import (
        _get_continuation_info,
    )
    from chat_downloader.utils import retry_utils
    from tests.youtube_third_helpers import Downloader, http_response, response

    entered, release = threading.Event(), threading.Event()
    original_seconds = retry_utils.RetryPolicy.sleep_seconds

    def sleep_seconds(policy, attempt):
        seconds = original_seconds(policy, attempt)
        entered.set()
        assert release.wait(2)
        return seconds

    monkeypatch.setattr(retry_utils.RetryPolicy, "sleep_seconds", sleep_seconds)
    monkeypatch.setattr(retry_utils.time, "sleep", lambda _seconds: None)
    downloader = Downloader()
    downloader.session.close = Mock()
    post = Mock(
        side_effect=[
            http_response(503, payload={"error": {"message": "unavailable"}}),
            http_response(payload=response()),
        ]
    )
    downloader._session_post = post

    def provider():
        yield _get_continuation_info(
            "https://www.youtube.com/youtubei/v1/live_chat/get_live_chat",
            downloader._session_post,
            ChatRequest(max_attempts=2, retry_timeout=60),
        )

    options = {} if stop_reason == "close" else {stop_reason: 60}
    source = TimedGenerator(provider(), **options)
    try:
        assert entered.wait(1)
        if stop_reason == "close":
            source.close()
        else:
            if stop_reason == "timeout":
                source._timeout_deadline = 0
            else:
                source._inactivity_deadline = 0
            assert list(source) == []
            expected = "timeout" if stop_reason == "timeout" else "inactivity"
            assert source._finish_reason == expected
        release.set()
        source._worker.join(1)
        assert not source._worker.is_alive()
        assert post.call_count == 1
        downloader.session.close.assert_not_called()
    finally:
        release.set()
        source.close()


@pytest.mark.parametrize("seconds", [0, -1, 1])
def test_polling_sleep_checks_cancellation_even_without_positive_delay(seconds):
    from chat_downloader.utils.timed_generator import (
        _POLLING_STOP_EVENT,
        _PollingCancelled,
    )

    stopped = threading.Event()
    stopped.set()
    token = _POLLING_STOP_EVENT.set(stopped)
    try:
        with pytest.raises(_PollingCancelled):
            polling_sleep(seconds)
    finally:
        _POLLING_STOP_EVENT.reset(token)
