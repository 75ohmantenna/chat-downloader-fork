# SPDX-License-Identifier: MIT

"""Unit tests for live-stream JSONL output behavior."""

from unittest.mock import MagicMock, patch

from chat_downloader.models import ChatRequest
from chat_downloader.runtime.chat_pipeline import (
    configure_output_writer,
    is_live_stream,
    maybe_upgrade_to_jsonl,
)


class TestMaybeUpgradeToJsonl:
    def test_live_json_upgraded(self) -> None:
        result = maybe_upgrade_to_jsonl("chat.json", is_live=True)
        assert result == "chat.jsonl"

    def test_live_json_uppercase_ext_upgraded(self) -> None:
        result = maybe_upgrade_to_jsonl("chat.JSON", is_live=True)
        assert result == "chat.jsonl"

    def test_live_json_mixed_case_upgraded(self) -> None:
        result = maybe_upgrade_to_jsonl("chat.Json", is_live=True)
        assert result == "chat.jsonl"

    def test_vod_json_unchanged(self) -> None:
        result = maybe_upgrade_to_jsonl("chat.json", is_live=False)
        assert result == "chat.json"

    def test_live_jsonl_unchanged(self) -> None:
        result = maybe_upgrade_to_jsonl("chat.jsonl", is_live=True)
        assert result == "chat.jsonl"

    def test_live_csv_unchanged(self) -> None:
        result = maybe_upgrade_to_jsonl("chat.csv", is_live=True)
        assert result == "chat.csv"

    def test_live_txt_unchanged(self) -> None:
        result = maybe_upgrade_to_jsonl("chat.txt", is_live=True)
        assert result == "chat.txt"

    def test_live_no_extension_unchanged(self) -> None:
        result = maybe_upgrade_to_jsonl("chatlog", is_live=True)
        assert result == "chatlog"

    def test_live_path_with_directory(self) -> None:
        result = maybe_upgrade_to_jsonl(
            "/tmp/captures/stream.json", is_live=True
        )
        assert result == "/tmp/captures/stream.jsonl"

    def test_live_json_kept_when_format_explicitly_json(self) -> None:
        result = maybe_upgrade_to_jsonl(
            "chat.json", is_live=True, output_format="json"
        )
        assert result == "chat.json"

    def test_upgrade_emits_warning(self, caplog) -> None:
        import logging

        with caplog.at_level(logging.WARNING):
            maybe_upgrade_to_jsonl("chat.json", is_live=True)
        assert any("jsonl" in r.message.lower() for r in caplog.records)


class TestIsLiveStream:
    def _chat(self, status):
        c = MagicMock()
        c.status = status
        return c

    def test_status_live(self) -> None:
        assert is_live_stream(self._chat("live")) is True

    def test_status_post_live(self) -> None:
        assert is_live_stream(self._chat("post_live")) is True

    def test_status_was_live(self) -> None:
        assert is_live_stream(self._chat("was_live")) is False

    def test_status_not_live(self) -> None:
        assert is_live_stream(self._chat("not_live")) is False

    def test_status_past(self) -> None:
        assert is_live_stream(self._chat("past")) is False

    def test_status_none(self) -> None:
        assert is_live_stream(self._chat(None)) is False

    def test_no_status_attribute(self) -> None:
        c = object()
        assert is_live_stream(c) is False


class TestConfigureOutputWriterLive:
    """Verify that configure_output_writer uses the upgraded filename."""

    def _make_chat(self, status):
        chat = MagicMock()
        chat.status = status
        chat.attach_writer = MagicMock()
        return chat

    def _params(self, output):
        return {
            "output": output,
            "indent": 4,
            "sort_keys": True,
            "overwrite": True,
        }

    def test_live_json_writer_gets_jsonl_filename(self) -> None:
        chat = self._make_chat("live")
        request = ChatRequest.from_kwargs(**self._params("stream.json"))

        with patch(
            "chat_downloader.runtime.chat_pipeline.ContinuousWriter",
        ) as MockWriter:
            configure_output_writer(chat, request, writer_factory=MockWriter)

        MockWriter.assert_called_once()
        called_filename = MockWriter.call_args[0][0]
        assert called_filename == "stream.jsonl"

    def test_vod_json_writer_keeps_json_filename(self) -> None:
        chat = self._make_chat("was_live")
        request = ChatRequest.from_kwargs(**self._params("stream.json"))

        with patch(
            "chat_downloader.runtime.chat_pipeline.ContinuousWriter",
        ) as MockWriter:
            configure_output_writer(chat, request, writer_factory=MockWriter)

        called_filename = MockWriter.call_args[0][0]
        assert called_filename == "stream.json"

    def test_live_jsonl_writer_keeps_jsonl_filename(self) -> None:
        chat = self._make_chat("live")
        request = ChatRequest.from_kwargs(**self._params("stream.jsonl"))

        with patch(
            "chat_downloader.runtime.chat_pipeline.ContinuousWriter",
        ) as MockWriter:
            configure_output_writer(chat, request, writer_factory=MockWriter)

        called_filename = MockWriter.call_args[0][0]
        assert called_filename == "stream.jsonl"

    def test_live_json_writer_keeps_json_filename_when_format_is_json(
        self,
    ) -> None:
        chat = self._make_chat("live")
        params = self._params("stream.json")
        params["format"] = "json"
        request = ChatRequest.from_kwargs(**params)

        with patch(
            "chat_downloader.runtime.chat_pipeline.ContinuousWriter",
        ) as MockWriter:
            configure_output_writer(chat, request, writer_factory=MockWriter)

        called_filename = MockWriter.call_args[0][0]
        assert called_filename == "stream.json"

    def test_no_output_attaches_no_writer(self) -> None:
        chat = self._make_chat("live")

        configure_output_writer(
            chat, ChatRequest.from_kwargs(**self._params(None))
        )
        chat.attach_writer.assert_not_called()

    def test_multiple_outputs_all_upgraded(self) -> None:
        chat = self._make_chat("live")
        request = ChatRequest.from_kwargs(
            **self._params(["a.json", "b.json", "c.csv"])
        )

        with patch(
            "chat_downloader.runtime.chat_pipeline.ContinuousWriter",
        ) as MockWriter:
            configure_output_writer(chat, request, writer_factory=MockWriter)

        assert MockWriter.call_count == 3
        filenames = [call[0][0] for call in MockWriter.call_args_list]
        assert filenames == ["a.jsonl", "b.jsonl", "c.csv"]
