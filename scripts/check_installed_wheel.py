# SPDX-License-Identifier: MIT

"""Check resources in the installed wheel used by make smoke."""

from __future__ import annotations

from importlib.resources import files

from chat_downloader.formatting.format import ItemFormatter

if __name__ == "__main__":
    if not files("chat_downloader").joinpath("py.typed").is_file():
        msg = "Installed wheel is missing py.typed"
        raise SystemExit(msg)

    rendered = ItemFormatter().format(
        {"author": {"name": "Example"}, "message": "hello"}
    )
    if rendered != "Example: hello":
        msg = f"Unexpected default formatting: {rendered!r}"
        raise SystemExit(msg)
