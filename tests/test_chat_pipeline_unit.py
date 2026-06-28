# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.runtime.chat_pipeline import (
    apply_message_limit,
    build_output_writer,
    configure_chat,
    configure_formatter,
    configure_output_writer,
    configure_timeouts,
)
from chat_downloader.sites.models import Chat


class _FakeWriter:
    def __init__(
        self,
        output_file: str,
        *,
        sort_keys: bool,
        overwrite: bool,
        lazy_initialise: bool,
    ) -> None:
        self.output_file = output_file
        self.sort_keys = sort_keys
        self.overwrite = overwrite
        self.lazy_initialise = lazy_initialise


def test_apply_message_limit_uses_islice_for_positive_integers() -> None:
    chat = Chat(iter(range(5)), title="Example")

    apply_message_limit(chat, 2)

    assert list(cast("Any", chat.chat)) == [0, 1]


def test_apply_message_limit_none_is_no_op() -> None:
    chat = Chat(iter(range(5)), title="Example")
    apply_message_limit(chat, None)
    assert list(cast("Any", chat.chat)) == [0, 1, 2, 3, 4]


def test_apply_message_limit_propagates_close_to_source() -> None:
    source = MagicMock()
    source.__next__.side_effect = [{"id": "1"}, {"id": "2"}]
    chat = Chat(source, title="Example")

    apply_message_limit(chat, 1)
    assert next(cast("Any", chat.chat)) == {"id": "1"}
    limited = cast("Any", chat.chat)
    chat.close()
    limited.close()

    source.close.assert_called_once()


def test_apply_message_limit_closes_source_when_iteration_raises() -> None:
    source = MagicMock()
    source.__next__.side_effect = KeyboardInterrupt
    chat = Chat(source, title="Example")
    apply_message_limit(chat, 1)

    with pytest.raises(KeyboardInterrupt):
        next(cast("Any", chat.chat))

    source.close.assert_called_once()


def test_configure_timeouts_wraps_chat_and_installs_callbacks(
    monkeypatch,
) -> None:
    callback_logs = []
    time_values = iter([100.0, 103.5])

    class FakeTimedGenerator:
        def __init__(self, generator, timeout, inactivity_timeout) -> None:
            self.source = generator
            self.timeout = timeout
            self.inactivity_timeout = inactivity_timeout
            self.on_timeout = None
            self.on_inactivity_timeout = None

    chat = SimpleNamespace(chat=iter([1, 2, 3]))

    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.TimedGenerator",
        FakeTimedGenerator,
    )
    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.time.time",
        lambda: next(time_values),
    )
    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.log",
        lambda level, message: callback_logs.append((level, str(message))),
    )

    configure_timeouts(chat, timeout=5, inactivity_timeout=7)

    assert isinstance(chat.chat, FakeTimedGenerator)
    assert chat.chat.timeout == 5
    assert chat.chat.inactivity_timeout == 7

    assert chat.chat.on_timeout is not None
    assert chat.chat.on_inactivity_timeout is not None
    chat.chat.on_timeout()
    chat.chat.on_inactivity_timeout()

    assert callback_logs == [
        ("debug", "Timeout occurred after 3.5 seconds."),
        ("debug", "Inactivity timeout occurred after 7 seconds."),
    ]


def test_configure_timeouts_noop_when_both_timeouts_are_none() -> None:
    source = iter([1, 2, 3])
    chat = SimpleNamespace(chat=source)

    configure_timeouts(chat, timeout=None, inactivity_timeout=None)

    assert chat.chat is source


def test_configure_timeouts_chat_none() -> None:
    chat = Chat()
    chat.chat = None
    configure_timeouts(chat, timeout=30.0, inactivity_timeout=None)
    assert chat.chat is None  # Returns early — no TimedGenerator wrapping


@pytest.mark.parametrize(
    ("timeout", "inactivity_timeout", "timeout_callback", "inactivity_callback"),
    [
        (None, 7, False, True),
        (5, None, True, False),
    ],
)
def test_configure_timeouts_installs_only_numeric_timeout_callbacks(
    monkeypatch,
    timeout,
    inactivity_timeout,
    timeout_callback,
    inactivity_callback,
) -> None:
    class FakeTimedGenerator:
        def __init__(self, generator, timeout, inactivity_timeout) -> None:
            self.source = generator
            self.timeout = timeout
            self.inactivity_timeout = inactivity_timeout
            self.on_timeout = None
            self.on_inactivity_timeout = None

    chat = SimpleNamespace(chat=iter([1, 2, 3]))
    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.TimedGenerator",
        FakeTimedGenerator,
    )

    configure_timeouts(chat, timeout=timeout, inactivity_timeout=inactivity_timeout)

    assert isinstance(chat.chat, FakeTimedGenerator)
    assert (chat.chat.on_timeout is not None) is timeout_callback
    assert (chat.chat.on_inactivity_timeout is not None) is inactivity_callback


def test_configure_formatter_installs_item_formatter_wrapper(
    monkeypatch,
) -> None:
    formatter_calls = []

    class FakeFormatter:
        def __init__(self, format_file) -> None:
            self.format_file = format_file

        def format(self, message, format_name=None) -> str:
            formatter_calls.append((self.format_file, message, format_name))
            return f"formatted:{message['message_type']}:{format_name}"

    installed: list = []
    chat = SimpleNamespace(set_formatter=installed.append)

    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.ItemFormatter",
        FakeFormatter,
    )

    configure_formatter(chat, "formats/custom.txt", "youtube")

    assert len(installed) == 1
    format_callable = installed[0]
    assert format_callable({"message_type": "text_message"}) == (
        "formatted:text_message:youtube"
    )
    assert formatter_calls == [
        ("formats/custom.txt", {"message_type": "text_message"}, "youtube"),
    ]


def test_configure_formatter_prefers_timestamp_variants_for_youtube_live(
    monkeypatch,
) -> None:
    formatter_calls = []

    class FakeFormatter:
        def __init__(self, format_file) -> None:
            self.format_file = format_file

        def format(self, message, format_name=None) -> str:
            formatter_calls.append((self.format_file, message, format_name))
            return "formatted"

    default_chat = Chat(iter(()), status="live")
    default_chat.site = SimpleNamespace(_NAME="youtube.com")
    site_default_chat = Chat(iter(()), status="live")
    site_default_chat.site = SimpleNamespace(_NAME="youtube.com")

    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.ItemFormatter",
        FakeFormatter,
    )

    configure_formatter(default_chat, "formats/custom.txt", "default")
    configure_formatter(site_default_chat, "formats/custom.txt", "youtube")

    assert default_chat.format({"message_type": "text_message"}) == "formatted"
    assert site_default_chat.format({"message_type": "text_message"}) == "formatted"
    assert formatter_calls == [
        (
            "formats/custom.txt",
            {"message_type": "text_message"},
            "youtube_live_default",
        ),
        (
            "formats/custom.txt",
            {"message_type": "text_message"},
            "youtube_live_default",
        ),
    ]


def test_configure_formatter_keeps_replay_and_non_youtube_formats_unchanged(
    monkeypatch,
) -> None:
    formatter_calls = []

    class FakeFormatter:
        def __init__(self, format_file) -> None:
            self.format_file = format_file

        def format(self, message, format_name=None) -> str:
            formatter_calls.append((self.format_file, message, format_name))
            return "formatted"

    replay_chat = Chat(iter(()), status="past")
    replay_chat.site = SimpleNamespace(_NAME="youtube.com")
    twitch_chat = Chat(iter(()), status="live")
    twitch_chat.site = SimpleNamespace(_NAME="twitch.tv")

    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.ItemFormatter",
        FakeFormatter,
    )

    configure_formatter(replay_chat, "formats/custom.txt", "default")
    configure_formatter(twitch_chat, "formats/custom.txt", "24_hour")

    assert replay_chat.format({"message_type": "text_message"}) == "formatted"
    assert twitch_chat.format({"message_type": "text_message"}) == "formatted"
    assert formatter_calls == [
        ("formats/custom.txt", {"message_type": "text_message"}, "default"),
        ("formats/custom.txt", {"message_type": "text_message"}, "24_hour"),
    ]


def test_configure_output_writer_supports_multiple_outputs() -> None:
    attached = []

    chat = SimpleNamespace(
        status="live",
        attach_writer=attached.append,
    )
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=["first.jsonl", "second.txt"],
        sort_keys=False,
        overwrite=False,
    )

    configure_output_writer(chat, request, writer_factory=_FakeWriter)

    assert [writer.output_file for writer in attached] == [
        "first.jsonl",
        "second.txt",
    ]
    assert attached[0].sort_keys is False
    assert attached[0].overwrite is False
    assert attached[0].lazy_initialise is True


def test_configure_output_writer_deduplicates_duplicate_paths(tmp_path) -> None:
    output_path = str(tmp_path / "out.jsonl")
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=[output_path, output_path],
    )

    chat = Chat(status="live")
    writer_factory = MagicMock(return_value=MagicMock())

    configure_output_writer(chat, request, writer_factory=writer_factory)
    assert len(chat._output_dispatcher.writers) == 1


def test_configure_output_writer_rejects_json_output(tmp_path) -> None:
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=str(tmp_path / "out.json"),
    )
    chat = Chat(status="live")

    with pytest.raises(ValueError, match=r"Use a \.jsonl output path"):
        configure_output_writer(chat, request)


def test_build_output_writer_copies_request_output_settings() -> None:
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        sort_keys=False,
        overwrite=False,
    )

    writer = build_output_writer("chat.jsonl", request, writer_factory=_FakeWriter)

    assert writer.output_file == "chat.jsonl"
    assert writer.sort_keys is False
    assert writer.overwrite is False
    assert writer.lazy_initialise is True


def test_configure_chat_applies_pipeline_helpers(monkeypatch) -> None:
    calls: list[tuple[Any, ...]] = []
    chat = SimpleNamespace(chat=iter(()), site=None)
    request = ChatRequest(url="https://www.youtube.com/watch?v=abc")
    site = object()

    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.apply_message_limit",
        lambda current_chat, max_messages: calls.append(
            ("limit", current_chat, max_messages),
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.configure_timeouts",
        lambda current_chat, timeout, inactivity_timeout: calls.append(
            ("timeouts", current_chat, timeout, inactivity_timeout),
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.configure_formatter",
        lambda current_chat, format_file, format_name: calls.append(
            ("formatter", current_chat, format_file, format_name),
        ),
    )
    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.configure_output_writer",
        lambda current_chat, current_request: calls.append(
            ("writer", current_chat, current_request),
        ),
    )

    configure_chat(chat, request, site)

    assert calls == [
        ("limit", chat, request.max_messages),
        ("timeouts", chat, request.timeout, request.inactivity_timeout),
        ("formatter", chat, request.format_file, request.format),
        ("writer", chat, request),
    ]
    assert chat.site is site
