"""Verified captures and checkpoints share resolved output identities."""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites.models import Chat
from tests.test_capture_checkpoint_unit import Downloader, parameters


@pytest.mark.parametrize("kind", ["repeat", "relative", "template"])
def test_verified_capture_deduplicates_effective_output_pair(tmp_path, kind):
    params = parameters(tmp_path)
    params.pop("resume")
    raw, txt = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    alias = str(raw)
    if kind == "relative":
        alias = f"{tmp_path}/./chat.jsonl"
    elif kind == "template":
        raw = tmp_path / "video.jsonl"
        params["output"][0] = str(raw)
        alias = str(tmp_path / "{id}.jsonl")
    params["output"] += [alias, str(txt)]
    path = tmp_path / "verified.json"
    result = execute_run(Downloader, run_manifest=str(path), **params)
    assert result.success
    assert result.parity_status == "passed"
    assert len(raw.read_text().splitlines()) == 4
    assert len(txt.read_text().splitlines()) == 4
    report = json.loads(path.read_text())
    assert len(report["outputs"]) == 2
    assert all(item["records_written"] == 4 for item in report["outputs"])


def test_unresolved_templates_still_reject_extra_targets_before_writing(tmp_path):
    params = parameters(tmp_path)
    params.pop("resume")
    raw = tmp_path / "chat.jsonl"
    raw.write_text("preserve\n")
    params["output"].append(str(tmp_path / "{id}.jsonl"))
    result = execute_run(Downloader, **params)
    assert not result.success
    assert raw.read_text() == "preserve\n"
    assert not (tmp_path / "video.jsonl").exists()


def test_resumed_verification_deduplicates_outputs_before_checkpoint_identity(tmp_path):
    params = parameters(tmp_path)
    params["output"] *= 2
    assert execute_run(Downloader, max_messages=2, **params).success
    result = execute_run(Downloader, **params)
    assert result.success
    assert result.message_count == 2
    assert result.parity_status == "passed"
    assert len((tmp_path / "chat.jsonl").read_text().splitlines()) == 4


def test_unknown_lazy_metadata_does_not_hide_extra_output_destinations(tmp_path):
    from chat_downloader.models import ChatRequest
    from chat_downloader.runtime.chat_pipeline import configure_chat

    class LazyDownloader(Downloader):
        def get_chat(self, **kwargs):
            request = ChatRequest.from_kwargs(**kwargs)

            def source():
                chat.title = "Title"
                chat.id = "video"
                yield {"message_type": "text_message", "message": "hello"}

            chat = Chat(source(), status="completed")
            configure_chat(
                chat, request, SimpleNamespace(is_live_status=lambda _: False)
            )
            return chat

    result = execute_run(
        LazyDownloader,
        output=[
            str(tmp_path / "capture-{title}.jsonl"),
            str(tmp_path / "capture-{id}.jsonl"),
            str(tmp_path / "chat.txt"),
        ],
        verify_output=True,
    )
    assert not result.success
    assert not list(tmp_path.glob("*.jsonl"))
    assert not list(tmp_path.glob("*.txt"))


def test_conflicting_format_aliases_never_truncate_without_verification(tmp_path):
    params = parameters(tmp_path)
    params.pop("resume")
    params["verify_output"] = False
    raw, txt = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    raw.write_text("preserve\n")
    os.link(raw, txt)
    result = execute_run(Downloader, **params)
    assert not result.success
    assert "distinct files" in result.error_message
    assert raw.read_text() == txt.read_text() == "preserve\n"


@pytest.mark.parametrize("hardlink", [False, True])
def test_checkpoint_destination_never_aliases_capture_output(tmp_path, hardlink):
    params = parameters(tmp_path)
    checkpoint = tmp_path / "reserved.jsonl"
    params["resume"] = str(checkpoint)
    raw = tmp_path / "chat.jsonl"
    if hardlink:
        checkpoint.write_text("preserve\n")
        os.link(checkpoint, raw)
    else:
        params["output"][0] = str(checkpoint)
    result = execute_run(Downloader, **params)
    assert not result.success
    assert result.message_count == 0
    if hardlink:
        assert checkpoint.read_text() == raw.read_text() == "preserve\n"
    else:
        assert not checkpoint.exists()
    assert not (tmp_path / "chat.txt").exists()
    assert not (tmp_path / "reserved.jsonl.lock").exists()
