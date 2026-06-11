# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any, NoReturn
from unittest.mock import MagicMock

import pytest
from requests.exceptions import (
    ConnectionError,  # noqa: A004 — intentional: requests.ConnectionError shadows builtin; test needs the requests type
    RequestException,
)

from chat_downloader.debugging import (
    TestingException as RuntimeTestingException,
)
from chat_downloader.errors import (
    ChatGeneratorError,
    ParsingError,
    SiteNotSupported,
)
from chat_downloader.runtime.runner import (
    SITE_CHANGE_ERROR_HINT,
    _classify_run_error,
    _finalize_run,
    create_message_callback,
    execute_run,
)


class _FakeChat:
    def __iter__(self):
        return iter(())

    def print_formatted(self, _msg) -> None: ...

    def close(self) -> None: ...


class _FakeDownloader:
    """Minimal downloader stub; get_chat returns _FakeChat by default."""

    _last: _FakeDownloader | None = None

    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.chat_kwargs: dict[str, Any] | None = None
        self.closed = False
        _FakeDownloader._last = self

    def get_chat(self, **kwargs) -> _FakeChat:
        self.chat_kwargs = kwargs
        return _FakeChat()

    def close(self) -> None:
        self.closed = True


def _make_error_downloader(error_to_raise: BaseException) -> type:
    """Return a fresh class whose get_chat() raises error_to_raise."""

    class _ErrorDownloader:
        _last: _ErrorDownloader | None = None

        def __init__(self, **_kwargs) -> None:
            self.closed = False
            _ErrorDownloader._last = self

        def get_chat(self, **_kwargs) -> NoReturn:
            raise error_to_raise

        def close(self) -> None:
            self.closed = True

    return _ErrorDownloader


@pytest.mark.parametrize(
    ("exc", "expected_fragment"),
    [
        (ChatGeneratorError("gen"), SITE_CHANGE_ERROR_HINT),
        (ParsingError("parse"), SITE_CHANGE_ERROR_HINT),
        (RuntimeTestingException("test"), SITE_CHANGE_ERROR_HINT),
        (ConnectionError("offline"), "internet connection"),
        (SiteNotSupported("no site"), "no site"),
        (RequestException("timeout"), "timeout"),
    ],
)
def test_classify_run_error_returns_correct_message(
    exc: Exception, expected_fragment: str
) -> None:
    msg = _classify_run_error(exc)
    assert expected_fragment in msg


@pytest.mark.parametrize(
    ("error_to_raise", "expected_result_fragment", "expected_interrupted"),
    [
        (ChatGeneratorError("gen"), SITE_CHANGE_ERROR_HINT, False),
        (ParsingError("parse"), SITE_CHANGE_ERROR_HINT, False),
        (RuntimeTestingException("test"), SITE_CHANGE_ERROR_HINT, False),
        (ConnectionError("offline"), "internet connection", False),
        (SiteNotSupported("no site"), "no site", False),
        (RequestException("timeout"), "timeout", False),
        (KeyboardInterrupt(), "Keyboard Interrupt", True),
    ],
)
def test_execute_run_result_fields_per_exception_type(
    error_to_raise: BaseException,
    expected_result_fragment: str,
    expected_interrupted: bool,
) -> None:
    result = execute_run(_make_error_downloader(error_to_raise))
    assert result.success is False
    assert result.error_message is not None
    assert expected_result_fragment in result.error_message
    assert result.interrupted is expected_interrupted


def test_finalize_run_suppresses_cleanup_errors_on_primary_error(
    monkeypatch,
) -> None:
    logged = []
    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, message: logged.append((level, str(message))),
    )

    class _BadChat:
        def close(self) -> None:
            raise RuntimeError("chat broken")

    class _BadDownloader:
        def close(self) -> None:
            raise RuntimeError("downloader broken")

    _finalize_run(_BadChat(), _BadDownloader(), primary_error=True)
    assert ("warning", "Error finalizing chat output: chat broken") in logged
    assert (
        "warning",
        "Error closing downloader session(s): downloader broken",
    ) in logged


def test_finalize_run_reraises_cleanup_errors_without_primary_error() -> None:
    class _BadChat:
        def close(self) -> None:
            raise RuntimeError("chat broken")

    with pytest.raises(RuntimeError, match="chat broken"):
        _finalize_run(_BadChat(), None, primary_error=False)


def test_create_message_callback_quiet_returns_noop() -> None:
    chat = MagicMock()
    callback = create_message_callback(True, chat)

    callback({"message_type": "text_message"})
    chat.print_formatted.assert_not_called()


def test_create_message_callback_deduplicates_superchat_stdout() -> None:
    chat = MagicMock()
    callback = create_message_callback(False, chat)

    callback({"message_type": "paid_message", "message_id": "abc"})
    callback({"message_type": "ticker_paid_message_item", "message_id": "abc"})
    callback({"message_type": "text_message", "message_id": "abc"})

    assert chat.print_formatted.call_count == 2


def test_create_message_callback_deduplicates_with_bounded_cache() -> None:
    chat = MagicMock()
    callback = create_message_callback(False, chat, max_seen_message_ids=1)

    callback({"message_type": "paid_message", "message_id": "one"})
    callback({"message_type": "ticker_paid_message_item", "message_id": "one"})
    callback({"message_type": "membership_item", "message_id": "two"})
    callback({"message_type": "ticker_sponsor_item", "message_id": "two"})
    callback({"message_type": "paid_message", "message_id": "one"})

    assert chat.print_formatted.call_count == 3


def test_create_message_callback_uses_default_cache_when_limit_disabled() -> (
    None
):
    chat = MagicMock()
    callback = create_message_callback(False, chat, max_seen_message_ids=0)

    callback({"message_type": "paid_message", "message_id": "dup"})
    callback({"message_type": "text_message", "message_id": "dup"})
    callback({"message_type": "paid_message", "message_id": "dup"})

    assert chat.print_formatted.call_count == 2


def test_execute_run_processes_messages_and_closes_downloader() -> None:
    seen_messages: list[dict[str, str]] = []

    class FakeChat:
        def __iter__(self):
            yield {"message_type": "text_message", "message_id": "1"}

        def print_formatted(self, message) -> None:
            seen_messages.append(message)

    class FakeDownloader:
        instance = None

        def __init__(self, **kwargs) -> None:
            self.init_kwargs = kwargs
            self.closed = False
            self.chat_kwargs = None
            FakeDownloader.instance = self

        def get_chat(self, **kwargs):
            self.chat_kwargs = kwargs
            return FakeChat()

        def close(self) -> None:
            self.closed = True

    execute_run(
        FakeDownloader,
        url="https://www.youtube.com/watch?v=abc",
        max_messages=1,
    )

    assert FakeDownloader.instance is not None
    assert FakeDownloader.instance.init_kwargs == {}
    assert FakeDownloader.instance.chat_kwargs == {
        "url": "https://www.youtube.com/watch?v=abc",
        "max_messages": 1,
    }
    assert FakeDownloader.instance.closed is True
    assert seen_messages == [
        {"message_type": "text_message", "message_id": "1"}
    ]


def test_execute_run_passes_dedup_cache_size_to_message_callback() -> None:
    captured = {}

    def fake_create_message_callback(quiet, chat, *, max_seen_message_ids):
        captured["quiet"] = quiet
        captured["chat"] = chat
        captured["max_seen_message_ids"] = max_seen_message_ids
        return lambda _message: None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "chat_downloader.runtime.runner.create_message_callback",
        fake_create_message_callback,
    )
    try:
        execute_run(
            _FakeDownloader,
            quiet=False,
            max_seen_message_ids=123,
        )
    finally:
        monkeypatch.undo()

    assert captured["quiet"] is False
    assert isinstance(captured["chat"], _FakeChat)
    assert captured["max_seen_message_ids"] == 123


def test_execute_run_applies_typed_run_debug_controls(monkeypatch) -> None:
    captured_modes: list[dict[str, bool]] = []

    monkeypatch.setattr(
        "chat_downloader.runtime.runner.setup_testing_mode",
        lambda kwargs: captured_modes.append(dict(kwargs)),
    )

    execute_run(
        _FakeDownloader,
        exit_on_debug=True,
        pause_on_debug=False,
    )

    assert captured_modes == [{"exit_on_debug": True, "pause_on_debug": False}]


def test_execute_run_closes_chat_when_iteration_is_interrupted() -> None:
    class FakeChat:
        def __init__(self) -> None:
            self.closed = False
            self.calls = 0

        def __iter__(self):
            return self

        def __next__(self):
            if self.calls == 0:
                self.calls += 1
                return {"message_type": "text_message", "message_id": "1"}
            raise KeyboardInterrupt

        def print_formatted(self, _message) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    class FakeDownloader:
        instance = None

        def __init__(self, **kwargs) -> None:
            self.closed = False
            self.chat = FakeChat()
            FakeDownloader.instance = self

        def get_chat(self, **kwargs):
            return self.chat

        def close(self) -> None:
            self.closed = True

    execute_run(FakeDownloader)

    assert FakeDownloader.instance.chat.closed is True
    assert FakeDownloader.instance.closed is True


@pytest.mark.parametrize(
    ("error_to_raise", "expected_fragment"),
    [
        (SiteNotSupported("unsupported"), "unsupported"),
        (ConnectionError("offline"), "internet connection"),
        (RequestException("bad response"), "bad response"),
    ],
)
def test_execute_run_logs_expected_error_paths(
    monkeypatch,
    error_to_raise,
    expected_fragment,
) -> None:
    logged = []
    FakeDownloader = _make_error_downloader(error_to_raise)

    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, message: logged.append((level, str(message))),
    )

    execute_run(
        FakeDownloader,
        url="https://www.youtube.com/watch?v=abc",
    )

    assert logged
    assert logged[0][0] == "error"
    assert expected_fragment in logged[0][1]
    assert FakeDownloader._last is not None
    assert FakeDownloader._last.closed is True


@pytest.mark.parametrize(
    "error_to_raise",
    [
        ChatGeneratorError("generator"),
        ParsingError("parse"),
        RuntimeTestingException("testing"),
    ],
)
def test_execute_run_logs_error_message_for_generator_and_testing_errors(
    monkeypatch,
    error_to_raise,
) -> None:
    logged = []
    FakeDownloader = _make_error_downloader(error_to_raise)

    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, message: logged.append((level, str(message))),
    )

    execute_run(
        FakeDownloader,
        url="https://www.youtube.com/watch?v=abc",
    )

    assert logged == [
        (
            "error",
            f"{error_to_raise}. This usually means the site response "
            "changed. Re-run with --logging debug for details.",
        ),
    ]


def test_execute_run_logs_and_continues_when_chat_close_fails(
    monkeypatch,
) -> None:
    logged = []

    class _OSErrorChat(_FakeChat):
        def close(self) -> NoReturn:
            raise OSError("close failed")

    class _TrackingDownloader(_FakeDownloader):
        def get_chat(self, **kwargs):
            return _OSErrorChat()

    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, message: logged.append((level, str(message))),
    )

    execute_run(_TrackingDownloader)

    assert ("warning", "Error finalizing chat output: close failed") in logged
    assert _TrackingDownloader._last.closed is True


def test_execute_run_does_not_swallow_non_io_chat_close_errors() -> None:
    class _RuntimeErrorChat(_FakeChat):
        def close(self) -> NoReturn:
            raise RuntimeError("programmer bug")

    class _Downloader(_FakeDownloader):
        def get_chat(self, **kwargs):
            return _RuntimeErrorChat()

    with pytest.raises(RuntimeError, match="programmer bug"):
        execute_run(_Downloader)


def test_execute_run_logs_cleanup_errors_when_primary_error_occurs(
    monkeypatch,
) -> None:
    logged = []

    class FakeChat:
        def __iter__(self):
            return self

        def __next__(self):
            raise ChatGeneratorError("generator failed")

        def print_formatted(self, _message) -> None:
            pass

        def close(self) -> NoReturn:
            msg = "chat cleanup failed"
            raise RuntimeError(msg)

    class FakeDownloader:
        def __init__(self, **_kwargs) -> None:
            self.closed = False

        def get_chat(self, **_kwargs):
            return FakeChat()

        def close(self) -> None:
            raise RuntimeError("downloader cleanup failed")

    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, message: logged.append((level, str(message))),
    )

    result = execute_run(FakeDownloader)

    assert result.success is False
    assert result.error_message == (
        "generator failed. This usually means the site response changed. "
        "Re-run with --logging debug for details."
    )
    assert (
        "warning",
        "Error finalizing chat output: chat cleanup failed",
    ) in logged
    assert (
        "warning",
        "Error closing downloader session(s): downloader cleanup failed",
    ) in logged
    assert any(
        entry[0] == "error" and "generator failed" in entry[1]
        for entry in logged
    )


def test_execute_run_raises_downloader_close_error_when_no_primary_error(
    monkeypatch,
) -> None:
    class _BadCloseDownloader(_FakeDownloader):
        def close(self) -> NoReturn:
            raise RuntimeError("downloader cleanup failed")

    with pytest.raises(RuntimeError, match="downloader cleanup failed"):
        execute_run(_BadCloseDownloader)


def test_execute_run_logs_keyboard_interrupt_without_propagation(
    monkeypatch,
) -> None:
    logged = []
    FakeDownloader = _make_error_downloader(KeyboardInterrupt())

    monkeypatch.setattr(
        "chat_downloader.runtime.runner.log",
        lambda level, message: logged.append((level, str(message))),
    )

    execute_run(
        FakeDownloader,
        url="https://www.youtube.com/watch?v=abc",
    )

    assert logged == [("error", "Keyboard Interrupt")]
    assert FakeDownloader._last is not None
    assert FakeDownloader._last.closed is True


def test_execute_run_propagates_keyboard_interrupt_when_requested() -> None:
    FakeDownloader = _make_error_downloader(KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        execute_run(
            FakeDownloader,
            propagate_interrupt=True,
            url="https://www.youtube.com/watch?v=abc",
        )

    assert FakeDownloader._last is not None
    assert FakeDownloader._last.closed is True


@pytest.mark.parametrize(
    ("kwargs", "expected_mode"),
    [
        ({"exit_on_debug": True}, "EXIT_ON_DEBUG"),
        ({"pause_on_debug": True}, "PAUSE_ON_DEBUG"),
        ({}, "NONE"),
    ],
)
def test_setup_testing_mode_sets_expected_mode(
    monkeypatch, kwargs, expected_mode
) -> None:
    seen_modes = []

    monkeypatch.setattr(
        "chat_downloader.runtime.testing.set_testing_mode",
        lambda mode: seen_modes.append(mode.name),
    )

    from chat_downloader.runtime.testing import setup_testing_mode

    setup_testing_mode(kwargs)

    assert seen_modes == [expected_mode]


def test_setup_testing_mode_resets_to_none_when_no_flags(monkeypatch) -> None:
    """setup_testing_mode must reset to NONE when neither flag is set."""
    seen_modes = []

    monkeypatch.setattr(
        "chat_downloader.runtime.testing.set_testing_mode",
        lambda mode: seen_modes.append(mode.name),
    )

    from chat_downloader.runtime.testing import setup_testing_mode

    setup_testing_mode({"exit_on_debug": True})
    setup_testing_mode({})

    assert seen_modes == ["EXIT_ON_DEBUG", "NONE"]
