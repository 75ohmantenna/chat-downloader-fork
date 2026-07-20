# SPDX-License-Identifier: MIT

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest

from chat_downloader.models import ChatRequest
from chat_downloader.runtime import chat_pipeline
from chat_downloader.sites.models import Chat

apply_message_limit = chat_pipeline._apply_message_limit
build_output_writer = chat_pipeline._build_output_writer
configure_chat = chat_pipeline.configure_chat
configure_formatter = chat_pipeline._configure_formatter
configure_output_writer = chat_pipeline._configure_output_writer
configure_timeouts = chat_pipeline._configure_timeouts


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
        "chat_downloader.runtime.chat_pipeline.time.monotonic",
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


class _FakeSite:
    """Minimal site exposing the live-format capability contract.

    The pipeline is provider-neutral: it asks the site whether a status is live
    and how to remap the format. This fake stands in for any site, so these
    tests assert *delegation* rather than YouTube-specific values (those live in
    the YouTube suite).
    """

    def __init__(
        self,
        *,
        live_statuses: frozenset[str] = frozenset(),
        overrides: dict[str, str] | None = None,
    ) -> None:
        self._live = live_statuses
        self._overrides = overrides or {}

    def is_live_status(self, status: str | None) -> bool:
        return status in self._live

    def resolve_live_format(self, format_name: str) -> str:
        return self._overrides.get(format_name, format_name)


def test_configure_formatter_applies_site_live_override_for_live_status(
    monkeypatch,
) -> None:
    formatter_calls = []

    class FakeFormatter:
        def __init__(self, format_file) -> None:
            self.format_file = format_file

        def format(self, message, format_name=None) -> str:
            formatter_calls.append((self.format_file, message, format_name))
            return "formatted"

    live_site = _FakeSite(
        live_statuses=frozenset({"live"}),
        overrides={"default": "live_default", "custom": "live_custom"},
    )
    default_chat = Chat(iter(()), status="live")
    default_chat.site = cast("Any", live_site)
    custom_chat = Chat(iter(()), status="live")
    custom_chat.site = cast("Any", live_site)

    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.ItemFormatter",
        FakeFormatter,
    )

    configure_formatter(default_chat, "formats/custom.txt", "default")
    configure_formatter(custom_chat, "formats/custom.txt", "custom")

    assert default_chat.format({"message_type": "text_message"}) == "formatted"
    assert custom_chat.format({"message_type": "text_message"}) == "formatted"
    assert formatter_calls == [
        ("formats/custom.txt", {"message_type": "text_message"}, "live_default"),
        ("formats/custom.txt", {"message_type": "text_message"}, "live_custom"),
    ]


def test_configure_formatter_keeps_format_when_status_not_live(
    monkeypatch,
) -> None:
    formatter_calls = []

    class FakeFormatter:
        def __init__(self, format_file) -> None:
            self.format_file = format_file

        def format(self, message, format_name=None) -> str:
            formatter_calls.append((self.format_file, message, format_name))
            return "formatted"

    live_site = _FakeSite(
        live_statuses=frozenset({"live"}),
        overrides={"default": "live_default"},
    )
    # Replay status: site declares override but the status is not live, so the
    # pipeline must not remap.
    replay_chat = Chat(iter(()), status="past")
    replay_chat.site = cast("Any", live_site)
    # A site with no overrides leaves the format untouched even when live.
    neutral_chat = Chat(iter(()), status="live")
    neutral_chat.site = cast("Any", _FakeSite(live_statuses=frozenset({"live"})))

    monkeypatch.setattr(
        "chat_downloader.runtime.chat_pipeline.ItemFormatter",
        FakeFormatter,
    )

    configure_formatter(replay_chat, "formats/custom.txt", "default")
    configure_formatter(neutral_chat, "formats/custom.txt", "24_hour")

    assert replay_chat.format({"message_type": "text_message"}) == "formatted"
    assert neutral_chat.format({"message_type": "text_message"}) == "formatted"
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
    aliased_path = f"{tmp_path}/./out.jsonl"
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=[output_path, aliased_path],
    )

    chat = Chat(status="live")
    writer_factory = MagicMock(return_value=MagicMock())

    configure_output_writer(chat, request, writer_factory=writer_factory)
    assert len(chat._output_dispatcher.writers) == 1


def test_configure_output_writer_alias_writes_each_item_once(tmp_path) -> None:
    output_path = tmp_path / "out.txt"
    output_path.write_text("existing\n", encoding="utf-8")
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=[str(output_path), f"{tmp_path}/./out.txt"],
        overwrite=False,
    )
    chat = Chat(iter([{"message_type": "text_message"}]), status="live")
    chat.set_formatter(lambda _item: "hello")

    configure_output_writer(chat, request)
    assert next(chat) == {"message_type": "text_message"}
    with pytest.raises(StopIteration):
        next(chat)

    assert output_path.read_text(encoding="utf-8") == "existing\nhello\n"


def test_configure_output_writer_rejects_json_output(tmp_path) -> None:
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        output=str(tmp_path / "out.json"),
    )
    chat = Chat(status="live")

    with pytest.raises(ValueError, match=r"Use a \.jsonl or \.txt output path"):
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


def test_configure_chat_composes_real_limit_formatter_and_writer(tmp_path) -> None:
    output = tmp_path / "chat.jsonl"
    chat = Chat(
        iter(
            [
                {"message_id": "one", "message_type": "text_message"},
                {"message_id": "two", "message_type": "text_message"},
            ]
        ),
        status="live",
        title="Example",
        id="abc",
    )
    request = ChatRequest(
        url="https://www.youtube.com/watch?v=abc",
        max_messages=1,
        output=str(output),
    )
    site = _FakeSite()

    configure_chat(chat, request, cast("Any", site))

    assert chat.site is site
    assert list(chat) == [{"message_id": "one", "message_type": "text_message"}]
    assert '"message_id": "one"' in output.read_text(encoding="utf-8")
    assert "two" not in output.read_text(encoding="utf-8")
