# SPDX-License-Identifier: MIT

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import pathlib


import requests

_NETWORK_SCOPES = {"network_replay", "network_live", "network_environment"}


@pytest.fixture
def tmp_dir(tmp_path: pathlib.Path) -> str:
    return str(tmp_path)


@pytest.fixture
def jsonl_path(tmp_path: pathlib.Path) -> str:
    return str(tmp_path / "test.jsonl")


@pytest.fixture
def make_fake_http_response():
    def factory(status_code: int, payload: Any, text: str = "") -> Any:
        class _Resp:
            def __init__(self) -> None:
                self.status_code = status_code
                self._payload = payload
                self.text = text
                self.ok = 200 <= status_code < 300

            def json(self):
                return self._payload

            def raise_for_status(self) -> None:
                if not self.ok:
                    msg = f"HTTP {self.status_code} error"
                    raise requests.HTTPError(msg)

        return _Resp()

    return factory


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "network: test requires outbound access to a supported provider",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Run tests marked 'network' (skipped by default; "
        "pass this flag to enable)",
    )


def _check_network_marks(item: pytest.Item) -> None:
    scopes = [mark for mark in item.iter_markers() if mark.name in _NETWORK_SCOPES]
    network = item.get_closest_marker("network") is not None
    if not network and not scopes:
        return
    if not network or len(scopes) != 1:
        msg = f"{item.nodeid} must have the network mark and exactly one network scope."
        raise pytest.UsageError(msg)

    timeout = item.get_closest_marker("timeout")
    seconds = None
    if timeout is not None:
        seconds = timeout.args[0] if timeout.args else timeout.kwargs.get("timeout")
    if type(seconds) not in (int, float) or not (0 < seconds < math.inf):
        msg = f"{item.nodeid} must have a positive, finite network test timeout."
        raise pytest.UsageError(msg)


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    skip_network = pytest.mark.skip(
        reason="network tests are opt-in: pass --run-network to enable",
    )
    run_network = config.getoption("--run-network")
    for item in items:
        _check_network_marks(item)
        if not run_network and item.get_closest_marker("network") is not None:
            item.add_marker(skip_network)
