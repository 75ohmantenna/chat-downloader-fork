# SPDX-License-Identifier: MIT

"""Output path templates reject unsafe fields before provider requests."""

from __future__ import annotations

import pytest

from chat_downloader.errors import InvalidParameter
from chat_downloader.models import ChatRequest
from chat_downloader.output.continuous_write import ContinuousWriter
from chat_downloader.sites.models import Chat


@pytest.mark.parametrize(
    "path",
    [
        "chat_{date}.txt",
        "chat_{}.txt",
        "chat_{0}.txt",
        "chat_{title!r}.txt",
        "chat_{title:>1000000000}.txt",
        "chat_{title.name}.txt",
        "chat_{title.txt",
    ],
)
def test_invalid_output_template_is_rejected_at_request_boundary(path: str) -> None:
    with pytest.raises(InvalidParameter, match=r"output path|Output paths"):
        ChatRequest(output=path)


def test_literal_braces_and_supported_fields_expand(tmp_path) -> None:
    path = str(tmp_path / "{{archive}}_{title}_{id}.txt")
    ChatRequest(output=path)
    with Chat(iter([{}]), title="Example", id="123") as chat:
        chat.set_formatter(lambda _: "hello")
        chat.attach_writer(ContinuousWriter(path, lazy_initialise=True))
        assert list(chat) == [{}]
    assert (tmp_path / "{archive}_Example_123.txt").read_text() == "hello\n"
