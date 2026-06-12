# SPDX-License-Identifier: MIT

from __future__ import annotations

import select
import socket
import socketserver
import threading
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    import pathlib


class _ConnectProxy(socketserver.StreamRequestHandler):
    """Minimal HTTP CONNECT-only proxy for testing."""

    def handle(self) -> None:
        line = self.rfile.readline().decode()
        if not line.upper().startswith("CONNECT "):
            return
        host_port = line.split()[1]
        host, _, port_str = host_port.rpartition(":")
        port = int(port_str) if port_str else 443
        # Drain request headers
        while self.rfile.readline().strip():
            pass
        try:
            remote = socket.create_connection((host, port), timeout=10)
        except OSError:
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
            return
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        self.wfile.flush()
        fds = [self.connection, remote]
        try:
            while True:
                readable, _, _ = select.select(fds, [], fds, 30)
                if not readable:
                    break
                for src in readable:
                    dst = remote if src is self.connection else self.connection
                    data = src.recv(65536)
                    if not data:
                        return
                    dst.sendall(data)
        finally:
            remote.close()


@pytest.fixture(scope="session")
def local_http_proxy() -> str:
    """Spin up a local CONNECT proxy on a free port and return its URL."""
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _ConnectProxy)
    server.daemon_threads = True
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


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

            def json(self):
                return self._payload

        return _Resp()

    return factory


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "network: test requires outbound network access (YouTube/Twitch integration)",
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="Run tests marked 'network' (skipped by default; "
        "pass this flag to enable)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if config.getoption("--run-network"):
        return

    skip_network = pytest.mark.skip(
        reason="network tests are opt-in: pass --run-network to enable",
    )
    for item in items:
        if item.get_closest_marker("network") is not None:
            item.add_marker(skip_network)
