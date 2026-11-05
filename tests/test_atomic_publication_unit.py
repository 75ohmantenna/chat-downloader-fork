# SPDX-License-Identifier: MIT

"""Complete-file publication, durability failures, and concurrent reuse."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest

import chat_downloader.redaction as red
from chat_downloader.runtime.capture_manifest import RunManifest
from chat_downloader.runtime.runner import RunResult
from chat_downloader.utils import atomic_files
from tests.test_redaction_unit import (
    _restore_logging_state,  # noqa: F401 - pytest discovers this imported fixture
    capture_sample_dir,  # noqa: F401 - pytest discovers this imported fixture
)


def _publication_os(monkeypatch, **overrides):
    monkeypatch.setattr(atomic_files, "os", SimpleNamespace(**(vars(os) | overrides)))


@pytest.mark.parametrize("failure_at", ["create", "stream", "flush", "sync", "close"])
def test_manifest_io_failure_never_exposes_partial_report(
    tmp_path, monkeypatch, failure_at
):
    path = tmp_path / "report.json"
    original_open, original_fdopen, original_fsync = os.open, os.fdopen, os.fsync
    descriptors = []

    def create(*args, **kwargs):
        if failure_at == "create":
            raise OSError("staging creation failed")
        descriptor = original_open(*args, **kwargs)
        descriptors.append(descriptor)
        return descriptor

    class FailingStream:
        def __init__(self, descriptor, *args, **kwargs):
            self.stream = original_fdopen(descriptor, *args, **kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()
            if failure_at == "close":
                raise OSError("staging close failed")

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def write(self, value):
            assert not path.exists()
            return self.stream.write(value)

        def flush(self):
            self.stream.flush()
            if failure_at == "flush":
                raise OSError("staging flush failed")

    def stream(*args, **kwargs):
        if failure_at == "stream":
            raise OSError("stream creation failed")
        return FailingStream(*args, **kwargs)

    def sync(descriptor):
        if failure_at == "sync":
            raise OSError("staging fsync failed")
        return original_fsync(descriptor)

    _publication_os(monkeypatch, open=create, fdopen=stream, fsync=sync)
    with pytest.raises(OSError):
        RunManifest(str(path), None).write(None, RunResult(success=True))
    assert list(tmp_path.iterdir()) == []
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_manifest_corrected_outcome_write_failure_leaves_no_report(
    tmp_path, monkeypatch
):
    path = tmp_path / "report.json"
    result = RunResult(success=True, termination_reason="completed")
    original_fdopen = os.fdopen

    class InterruptedStream:
        def __init__(self, *args, **kwargs):
            self.stream = original_fdopen(*args, **kwargs)
            self.writes = 0

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def flush(self):
            self.stream.flush()
            result.success = False
            result.termination_reason = "interrupted"
            result.failure_stage = "manifest"

        def write(self, value):
            self.writes += 1
            if self.writes == 3:
                self.stream.write(value[:8])
                self.stream.flush()
                raise OSError("corrected outcome write failed")
            return self.stream.write(value)

    _publication_os(monkeypatch, fdopen=InterruptedStream)
    with pytest.raises(OSError, match="corrected outcome"):
        RunManifest(str(path), None).write(None, result)
    assert list(tmp_path.iterdir()) == []


def test_concurrent_manifests_publish_one_whole_report_without_replacement(
    tmp_path, monkeypatch
):
    path = tmp_path / "report.json"
    manifests = [RunManifest(str(path), None) for _ in range(2)]
    ready = Barrier(2)
    original_link = os.link

    def publish(source, destination, *args, **kwargs):
        descriptor = os.open(source, os.O_RDONLY, dir_fd=kwargs["src_dir_fd"])
        with os.fdopen(descriptor, "rb") as staged:
            report = json.load(staged)
            assert stat.S_IMODE(os.fstat(staged.fileno()).st_mode) == 0o600
        assert report["message_count"] in {1, 2}
        assert not path.exists()
        ready.wait(timeout=10)
        return original_link(source, destination, *args, **kwargs)

    def write(index):
        try:
            manifests[index].write(
                None, RunResult(success=True, message_count=index + 1)
            )
        except FileExistsError:
            return False
        return True

    _publication_os(monkeypatch, link=publish)
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(write, range(2)))
    assert sorted(outcomes) == [False, True]
    assert json.loads(path.read_bytes())["message_count"] == outcomes.index(True) + 1
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("directory_failure", ["open", "fsync"])
def test_directory_failure_never_leaves_a_partial_report(
    tmp_path, monkeypatch, directory_failure
):
    path = tmp_path / "report.json"
    original_open, original_fsync = os.open, os.fsync

    def open_directory(name, flags, *args, **kwargs):
        if flags & os.O_DIRECTORY and directory_failure == "open":
            raise OSError("directory open failed")
        return original_open(name, flags, *args, **kwargs)

    def sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            assert json.loads(path.read_bytes())["success"] is True
            raise OSError("directory fsync failed")
        return original_fsync(descriptor)

    _publication_os(monkeypatch, open=open_directory, fsync=sync)
    with pytest.raises(OSError, match="directory"):
        RunManifest(str(path), None).write(None, RunResult(success=True))
    if directory_failure == "open":
        assert list(tmp_path.iterdir()) == []
    else:
        assert json.loads(path.read_bytes())["success"] is True
        assert list(tmp_path.iterdir()) == [path]


def test_windows_directory_sync_does_not_claim_a_portable_directory_fsync(
    tmp_path, monkeypatch
):
    def unsupported(*_args, **_kwargs):
        pytest.fail("Windows directory fsync has no portable implementation")

    _publication_os(monkeypatch, name="nt", open=unsupported, fsync=unsupported)
    atomic_files.sync_directory(tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "contents",
    [
        b"",
        b"{",
        b"{}\n",
        b'{"other": 1}\n',
        b'{\n  "value": "sample"\n}',
        b'{\n  "value": "sample"\n}\nextra',
    ],
)
def test_sample_reuse_requires_exact_complete_bytes_and_allows_corrected_retry(
    sample_dir, contents
):
    name = red.capture_debug_sample("reuse", {"value": "sample"}, sample_limit=1)
    assert name is not None
    path = Path(name)
    original = path.read_bytes()
    path.write_bytes(contents)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (
        red.capture_debug_sample("reuse", {"value": "sample"}, sample_limit=1) is None
    )
    assert path.read_bytes() == contents
    assert list(sample_dir.iterdir()) == [path]
    path.write_bytes(original)
    assert (
        red.capture_debug_sample("reuse", {"value": "sample"}, sample_limit=1) == name
    )
    assert (
        red.capture_debug_sample("reuse", {"value": "different"}, sample_limit=1)
        is None
    )


@pytest.mark.parametrize("failure_at", ["file-sync", "publish", "directory-sync"])
def test_sample_publication_failure_releases_slots_and_retry_checks_complete_bytes(
    sample_dir, monkeypatch, failure_at
):
    original_link, original_fsync = os.link, os.fsync
    bounds = {"sample_limit": 1, "sample_group": "failures", "group_limit": 1}

    def publish(*args, **kwargs):
        if failure_at == "publish":
            raise OSError("publication failed")
        return original_link(*args, **kwargs)

    def sync(descriptor):
        directory = stat.S_ISDIR(os.fstat(descriptor).st_mode)
        if (failure_at == "directory-sync" and directory) or (
            failure_at == "file-sync" and not directory
        ):
            raise OSError("persistence failed")
        return original_fsync(descriptor)

    with monkeypatch.context() as failures:
        _publication_os(failures, link=publish, fsync=sync)
        assert red.capture_debug_sample("retry", {"value": 1}, **bounds) is None
    files = list(sample_dir.iterdir())
    if failure_at == "directory-sync":
        assert len(files) == 1
        assert json.loads(files[0].read_bytes()) == {"value": 1}
    else:
        assert files == []
    result = red.capture_debug_sample("retry", {"value": 1}, **bounds)
    assert result is not None
    assert json.loads(Path(result).read_bytes()) == {"value": 1}
    assert red.capture_debug_sample("other", {"value": 2}, **bounds) is None
    assert list(sample_dir.iterdir()) == [Path(result)]


def test_concurrent_identical_samples_reuse_only_complete_publication(
    sample_dir, monkeypatch
):
    ready = Barrier(2)
    original_link = os.link

    def publish(source, destination, *args, **kwargs):
        assert json.loads((sample_dir / source).read_bytes()) == {"value": 1}
        assert not (sample_dir / destination).exists()
        ready.wait(timeout=10)
        return original_link(source, destination, *args, **kwargs)

    def capture(_index):
        return red.capture_debug_sample("parallel", {"value": 1}, sample_limit=1)

    _publication_os(monkeypatch, link=publish)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(capture, range(2)))
    assert results[0] is not None
    assert results[0] == results[1]
    assert json.loads(Path(results[0]).read_bytes()) == {"value": 1}
    assert list(sample_dir.iterdir()) == [Path(results[0])]
    assert red.capture_debug_sample("parallel", {"value": 2}, sample_limit=1) is None


@pytest.mark.parametrize("failure_finishes_first", [False, True])
def test_failed_concurrent_attempt_cannot_release_another_samples_quota(
    sample_dir, monkeypatch, failure_finishes_first
):
    entered = Barrier(2)
    succeeded, failed = Event(), Event()
    original_link = os.link
    calls = 0
    call_lock = Lock()

    def publish(source, destination, *args, **kwargs):
        nonlocal calls
        with call_lock:
            index = calls
            calls += 1
        entered.wait(timeout=10)
        if index == 0:
            if not failure_finishes_first:
                assert succeeded.wait(timeout=10)
            raise OSError("one concurrent publication failed")
        if failure_finishes_first:
            assert failed.wait(timeout=10)
        return original_link(source, destination, *args, **kwargs)

    def capture(_index):
        result = red.capture_debug_sample("racing", {"value": 1}, sample_limit=1)
        (succeeded if result is not None else failed).set()
        return result

    _publication_os(monkeypatch, link=publish)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(capture, range(2)))
    assert sum(result is not None for result in results) == 1
    assert red.capture_debug_sample("racing", {"value": 2}, sample_limit=1) is None
    files = list(sample_dir.iterdir())
    assert len(files) == 1
    assert json.loads(files[0].read_bytes()) == {"value": 1}


@pytest.mark.skipif(not hasattr(os, "pathconf"), reason="POSIX filename limits")
def test_manifest_accepts_a_destination_at_the_filesystem_filename_limit(tmp_path):
    path = tmp_path / ("m" * os.pathconf(tmp_path, "PC_NAME_MAX"))
    RunManifest(str(path), None).write(None, RunResult(success=True))
    assert json.loads(path.read_bytes())["success"] is True
    assert list(tmp_path.iterdir()) == [path]


def test_sample_reuse_read_failure_preserves_file_and_allows_retry(
    sample_dir, monkeypatch
):
    name = red.capture_debug_sample("read-retry", {"value": 1}, sample_limit=1)
    assert name is not None
    path = Path(name)
    original = path.read_bytes()
    original_fdopen = os.fdopen
    read_descriptors = []

    class FailedRead:
        def __init__(self, descriptor, *args, **kwargs):
            self.stream = original_fdopen(descriptor, *args, **kwargs)
            read_descriptors.append(descriptor)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def read(self, _size):
            raise OSError("sample read failed")

    def stream(descriptor, mode, *args, **kwargs):
        if mode == "rb":
            return FailedRead(descriptor, mode, *args, **kwargs)
        return original_fdopen(descriptor, mode, *args, **kwargs)

    with monkeypatch.context() as failures:
        failures.setattr(red.os, "fdopen", stream)
        assert (
            red.capture_debug_sample("read-retry", {"value": 1}, sample_limit=1) is None
        )
    assert len(read_descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(read_descriptors[0])
    assert path.read_bytes() == original
    assert red.capture_debug_sample("read-retry", {"value": 1}, sample_limit=1) == name
    assert list(sample_dir.iterdir()) == [path]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX special-file contract")
def test_sample_reuse_rejects_fifo_without_blocking(sample_dir):
    name = red.capture_debug_sample("fifo-reuse", {"value": 1})
    assert name is not None
    path = Path(name)
    path.unlink()
    os.mkfifo(path, 0o600)
    assert red.capture_debug_sample("fifo-reuse", {"value": 1}) is None
    assert stat.S_ISFIFO(path.stat().st_mode)
    assert list(sample_dir.iterdir()) == [path]


@pytest.mark.parametrize("grouped", [False, True])
def test_published_sample_retains_quota_after_directory_persistence_failure(
    sample_dir, monkeypatch, grouped
):
    bounds = {"sample_limit": 1}
    if grouped:
        bounds.update(sample_group="published", group_limit=1)
    original_fsync = os.fsync

    def sync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("published directory persistence failed")
        return original_fsync(descriptor)

    with monkeypatch.context() as failures:
        _publication_os(failures, fsync=sync)
        assert red.capture_debug_sample("first", {"value": 1}, **bounds) is None
    files = list(sample_dir.iterdir())
    assert len(files) == 1
    assert json.loads(files[0].read_bytes()) == {"value": 1}
    label = "second" if grouped else "first"
    assert red.capture_debug_sample(label, {"value": 2}, **bounds) is None
    assert red.capture_debug_sample("first", {"value": 1}, **bounds) == str(files[0])
    assert list(sample_dir.iterdir()) == files


@pytest.mark.parametrize("replacement", ["regular", "symlink"])
def test_manifest_publication_uses_pinned_private_source_after_namespace_swap(
    tmp_path, monkeypatch, replacement
):
    tmp_path.chmod(0o777)
    path = tmp_path / "report.json"
    victim = tmp_path / "victim.json"
    victim.write_text("untouched", encoding="utf-8")
    moved = tmp_path / "moved-private-staging"
    original_link = os.link
    replaced = []

    def publish(source, destination, *args, **kwargs):
        entries = [entry for entry in tmp_path.iterdir() if entry.is_dir()]
        assert len(entries) == 1
        private = entries[0]
        assert stat.S_IMODE(private.stat().st_mode) == 0o700
        pinned = os.fstat(kwargs["src_dir_fd"])
        assert (pinned.st_dev, pinned.st_ino) == (
            private.stat().st_dev,
            private.stat().st_ino,
        )
        private.rename(moved)
        private.mkdir(mode=0o700)
        substituted = private / source
        if replacement == "symlink":
            substituted.symlink_to(victim)
        else:
            substituted.write_bytes(b"")
        replaced.append(substituted)
        return original_link(source, destination, *args, **kwargs)

    _publication_os(monkeypatch, link=publish)
    RunManifest(str(path), None).write(None, RunResult(success=True, message_count=7))
    assert not path.is_symlink()
    assert json.loads(path.read_bytes())["message_count"] == 7
    assert victim.read_text(encoding="utf-8") == "untouched"
    assert replaced[0].exists()
    assert list(moved.iterdir()) == []


@pytest.mark.parametrize("unsafe", ["mode", "owner"])
def test_manifest_rejects_an_unsafe_opened_private_staging_directory(
    tmp_path, monkeypatch, unsafe
):
    original_fstat = os.fstat

    def metadata(descriptor):
        entry = original_fstat(descriptor)
        values = list(entry)
        if unsafe == "mode":
            values[0] = (entry.st_mode & ~0o777) | 0o755
        else:
            values[4] = entry.st_uid + 1
        return os.stat_result(values)

    _publication_os(monkeypatch, fstat=metadata)
    with pytest.raises(OSError, match="private staging directory"):
        RunManifest(str(tmp_path / "report.json"), None).write(
            None, RunResult(success=True)
        )
    assert list(tmp_path.iterdir()) == []


def test_windows_publication_fallback_preserves_ordinary_complete_no_clobber(
    tmp_path, monkeypatch
):
    """Exercise the Windows branch synthetically, not native handle semantics."""
    path = tmp_path / "report.json"
    original_link, original_fsync = os.link, os.fsync
    reports = [RunManifest(str(path), None) for _ in range(2)]

    def sync(descriptor):
        assert stat.S_ISREG(os.fstat(descriptor).st_mode)
        return original_fsync(descriptor)

    def publish(source, destination, *args, **kwargs):
        assert kwargs["src_dir_fd"] is None
        assert kwargs["dst_dir_fd"] is None
        return original_link(source, destination, *args, **kwargs)

    _publication_os(monkeypatch, name="nt", fsync=sync, link=publish)
    reports[0].write(None, RunResult(success=True, message_count=1))
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        reports[1].write(None, RunResult(success=True, message_count=2))
    assert path.read_bytes() == original
    assert json.loads(original)["message_count"] == 1
    assert list(tmp_path.iterdir()) == [path]


def test_posix_directory_persistence_can_open_its_own_descriptor(tmp_path):
    atomic_files.sync_directory(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_validated_existing_sample_retains_quota_when_file_sync_fails(
    sample_dir, monkeypatch
):
    name = red.capture_debug_sample("existing-sync", {"value": 1})
    assert name is not None
    path = Path(name)
    identity = (path.stat().st_dev, path.stat().st_ino)
    original_fsync = os.fsync
    bounds = {
        "sample_limit": 1,
        "sample_group": "existing-sync-group",
        "group_limit": 1,
    }
    synchronized_existing = []

    def sync(descriptor):
        entry = os.fstat(descriptor)
        if (entry.st_dev, entry.st_ino) == identity:
            synchronized_existing.append(identity)
            raise OSError("existing complete sample fsync failed")
        return original_fsync(descriptor)

    with monkeypatch.context() as failures:
        failures.setattr(red.os, "fsync", sync)
        assert red.capture_debug_sample("existing-sync", {"value": 1}, **bounds) is None
    assert synchronized_existing == [identity]
    assert red.capture_debug_sample("existing-sync", {"value": 2}, **bounds) is None
    assert red.capture_debug_sample("other-label", {"value": 2}, **bounds) is None
    assert red.capture_debug_sample("existing-sync", {"value": 1}, **bounds) == name
    assert list(sample_dir.iterdir()) == [path]
