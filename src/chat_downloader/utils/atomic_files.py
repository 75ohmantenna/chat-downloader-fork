# SPDX-License-Identifier: MIT

"""Private staging and durable, atomic publication without replacement."""

from __future__ import annotations

import os
import secrets
import stat
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path
    from typing import TextIO


class PublishedFileError(OSError):
    """A complete final file exists, but its persistence could not be confirmed."""


def sync_directory(path: Path, directory_fd: int | None = None) -> None:
    """Persist published entries on POSIX; Windows has no portable equivalent."""
    try:
        if directory_fd is not None:
            os.fsync(directory_fd)
        elif os.name != "nt":
            descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    except OSError as error:
        raise PublishedFileError(*error.args) from error


@contextmanager
def _staging_directories(
    path: Path, directory_fd: int | None
) -> Iterator[tuple[int | None, int | None]]:
    """Pin private POSIX staging; preserve ordinary owned-directory Windows use."""
    if directory_fd is not None or os.name == "nt":
        yield directory_fd, directory_fd
        return
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    name = f".chat-downloader-{secrets.token_hex(16)}"
    staging = -1
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent)
        created = True
        staging = os.open(
            name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
        )
        entry = os.fstat(staging)
        if stat.S_IMODE(entry.st_mode) != 0o700 or entry.st_uid != os.getuid():
            msg = "Atomic publication requires an owned private staging directory."
            raise OSError(msg)
        yield staging, parent
    finally:
        if created:
            with suppress(OSError):
                current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                expected = os.fstat(staging) if staging >= 0 else current
                if current.st_uid == os.getuid() and (
                    current.st_dev,
                    current.st_ino,
                ) == (expected.st_dev, expected.st_ino):
                    os.rmdir(name, dir_fd=parent)
        if staging >= 0:
            with suppress(OSError):
                os.close(staging)
        with suppress(OSError):
            os.close(parent)


@contextmanager
def atomic_text_file(
    path: Path, *, directory_fd: int | None = None
) -> Iterator[TextIO]:
    """Publish only closed, fsynced text using an exclusive same-filesystem link.

    POSIX manifests stage within a private directory pinned by descriptor, so
    another user with write access to the destination parent cannot substitute
    the source entry. A supplied directory fd must already pin a private owned
    directory. Windows retains path-based same-directory staging for ordinary
    owned directories; it does not provide the POSIX hostile-namespace guarantee.
    Unsupported hard links fail rather than falling back to partial final files
    or overwriting renames. A post-publication persistence error identifies the
    complete final file with PublishedFileError. Cleanup is best effort.
    """
    with _staging_directories(path, directory_fd) as (source_fd, destination_fd):
        temporary = path.with_name(f".chat-downloader-{secrets.token_hex(16)}.tmp")
        source = temporary if source_fd is None else temporary.name
        destination = path if destination_fd is None else path.name
        descriptor = os.open(
            source,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=source_fd,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
                descriptor = -1
                yield target
                target.flush()
                os.fsync(target.fileno())
            os.link(
                source,
                destination,
                src_dir_fd=source_fd,
                dst_dir_fd=destination_fd,
                follow_symlinks=False,
            )
        finally:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
            with suppress(OSError):
                os.unlink(source, dir_fd=source_fd)
        sync_directory(path.parent, destination_fd)
