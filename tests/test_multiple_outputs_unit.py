# SPDX-License-Identifier: MIT

"""Unit test for multiple output formats feature (no network required)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from chat_downloader.formatting.format import ItemFormatter
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat

if TYPE_CHECKING:
    import pathlib

# ---------------------------------------------------------------------------
# Multiple output writers
# ---------------------------------------------------------------------------


def test_multiple_writers_attached(tmp_path: pathlib.Path) -> None:
    def mock_generator():
        for i in range(3):
            yield {
                "message_id": f"msg_{i}",
                "message": f"Test message {i}",
                "author": {"name": f"User{i}"},
                "timestamp": 1000000 + i,
            }

    chat = Chat(chat=mock_generator(), title="Test Stream", id="test123")
    formatter = ItemFormatter()
    chat.set_formatter(lambda msg: formatter.format(msg, format_name="default"))

    jsonl_output = str(tmp_path / "test.jsonl")
    csv_output = str(tmp_path / "test.csv")
    txt_output = str(tmp_path / "test.txt")

    chat.attach_writer(
        ContinuousWriter(
            jsonl_output, sort_keys=True, overwrite=True, lazy_initialise=True
        )
    )
    chat.attach_writer(
        ContinuousWriter(
            csv_output, sort_keys=True, overwrite=True, lazy_initialise=True
        )
    )
    chat.attach_writer(
        ContinuousWriter(txt_output, overwrite=True, lazy_initialise=True)
    )

    assert len(chat._output_dispatcher.writers) == 3

    messages = list(chat)
    assert len(messages) == 3

    assert (tmp_path / "test.jsonl").exists()
    assert (tmp_path / "test.csv").exists()
    assert (tmp_path / "test.txt").exists()

    with open(jsonl_output, encoding="utf-8") as f:
        lines = f.readlines()
        assert len(lines) == 3
        assert json.loads(lines[0])["message"] == "Test message 0"

    with open(csv_output, encoding="utf-8") as f:
        csv_lines = f.readlines()
        assert len(csv_lines) >= 3


def test_single_writer_backwards_compatibility(tmp_path: pathlib.Path) -> None:
    chat = Chat(
        chat=iter([{"message": "Test", "author": {"name": "User"}}]),
        title="Test",
        id="test",
    )
    formatter = ItemFormatter()
    chat.set_formatter(lambda msg: formatter.format(msg, format_name="default"))

    jsonl_output = str(tmp_path / "single.jsonl")
    chat.attach_writer(
        ContinuousWriter(jsonl_output, overwrite=True, lazy_initialise=True)
    )

    assert len(chat._output_dispatcher.writers) == 1
    assert len(list(chat)) == 1
    assert (tmp_path / "single.jsonl").exists()


def test_no_writers_attached(tmp_path: pathlib.Path) -> None:
    chat = Chat(chat=iter([{"message": "Test"}]))
    formatter = ItemFormatter()
    chat.set_formatter(lambda msg: formatter.format(msg, format_name="default"))

    assert len(chat._output_dispatcher.writers) == 0
    assert len(list(chat)) == 1


def test_write_error_count_tracks_close_failures(tmp_path: pathlib.Path) -> None:
    """A writer that fails on close increments write_error_count."""
    from chat_downloader.output.continuous_write import ContinuousWriter

    chat = Chat(
        chat=iter([{"message": "x", "author": {"name": "u"}}]),
        title="Test",
        id="test",
    )
    formatter = ItemFormatter()
    chat.set_formatter(lambda msg: formatter.format(msg, format_name="default"))

    writer = ContinuousWriter(
        str(tmp_path / "fails.jsonl"), overwrite=True, lazy_initialise=True
    )
    # Patch close to raise
    original_close = writer.close

    def _failing_close() -> None:
        original_close()
        msg = "write error"
        raise OSError(msg)

    writer.close = _failing_close  # type: ignore[method-assign]
    chat.attach_writer(writer)
    list(chat)  # consume
    chat.close()

    assert chat.write_error_count > 0
