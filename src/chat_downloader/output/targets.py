# SPDX-License-Identifier: MIT

"""Output target selection, template provenance, and filename observation."""

from __future__ import annotations

from pathlib import Path
from string import Formatter
from typing import Any, Protocol

from chat_downloader.debugging import log
from chat_downloader.output._secure_file import _check_output_path
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.utils.filename_utils import (
    output_path_identity,
    sanitize_filename_component,
    validate_output_template,
)


class ChatOutputWriter(Protocol):
    """Structural writer interface used by the chat output dispatcher."""

    @property
    def file_name(self) -> str | None:
        """Return the configured name, which may await lazy configuration."""
        ...  # pragma: no cover — structural typing declaration

    @file_name.setter
    def file_name(self, value: str) -> None:
        """Set the resolved output path before opening the writer."""
        ...  # pragma: no cover — structural typing declaration

    @property
    def output_mode(self) -> str:
        """Return the writer's resolved file format."""
        ...  # pragma: no cover — structural typing declaration

    def is_initialised(self) -> bool:
        """Return True if the writer has been initialized."""
        ...  # pragma: no cover — structural typing declaration

    def initialize(self) -> None:
        """Initialize the writer, creating files and resources as needed."""
        ...  # pragma: no cover — structural typing declaration

    def write(self, item: dict[str, Any] | str, *, flush: bool = False) -> None:
        """Write a chat item to the output target."""
        ...  # pragma: no cover — structural typing declaration

    def close(self) -> None:
        """Close the writer and release any held resources."""
        ...  # pragma: no cover — structural typing declaration


def _expand_output_file_name(
    file_name: str,
    *,
    title: str | None,
    video_id: str | None,
) -> str:
    """Expand output placeholders with safe single-component metadata."""
    validate_output_template(file_name)
    safe_title = sanitize_filename_component(title).replace("..", "_")
    safe_id = sanitize_filename_component(video_id).replace("..", "_")
    return file_name.format(title=safe_title, id=safe_id)


def _register_output_path(
    file_name: str,
    identity: tuple[object, ...],
    seen: dict[tuple[object, ...], str],
) -> bool:
    """Register one captured identity for both alias grouping and policy."""
    suffix = file_name.rsplit(".", 1)[-1].lower()
    if identity in seen:
        if seen[identity] != suffix:
            msg = "Different output formats require distinct files."
            raise ValueError(msg)
        log("warning", f"Duplicate output path '{file_name}' — skipping.")
        return False
    seen[identity] = suffix
    return True


class _OutputTargets:
    """Keep selected writers and their template state behind one interface."""

    def __init__(self) -> None:
        self.writers: list[ChatOutputWriter] = []
        self._attached: set[int] = set()
        self._resolved: set[int] = set()
        self._generated: set[int] = set()

    def attach(self, writer: ChatOutputWriter) -> bool:
        """Retain each writer object once, including already opened writers."""
        if id(writer) in self._attached:
            return False
        self._attached.add(id(writer))
        self.writers.append(writer)
        return True

    @staticmethod
    def name(writer: ChatOutputWriter) -> str:
        """Require a configured name without assuming writer initialization."""
        if writer.file_name is None:
            msg = "File name not set"
            raise ValueError(msg)
        return writer.file_name

    def _fields(self, writer: ChatOutputWriter) -> set[str]:
        if writer.is_initialised() or id(writer) in self._resolved:
            return set()
        return {
            field
            for _literal, field, _spec, _conversion in Formatter().parse(
                self.name(writer)
            )
            if field is not None
        }

    def _names(self, title: str | None, video_id: str | None) -> list[str]:
        return [
            self.name(writer)
            if writer.is_initialised() or id(writer) in self._resolved
            else _expand_output_file_name(
                self.name(writer), title=title, video_id=video_id
            )
            for writer in self.writers
        ]

    def paths(self, *, title: str | None, video_id: str | None) -> list[Path]:
        """Observe names without freezing metadata or re-expanding final paths."""
        return [Path(name) for name in self._names(title, video_id)]

    def _identities(
        self,
        paths: list[str],
        *,
        title: str | None,
        video_id: str | None,
        provisional: bool,
        metadata_pending: bool,
    ) -> dict[int, tuple[object, ...]]:
        """Snapshot identity and provenance together for alias selection."""
        identities: dict[int, tuple[object, ...]] = {}
        for writer, path in zip(self.writers, paths, strict=True):
            fields = self._fields(writer)
            unresolved = provisional and (
                ("title" in fields and (title is None or metadata_pending))
                or ("id" in fields and (video_id is None or metadata_pending))
            )
            identities[id(writer)] = (
                ("template", self.name(writer))
                if unresolved
                else output_path_identity(path)
            )
            if (
                not writer.is_initialised()
                and id(writer) not in self._resolved
                and (
                    fields
                    or (
                        isinstance(writer, ContinuousWriter)
                        and writer._require_new_file
                    )
                )
            ):
                self._generated.add(id(writer))
        return identities

    def resolve(
        self,
        *,
        title: str | None,
        video_id: str | None,
        provisional: bool = False,
        metadata_pending: bool = False,
    ) -> None:
        """Select aliases now, committing names and preflight only when final."""
        if all(writer.is_initialised() for writer in self.writers):
            return
        paths = self._names(title, video_id)
        identities = self._identities(
            paths,
            title=title,
            video_id=video_id,
            provisional=provisional,
            metadata_pending=metadata_pending,
        )
        generated = {identities[key] for key in self._generated if key in identities}
        seen: dict[tuple[object, ...], str] = {}
        selected = []
        for writer, path in zip(self.writers, paths, strict=True):
            unique = _register_output_path(path, identities[id(writer)], seen)
            if writer.is_initialised() or unique:
                selected.append((writer, path))
        if not provisional:
            for writer, path in zip(self.writers, paths, strict=True):
                if not writer.is_initialised() and isinstance(writer, ContinuousWriter):
                    _check_output_path(path, exclusive=id(writer) in self._generated)
        self.writers[:] = [writer for writer, _path in selected]
        for writer, path in selected:
            if not provisional:
                writer.file_name = path
                self._resolved.add(id(writer))
            if isinstance(writer, ContinuousWriter):
                writer._require_new_file |= identities[id(writer)] in generated
