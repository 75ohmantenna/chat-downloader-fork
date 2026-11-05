# SPDX-License-Identifier: MIT

"""Real capture writers reject filesystem redirections and generated collisions."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from chat_downloader.output import _secure_file
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.output.writers import (
    ContinuousFileWriter,
    JsonLinesContinuousWriter,
)
from chat_downloader.runtime.runner import execute_run
from chat_downloader.sites import output_dispatch
from chat_downloader.sites.models import Chat
from tests.test_capture_checkpoint_unit import Downloader, parameters

_SECRET = "synthetic-private-output-sentinel"  # noqa: S105 — synthetic capture data


def _options(outputs, *, overwrite=True):
    return {
        "url": "https://example.invalid/video",
        "output": [str(path) for path in outputs],
        "overwrite": overwrite,
        "quiet": True,
        "format": "kick",
    }


class _TitledDownloader(Downloader):
    title = "stream"

    def get_chat(self, **kwargs):
        chat = super().get_chat(**kwargs)
        chat.title = self.title
        return chat


def test_unconfigured_writer_cannot_commit_other_capture_files(tmp_path):
    output = tmp_path / "capture.jsonl"
    output.write_text(_SECRET)
    configured = ContinuousWriter(str(output), lazy_initialise=True)
    unconfigured = ContinuousWriter(None, lazy_initialise=True)
    chat = Chat(iter([{"message_id": "synthetic-first-record"}]))
    chat.attach_writer(configured)
    chat.attach_writer(unconfigured)

    with pytest.raises(ValueError, match="File name not set"):
        list(chat)

    assert output.read_text() == _SECRET
    assert not configured.is_initialised()
    assert not unconfigured.is_initialised()


def test_mixed_eager_and_lazy_writers_preserve_existing_literal_paths(tmp_path):
    first = tmp_path / "prior-{id}.jsonl"
    second = tmp_path / "second.jsonl"
    existing = ContinuousWriter(str(first))
    existing.write({"message_id": "prior"})
    pending = ContinuousWriter(str(second), lazy_initialise=True)
    chat = Chat(iter([{"message_id": "new"}]), id="synthetic")
    chat.attach_writer(existing)
    chat.attach_writer(pending)

    assert [row["message_id"] for row in chat] == ["new"]
    assert [
        json.loads(line)["message_id"] for line in first.read_text().splitlines()
    ] == ["prior", "new"]
    assert json.loads(second.read_text())["message_id"] == "new"
    assert not (tmp_path / "prior-synthetic.jsonl").exists()


@pytest.mark.parametrize("extension", ["jsonl", "txt"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_capture_rejects_symlink_planted_after_lazy_attachment(
    tmp_path, extension, overwrite
):
    victim = tmp_path / "private.txt"
    victim.write_text(_SECRET)
    output = tmp_path / f"chat.{extension}"

    class PlantedLink(Downloader):
        def get_chat(self, **kwargs):
            chat = super().get_chat(**kwargs)
            output.symlink_to(victim)
            return chat

    manifest = tmp_path / "report.json"
    result = execute_run(
        PlantedLink,
        run_manifest=str(manifest),
        **_options([output], overwrite=overwrite),
    )
    assert not result.success
    assert "regular file" in result.error_message
    assert _SECRET not in result.error_message
    assert victim.read_text() == _SECRET
    report = json.loads(manifest.read_text())
    assert not report["success"]
    assert report["outputs"][0]["sha256"] is None
    assert not report["outputs"][0]["file_created"]


@pytest.mark.parametrize("kind", ["directory", "fifo", "hardlink"])
@pytest.mark.parametrize("overwrite", [False, True])
def test_capture_preflights_all_outputs_before_overwriting(tmp_path, kind, overwrite):
    first = tmp_path / "first.jsonl"
    first.write_text("preserve first output\n")
    second = tmp_path / "second.txt"
    victim = tmp_path / "private.txt"
    victim.write_text(_SECRET)
    if kind == "directory":
        second.mkdir()
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("Filesystem FIFOs are unavailable")
        os.mkfifo(second)
    else:
        os.link(victim, second)
    result = execute_run(Downloader, **_options([first, second], overwrite=overwrite))
    assert not result.success
    assert "regular file" in result.error_message
    assert first.read_text() == "preserve first output\n"
    assert victim.read_text() == _SECRET


@pytest.mark.parametrize("directory_fd", [False, True])
def test_capture_detects_parent_namespace_substitution(
    tmp_path, monkeypatch, directory_fd
):
    if directory_fd and not _secure_file._SUPPORTS_DIRECTORY_FD:
        pytest.skip("Directory-relative opens are unavailable")
    monkeypatch.setattr(_secure_file, "_SUPPORTS_DIRECTORY_FD", directory_fd)
    parent = tmp_path / "outputs"
    parent.mkdir()
    output = parent / "capture.jsonl"
    output.write_text("preserve original\n")
    victim = tmp_path / "private.txt"
    victim.write_text(_SECRET)
    displaced = tmp_path / "displaced"
    original_open = os.open
    substituted = False

    def replace_parent_after_open(name, flags, mode=0o777, *, dir_fd=None):
        nonlocal substituted
        descriptor = original_open(name, flags, mode, dir_fd=dir_fd)
        if Path(name).name == output.name and not substituted:
            substituted = True
            parent.rename(displaced)
            parent.mkdir()
            (parent / output.name).write_text(_SECRET)
        return descriptor

    monkeypatch.setattr(os, "open", replace_parent_after_open)
    result = execute_run(Downloader, **_options([output]))
    assert substituted
    assert not result.success
    assert victim.read_text() == _SECRET
    assert (displaced / output.name).read_text() == "preserve original\n"


@pytest.mark.parametrize("nofollow", [False, True])
@pytest.mark.parametrize("substitution", ["symlink", "regular", "hardlink", "fifo"])
def test_capture_never_truncates_unvalidated_opened_destination(
    tmp_path, monkeypatch, nofollow, substitution
):
    if nofollow and not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("Kernel no-follow opens are unavailable")
    if substitution == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("Filesystem FIFOs are unavailable")
    if not nofollow:
        monkeypatch.delattr(os, "O_NOFOLLOW", raising=False)
    output = tmp_path / "capture.jsonl"
    output.write_text("preserve original\n")
    saved = tmp_path / "saved.jsonl"
    victim = tmp_path / "private.txt"
    victim.write_text(_SECRET)
    original_open = os.open
    substituted = False

    def replace_leaf_before_open(name, flags, mode=0o777, *, dir_fd=None):
        nonlocal substituted
        if Path(name).name == output.name and not substituted:
            substituted = True
            output.rename(saved)
            if substitution == "symlink":
                output.symlink_to(victim)
            elif substitution == "hardlink":
                os.link(victim, output)
            elif substitution == "fifo":
                os.mkfifo(output)
            else:
                output.write_text("preserve replacement\n")
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", replace_leaf_before_open)
    result = execute_run(Downloader, **_options([output]))
    assert substituted
    assert not result.success
    assert victim.read_text() == _SECRET
    assert saved.read_text() == "preserve original\n"
    if substitution == "regular":
        assert output.read_text() == "preserve replacement\n"


@pytest.mark.parametrize("field", ["title", "id"])
@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("extension", ["jsonl", "txt"])
def test_generated_output_cannot_touch_existing_destination(
    tmp_path, field, overwrite, extension
):
    value = "stream" if field == "title" else "video"
    existing = tmp_path / f"{value}.{extension}"
    existing.write_text(_SECRET)
    explicit = tmp_path / "explicit.jsonl"
    explicit.write_text("preserve explicit\n")
    template = tmp_path / f"{{{field}}}.{extension}"
    result = execute_run(
        _TitledDownloader,
        **_options([explicit, template], overwrite=overwrite),
    )
    assert not result.success
    assert "Generated output filename already exists" in result.error_message
    assert existing.read_text() == _SECRET
    assert explicit.read_text() == "preserve explicit\n"


@pytest.mark.parametrize("overwrite", [False, True])
def test_generated_output_exclusive_open_rejects_late_creation(
    tmp_path, monkeypatch, overwrite
):
    output = tmp_path / "video.jsonl"
    original_open = os.open
    planted = False

    def create_before_exclusive_open(name, flags, mode=0o777, *, dir_fd=None):
        nonlocal planted
        if Path(name).name == output.name and not planted:
            planted = True
            output.write_text(_SECRET)
            assert flags & os.O_EXCL
            assert not flags & os.O_TRUNC
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", create_before_exclusive_open)
    result = execute_run(
        Downloader,
        **_options([tmp_path / "{id}.jsonl"], overwrite=overwrite),
    )
    assert planted
    assert not result.success
    assert "Generated output filename already exists" in result.error_message
    assert output.read_text() == _SECRET


@pytest.mark.parametrize("overwrite", [False, True])
def test_distinct_titles_with_same_sanitized_name_collide(tmp_path, overwrite):
    class First(_TitledDownloader):
        title = "same/title"

    class Second(_TitledDownloader):
        title = "same\\title"

    options = _options([tmp_path / "{title}.jsonl"], overwrite=overwrite)
    first = execute_run(First, **options)
    assert first.success
    output = tmp_path / "same_title.jsonl"
    previous = output.read_bytes()
    second = execute_run(Second, **options)
    assert not second.success
    assert "Generated output filename already exists" in second.error_message
    assert output.read_bytes() == previous
    assert len(output.read_text().splitlines()) == first.message_count == 4


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("filename", ["fixed", "{{archive}}", "{{title}}"])
def test_explicit_paths_and_literal_braces_keep_overwrite_append_policy(
    tmp_path, overwrite, filename
):
    output = tmp_path / f"{filename.format()}.jsonl"
    output.write_text('{"preserved": true}\n')
    result = execute_run(
        Downloader,
        **_options([tmp_path / f"{filename}.jsonl"], overwrite=overwrite),
    )
    assert result.success
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert len(rows) == (4 if overwrite else 5)
    if not overwrite:
        assert rows[0] == {"preserved": True}
    assert rows[-1]["message_id"] == "4"


@pytest.mark.parametrize("overwrite", [False, True])
def test_generated_alias_cannot_bypass_existing_collision(tmp_path, overwrite):
    output = tmp_path / "video.jsonl"
    output.write_text(_SECRET)
    result = execute_run(
        Downloader,
        **_options([output, tmp_path / "{id}.jsonl"], overwrite=overwrite),
    )
    assert not result.success
    assert "Generated output filename already exists" in result.error_message
    assert output.read_text() == _SECRET


def test_generated_alias_propagates_exclusive_policy_to_kept_writer(
    tmp_path, monkeypatch
):
    output = tmp_path / "video.jsonl"
    original_open = os.open
    planted = False

    def plant_alias_collision(name, flags, mode=0o777, *, dir_fd=None):
        nonlocal planted
        if Path(name).name == output.name and not planted:
            planted = True
            output.write_text(_SECRET)
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", plant_alias_collision)
    result = execute_run(Downloader, **_options([output, tmp_path / "{id}.jsonl"]))
    assert planted
    assert not result.success
    assert "Generated output filename already exists" in result.error_message
    assert output.read_text() == _SECRET


@pytest.mark.parametrize("extension", ["jsonl", "txt"])
def test_created_output_is_private_and_empty_capture_stays_lazy(tmp_path, extension):
    output = tmp_path / f"capture.{extension}"

    class Empty(Downloader):
        records = ()

    assert execute_run(Empty, **_options([output])).success
    assert not output.exists()
    result = execute_run(Downloader, **_options([output]))
    assert result.success
    assert len(output.read_text().splitlines()) == result.message_count == 4
    if os.name != "nt":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        (b'{"prior": 1}', b'{"prior": 1}\n'),
        (b'{"prior": 1}\n{"broken":', b'{"prior": 1}\n'),
        (b'{"broken":', b""),
        (b'{"prior": 1}\n\xff', b'{"prior": 1}\n'),
    ],
)
def test_capture_append_repairs_tail_on_only_opened_handle(
    tmp_path, monkeypatch, tail, expected
):
    output = tmp_path / "capture.jsonl"
    output.write_bytes(tail)
    original_open = os.open
    opened = []

    def count_destination_opens(name, flags, mode=0o777, *, dir_fd=None):
        if Path(name).name == output.name:
            opened.append(flags)
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", count_destination_opens)
    result = execute_run(Downloader, **_options([output], overwrite=False))
    assert result.success
    assert len(opened) == 1
    assert not opened[0] & os.O_TRUNC
    data = output.read_bytes()
    assert data.startswith(expected)
    assert len(data.splitlines()) == result.message_count + expected.count(b"\n")


def test_checkpoint_rollback_cannot_follow_replaced_output_path(tmp_path, monkeypatch):
    output = tmp_path / "chat.jsonl"
    saved = tmp_path / "saved.jsonl"
    victim = tmp_path / "private.txt"
    victim.write_text(_SECRET)
    original_write = ContinuousWriter.write

    def replace_before_interruption(writer, item, *, flush=False):
        original_write(writer, item, flush=flush)
        if writer.file_name == str(output) and item["message_id"] == "2":
            output.rename(saved)
            output.symlink_to(victim)
            raise KeyboardInterrupt

    monkeypatch.setattr(ContinuousWriter, "write", replace_before_interruption)
    result = execute_run(Downloader, **parameters(tmp_path))
    assert result.interrupted
    assert victim.read_text() == _SECRET
    assert len(saved.read_text().splitlines()) == 1
    assert not (tmp_path / "checkpoint.json").exists()


def test_verified_generated_pair_keeps_counts_hashes_and_parity(tmp_path):
    manifest = tmp_path / "report.json"
    result = execute_run(
        _TitledDownloader,
        verify_output=True,
        run_manifest=str(manifest),
        **_options([tmp_path / "{title}.jsonl", tmp_path / "{id}.txt"]),
    )
    assert result.success
    assert result.parity_status == "passed"
    report = json.loads(manifest.read_text())
    assert report["message_count"] == 4
    assert all(item["records_written"] == 4 for item in report["outputs"])
    assert all(len(item["sha256"]) == 64 for item in report["outputs"])


def test_open_identity_must_be_available_before_truncation(tmp_path, monkeypatch):
    output = tmp_path / "capture.jsonl"
    output.write_text(_SECRET)
    original_stat = os.stat

    def no_identity(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if Path(path).name == output.name:
            return SimpleNamespace(
                st_mode=info.st_mode,
                st_nlink=info.st_nlink,
                st_dev=info.st_dev,
                st_ino=0,
            )
        return info

    monkeypatch.setattr(os, "stat", no_identity)
    result = execute_run(Downloader, **_options([output]))
    assert not result.success
    assert "verifiable file identity" in result.error_message
    assert output.read_text() == _SECRET


def test_descriptor_open_failure_closes_pinned_parent(tmp_path, monkeypatch):
    output = tmp_path / "capture.jsonl"
    original_fdopen = os.fdopen
    descriptors = []

    def fail_wrapping(descriptor, *args, **kwargs):
        descriptors.append(descriptor)
        raise OSError("synthetic descriptor wrapping failure")

    monkeypatch.setattr(os, "fdopen", fail_wrapping)
    with pytest.raises(OSError, match="descriptor wrapping failure"):
        ContinuousWriter(str(output))
    monkeypatch.setattr(os, "fdopen", original_fdopen)
    assert descriptors
    for descriptor in descriptors:
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.fstat(descriptor)


def test_prepared_handle_is_closed_when_later_preflight_fails(tmp_path, monkeypatch):
    first, second = tmp_path / "first.jsonl", tmp_path / "second.txt"
    first.write_text(_SECRET)
    original_prepare = ContinuousWriter._prepare_output
    prepared = []

    def fail_second(writer):
        if writer.file_name == str(second):
            raise OSError("synthetic second output failure")
        original_prepare(writer)
        prepared.append(writer._prepared_file.stream)

    monkeypatch.setattr(ContinuousWriter, "_prepare_output", fail_second)
    result = execute_run(Downloader, **_options([first, second]))
    assert not result.success
    assert first.read_text() == _SECRET
    assert prepared
    assert all(stream.closed for stream in prepared)


def test_missing_path_after_exclusive_race_fails_closed(tmp_path, monkeypatch):
    output = tmp_path / "capture.jsonl"
    original_open = os.open

    def disappearing_destination(name, flags, mode=0o777, *, dir_fd=None):
        if Path(name).name == output.name:
            raise FileExistsError("synthetic vanished competing creator")
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", disappearing_destination)
    result = execute_run(Downloader, **_options([output], overwrite=False))
    assert not result.success
    assert "changed while it was being opened" in result.error_message
    assert not output.exists()


def test_prepared_handle_rejects_removed_destination(tmp_path):
    output = tmp_path / "capture.jsonl"
    writer = ContinuousWriter(str(output), lazy_initialise=True)
    writer._prepare_output()
    output.unlink()
    with pytest.raises(OSError, match="changed before writing"):
        writer.initialize()
    assert not writer.is_initialised()
    assert writer._prepared_file is None


def test_repair_failure_closes_transferred_stream(tmp_path, monkeypatch):
    output = tmp_path / "capture.jsonl"
    output.write_text('{"prior": 1}')
    stream = None
    original_detach = _secure_file._OutputFile.detach

    def remember_stream(prepared):
        nonlocal stream
        stream = original_detach(prepared)
        return stream

    monkeypatch.setattr(_secure_file._OutputFile, "detach", remember_stream)
    monkeypatch.setattr(os, "fsync", Mock(side_effect=OSError("repair sync failure")))
    with pytest.raises(OSError, match="repair sync failure"):
        JsonLinesContinuousWriter(str(output), overwrite=False)
    assert stream is not None
    assert stream.closed


def test_checkpoint_rejects_non_descriptor_writer():
    chat = Chat(iter(()))
    chat.attach_writer(Mock(file_name="capture.jsonl"))
    with (
        pytest.raises(TypeError, match="descriptor-backed"),
        chat._output_dispatcher.record_transaction(),
    ):
        pass


@pytest.mark.parametrize("generated_first", [False, True])
def test_verified_alias_collision_preserves_both_existing_outputs(
    tmp_path, generated_first
):
    raw, text = tmp_path / "video.jsonl", tmp_path / "chat.txt"
    raw.write_text(_SECRET)
    text.write_text("preserve text\n")
    aliases = [raw, tmp_path / "{id}.jsonl"]
    if generated_first:
        aliases.reverse()
    result = execute_run(Downloader, verify_output=True, **_options([*aliases, text]))
    assert not result.success
    assert "Generated output filename already exists" in result.error_message
    assert raw.read_text() == _SECRET
    assert text.read_text() == "preserve text\n"


def test_metadata_braces_are_literal_after_one_template_expansion(tmp_path):
    class BracedTitle(_TitledDownloader):
        title = "{id}"

    manifest = tmp_path / "report.json"
    result = execute_run(
        BracedTitle,
        run_manifest=str(manifest),
        **_options([tmp_path / "{title}.jsonl"]),
    )
    assert result.success
    output = tmp_path / "{id}.jsonl"
    assert len(output.read_text().splitlines()) == 4
    assert json.loads(manifest.read_text())["outputs"][0]["file_name"] == str(output)


def test_prepare_requires_filename_and_initialization_failure_releases_handle(
    tmp_path, monkeypatch
):
    unnamed = ContinuousWriter(None, lazy_initialise=True)
    with pytest.raises(ValueError, match="File name not set"):
        unnamed._prepare_output()
    writer = ContinuousWriter(str(tmp_path / "capture.jsonl"), lazy_initialise=True)
    writer._prepare_output()
    prepared = writer._prepared_file
    stream = prepared.stream
    monkeypatch.setattr(writer, "_open_writer", Mock(side_effect=KeyboardInterrupt))
    with pytest.raises(KeyboardInterrupt):
        writer.initialize()
    assert writer._prepared_file is None
    assert not writer.is_initialised()
    assert stream.closed


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("kind", ["symlink", "regular", "hardlink"])
@pytest.mark.parametrize("replaced", ["first", "second"])
@pytest.mark.parametrize("timing", ["before_first_transfer", "after_last_transfer"])
def test_capture_revalidates_prepared_handle_before_repair_or_overwrite(
    tmp_path, monkeypatch, overwrite, kind, replaced, timing
):
    first, second = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    first.write_text(_SECRET)
    second.write_text("preserve text\n")
    target = first if replaced == "first" else second
    untouched = second if replaced == "first" else first
    original_target = target.read_text()
    original_untouched = untouched.read_text()
    saved = tmp_path / "saved.jsonl"
    victim = tmp_path / "private.txt"
    victim.write_text(_SECRET)
    original_initialize = ContinuousWriter.initialize
    original_prepare = ContinuousWriter._prepare_output
    prepared_handles = []
    substituted = False

    def remember_preparation(writer):
        original_prepare(writer)
        prepared = writer._prepared_file
        if prepared is not None and prepared.stream is not None:
            prepared_handles.append((prepared, prepared.stream))

    def substitute():
        nonlocal substituted
        substituted = True
        target.rename(saved)
        if kind == "symlink":
            target.symlink_to(victim)
        elif kind == "hardlink":
            os.link(victim, target)
        else:
            target.write_text("preserve replacement\n")

    def substitute_at_transfer_boundary(writer):
        if (
            timing == "before_first_transfer"
            and writer.file_name == str(first)
            and not substituted
        ):
            substitute()
        original_initialize(writer)
        if (
            timing == "after_last_transfer"
            and writer.file_name == str(second)
            and not substituted
        ):
            substitute()

    monkeypatch.setattr(ContinuousWriter, "_prepare_output", remember_preparation)
    monkeypatch.setattr(ContinuousWriter, "initialize", substitute_at_transfer_boundary)
    result = execute_run(Downloader, **_options([first, second], overwrite=overwrite))
    assert substituted
    assert not result.success
    assert saved.read_text() == original_target
    assert victim.read_text() == _SECRET
    assert untouched.read_text() == original_untouched
    if kind == "regular":
        assert target.read_text() == "preserve replacement\n"
    assert len({id(stream) for _prepared, stream in prepared_handles}) == 2
    assert all(
        stream.closed and prepared.directory_fd is None
        for prepared, stream in prepared_handles
    )


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("source", ["known_id", "lazy_title"])
@pytest.mark.parametrize("aliases", ["generated", "explicit_first", "generated_first"])
def test_generated_policy_survives_arrival_after_path_preflight(
    tmp_path, monkeypatch, overwrite, source, aliases
):
    downloader = Downloader if source == "known_id" else _TitledDownloader
    field = "id" if source == "known_id" else "title"
    name = "video" if source == "known_id" else "stream"
    output = tmp_path / f"{name}.jsonl"
    template = tmp_path / f"{{{field}}}.jsonl"
    outputs = [template]
    if aliases == "explicit_first":
        outputs = [output, template]
    elif aliases == "generated_first":
        outputs = [template, output]
    original_check = output_dispatch._check_output_path
    planted = False

    def create_after_check(file_name, *, exclusive):
        nonlocal planted
        original_check(file_name, exclusive=exclusive)
        if Path(file_name) == output and exclusive and not planted:
            planted = True
            output.write_text(_SECRET)

    monkeypatch.setattr(output_dispatch, "_check_output_path", create_after_check)
    result = execute_run(downloader, **_options(outputs, overwrite=overwrite))
    assert planted
    assert not result.success
    assert "Generated output filename already exists" in result.error_message
    assert output.read_text() == _SECRET


def test_attaching_eager_writer_does_not_reinitialize_its_existing_stream(tmp_path):
    output = tmp_path / "capture.jsonl"
    writer = ContinuousWriter(str(output))
    writer.write({"preserved": True})
    message = {"message_id": "one", "message": "captured"}
    chat = Chat(iter([message]))
    chat.attach_writer(writer)
    assert list(chat) == [message]
    assert [json.loads(line) for line in output.read_text().splitlines()] == [
        {"preserved": True},
        message,
    ]


@pytest.mark.parametrize("overwrite", [False, True])
def test_alias_grouping_uses_the_same_snapshot_as_generated_policy(
    tmp_path, monkeypatch, overwrite
):
    output = tmp_path / "video.jsonl"
    message = {"message_id": "one", "message": "captured"}
    chat = Chat(iter([message]), id="video")
    chat.attach_writer(
        ContinuousWriter(str(output), overwrite=overwrite, lazy_initialise=True)
    )
    chat.attach_writer(
        ContinuousWriter(
            str(tmp_path / "{id}.jsonl"),
            overwrite=overwrite,
            lazy_initialise=True,
        )
    )
    original_identity = output_dispatch.output_path_identity
    original_check = output_dispatch._check_output_path
    snapshots = 0
    temporary_observed = False
    planted = False

    def snapshot_with_transient_inode(path):
        nonlocal snapshots, temporary_observed
        snapshots += 1
        if snapshots == 2:
            output.write_text('{"temporary": true}\n')
            identity = original_identity(path)
            output.unlink()
            temporary_observed = True
            return identity
        return original_identity(path)

    def arrive_after_generated_preflight(file_name, *, exclusive):
        nonlocal planted
        original_check(file_name, exclusive=exclusive)
        if exclusive and not planted:
            planted = True
            output.write_text(_SECRET)

    monkeypatch.setattr(
        output_dispatch, "output_path_identity", snapshot_with_transient_inode
    )
    monkeypatch.setattr(
        output_dispatch, "_check_output_path", arrive_after_generated_preflight
    )
    with pytest.raises(FileExistsError, match="Generated output filename"):
        list(chat)
    assert temporary_observed
    assert planted
    assert output.read_text() == _SECRET


@pytest.mark.parametrize("overwrite", [False, True])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "regular"])
def test_post_commit_substitution_cannot_redirect_or_abort_pinned_group(
    tmp_path, monkeypatch, overwrite, kind
):
    first, second = tmp_path / "chat.jsonl", tmp_path / "chat.txt"
    first.write_text('{"prior": true}\n')
    second.write_text("prior text\n")
    saved = tmp_path / "saved.txt"
    victim = tmp_path / "private.txt"
    victim.write_text(_SECRET)
    manifest = tmp_path / "report.json"
    original_commit = ContinuousFileWriter._initialize_file
    substituted = False

    def replace_second_at_first_commit(writer):
        nonlocal substituted
        if not substituted:
            assert writer.file_name == str(first)
            substituted = True
            second.rename(saved)
            if kind == "symlink":
                second.symlink_to(victim)
            elif kind == "hardlink":
                os.link(victim, second)
            else:
                second.write_text("preserve replacement\n")
        original_commit(writer)

    monkeypatch.setattr(
        ContinuousFileWriter, "_initialize_file", replace_second_at_first_commit
    )
    result = execute_run(
        Downloader,
        run_manifest=str(manifest),
        **_options([first, second], overwrite=overwrite),
    )
    assert substituted
    # The collectively validated group commits to the original descriptors.
    # Its namespace replacement is rejected only at artifact certification,
    # never by a later activation after an earlier file has been truncated.
    assert result.message_count == 4
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    assert len(rows) == (4 if overwrite else 5)
    assert rows[-1]["message_id"] == "4"
    assert len(saved.read_text().splitlines()) == (4 if overwrite else 5)
    if not overwrite:
        assert rows[0] == {"prior": True}
        assert saved.read_text().startswith("prior text\n")
    assert victim.read_text() == _SECRET
    if kind == "regular":
        assert second.read_text() == "preserve replacement\n"
    assert not result.success
    assert result.failure_stage == "manifest"
    assert not manifest.exists()
