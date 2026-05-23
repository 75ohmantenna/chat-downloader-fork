# SPDX-License-Identifier: MIT

"""Guards on the YouTube continuation loop: no-progress + bounded fallbacks."""

from types import SimpleNamespace

import pytest

from chat_downloader.errors import (
    IncompleteContinuationError,
    NoContinuation,
)
from chat_downloader.models import ChatRequest
from chat_downloader.sites.youtube.chat_streams_runtime_iteration import (
    _get_chat_messages,
)
from chat_downloader.sites.youtube.continuation_loop_state import (
    ContinuationLoopState,
)


@pytest.fixture(autouse=True)
def _disable_polling_sleep(monkeypatch) -> None:
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.polling_sleep",
        lambda _seconds: None,
    )


class _DummySession:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


class _DummyDownloader:
    def __init__(self) -> None:
        self.session = _DummySession()
        self._session_post = object()
        self._request_profile = "youtube_web"
        self._auto_profile_fallback = True

    def check_for_invalid_types(self, *_a, **_kw) -> None:
        pass

    def update_session_headers(self, headers) -> None:
        self.session.headers.update(headers)

    def apply_request_profile(self, profile_name: str) -> bool:
        self._request_profile = profile_name
        return True


def _stub_loop_dependencies(
    monkeypatch,
    *,
    yt_info: dict,
    advance_returns: bool = False,
) -> None:
    """Wire enough fakes to drive _get_chat_messages without real HTTP."""
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._build_chat_context",
        lambda *_a, **_kw: SimpleNamespace(
            continuation_url="https://example.test/c",
            innertube_context={"client": {}},
            msg_filter=None,
            time_filter=None,
            loop_state=ContinuationLoopState(continuation="token"),
            live_start_time_ms=0,
            is_replay=False,
            offset=None,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_a, **_kw: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_a, **_kw: yt_info,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._handle_continuation_response",
        lambda *_a, **_kw: None,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._advance_continuation_loop",
        lambda *_a, **_kw: advance_returns,
    )


def test_continuation_loop_raises_after_repeated_empty_polls_with_stale_token(
    monkeypatch,
) -> None:
    """Empty actions + token stays the same → NoContinuation after 5 polls."""
    empty_info = {
        "continuationContents": {"liveChatContinuation": {"actions": []}},
    }
    _stub_loop_dependencies(monkeypatch, yt_info=empty_info)

    with pytest.raises(NoContinuation, match="No progress"):
        list(
            _get_chat_messages(
                _DummyDownloader(),
                {
                    "continuation_info": {"Live chat": "token"},
                    "status": "live",
                },
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="live",
                    message_groups=["messages"],
                ),
            )
        )


def test_continuation_loop_bounded_profile_fallbacks(monkeypatch) -> None:
    """If IncompleteContinuationError fires forever, give up after the cap."""
    fallback_calls: list[int] = []

    def fake_fallback(_downloader) -> bool:
        fallback_calls.append(1)
        return True

    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._build_chat_context",
        lambda *_a, **_kw: SimpleNamespace(
            continuation_url="https://example.test/c",
            innertube_context={"client": {}},
            msg_filter=None,
            time_filter=None,
            loop_state=ContinuationLoopState(continuation="token"),
            live_start_time_ms=0,
            is_replay=False,
            offset=None,
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration.build_continuation_params",
        lambda *_a, **_kw: {"continuation": "token"},
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._get_continuation_info",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            IncompleteContinuationError("repeated")
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._attempt_profile_fallback",
        fake_fallback,
    )
    monkeypatch.setattr(
        "chat_downloader.sites.youtube.chat_streams_runtime_iteration._profiled_innertube_context",
        lambda *_a, **_kw: {"client": {}},
    )

    with pytest.raises(IncompleteContinuationError, match="repeated"):
        list(
            _get_chat_messages(
                _DummyDownloader(),
                {
                    "continuation_info": {"Live chat": "token"},
                    "status": "live",
                },
                {"INNERTUBE_API_KEY": "key"},
                ChatRequest(
                    url="https://www.youtube.com/watch?v=abc",
                    chat_type="live",
                    message_groups=["messages"],
                ),
            )
        )

    # Three fallbacks were attempted (cap), then the fourth attempt
    # surfaced the error without another fallback call.
    assert len(fallback_calls) == 3
