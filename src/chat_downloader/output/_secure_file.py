# SPDX-License-Identifier: MIT

"""Non-destructive validation and descriptor-pinned capture destinations."""

from __future__ import annotations

import os
import stat
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from typing import BinaryIO

_SUPPORTS_DIRECTORY_FD = os.open in os.supports_dir_fd and os.stat in os.supports_dir_fd
_COLLISION_MESSAGE = (
    "Generated output filename already exists; choose a distinct output path."
)


def _identity(info: os.stat_result) -> tuple[int, int]:
    """Return the filesystem identity used before any destructive operation."""
    if not info.st_ino:
        msg = "Output filesystem does not provide a verifiable file identity."
        raise OSError(msg)
    return info.st_dev, info.st_ino


def _validate_regular(
    info: os.stat_result, before: os.stat_result | None = None
) -> None:
    """Reject links, special files, and hard-linked redirections."""
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        msg = "Output destination must be a regular file with exactly one link."
        raise OSError(msg)
    _identity(info)
    if before is not None and _identity(before) != _identity(info):
        msg = "Output destination changed while it was being opened."
        raise OSError(msg)


def _entry_info(path: Path, directory_fd: int | None) -> os.stat_result | None:
    """Inspect the destination itself rather than following its final link."""
    try:
        return os.stat(
            path if directory_fd is None else path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None


def _check_output_path(file_name: str, *, exclusive: bool) -> None:
    """Reject unsafe paths and known generated collisions before any opens."""
    current = _entry_info(Path(file_name), None)
    if current is None:
        return
    if exclusive:
        raise FileExistsError(_COLLISION_MESSAGE)
    _validate_regular(current)


def _open_descriptor(
    path: Path, directory_fd: int | None, *, append: bool, exclusive: bool
) -> tuple[int, os.stat_result | None]:
    """Create exclusively or open an inspected file without truncating it."""
    name = path if directory_fd is None else path.name
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    if append:
        flags |= os.O_APPEND
    before = _entry_info(path, directory_fd)
    if exclusive:
        try:
            descriptor = os.open(
                name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd
            )
        except FileExistsError:
            raise FileExistsError(_COLLISION_MESSAGE) from None
        return descriptor, None
    if before is None:
        try:
            descriptor = os.open(
                name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory_fd
            )
        except FileExistsError:
            # A legitimate append writer may create the file after our inspection.
            before = _entry_info(path, directory_fd)
        else:
            return descriptor, None
    if before is None:
        msg = "Output destination changed while it was being opened."
        raise OSError(msg)
    _validate_regular(before)
    return os.open(name, flags, dir_fd=directory_fd), before


class _OutputFile:
    """Hold a validated binary stream and its parent until writer handoff."""

    def __init__(self, file_name: str, *, append: bool, exclusive: bool) -> None:
        """Pin the namespace and acquire a non-truncating file descriptor."""
        self.path = Path(file_name).absolute()
        self.stream: BinaryIO | None = None
        self.directory_fd: int | None = None
        descriptor = -1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if _SUPPORTS_DIRECTORY_FD:
                self.directory_fd = os.open(
                    self.path.parent, os.O_RDONLY | os.O_DIRECTORY
                )
            self.parent_info = self.path.parent.stat()
            descriptor, before = _open_descriptor(
                self.path, self.directory_fd, append=append, exclusive=exclusive
            )
            self.info = os.fstat(descriptor)
            _validate_regular(self.info, before)
            self.stream = os.fdopen(descriptor, "r+b")
            descriptor = -1
            self.validate()
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            self.close()
            raise

    def validate(self, stream: BinaryIO | None = None) -> None:
        """Recheck an owned or transferred stream before repair or truncation."""
        stream = self.stream if stream is None else stream
        current = _entry_info(self.path, self.directory_fd)
        if stream is None or current is None:
            msg = "Output destination changed before writing."
            raise OSError(msg)
        opened = os.fstat(stream.fileno())
        _validate_regular(opened)
        _validate_regular(current)
        parent = self.path.parent.stat()
        pinned = (
            self.parent_info
            if self.directory_fd is None
            else os.fstat(self.directory_fd)
        )
        if (
            _identity(current) != _identity(self.info)
            or _identity(opened) != _identity(self.info)
            or _identity(parent) != _identity(self.parent_info)
            or _identity(pinned) != _identity(self.parent_info)
        ):
            msg = "Output destination namespace changed before writing."
            raise OSError(msg)

    def detach(self) -> BinaryIO:
        """Validate and transfer the file handle, retaining its namespace guard."""
        self.validate()
        stream = cast("BinaryIO", self.stream)
        self.stream = None
        return stream

    def _close_directory(self) -> None:
        """Release the parent descriptor after validation or on failure."""
        if self.directory_fd is not None:
            descriptor, self.directory_fd = self.directory_fd, None
            os.close(descriptor)

    def close(self) -> None:
        """Release resources without changing any destination bytes."""
        try:
            if self.stream is not None:
                stream, self.stream = self.stream, None
                stream.close()
        finally:
            with suppress(OSError):
                self._close_directory()
