# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
import socks

from chat_downloader.errors import InvalidParameter
from chat_downloader.sites import proxy


class _ConnectResponseSocket:
    """Socket fake that records a CONNECT request and returns success."""

    def __init__(self, responses: list[bytes] | None = None) -> None:
        self.sent = b""
        self.closed = False
        self.responses = responses or [b"HTTP/1.1 200 Connection established\r\n\r\n"]

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _buffer_size: int) -> bytes:
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def test_resolve_session_proxy_prefers_explicit_proxy(
    monkeypatch: Any,
) -> None:
    session = MagicMock()
    session.proxies = {"https": "socks5h://explicit.test:1080"}
    session.trust_env = True
    monkeypatch.setattr(
        proxy.requests.utils,
        "get_environ_proxies",
        lambda _url: {"https": "http://environment.test:8080"},
    )

    assert (
        proxy.resolve_session_proxy(session, "https://target.test")
        == "socks5h://explicit.test:1080"
    )


def test_parse_proxy_url_preserves_encoded_credentials() -> None:
    config = proxy.parse_proxy_url("https://user%40example.test:p%3Ass@proxy.test")

    assert config.scheme == "https"
    assert config.host == "proxy.test"
    assert config.port == 443
    assert config.username == "user@example.test"
    assert config.password == "p:ss"  # noqa: S105 — synthetic proxy credential


@pytest.mark.parametrize(
    "proxy_url",
    ["ftp://proxy.test", "http://proxy.test:not-a-port"],
)
def test_parse_proxy_url_rejects_invalid_values(proxy_url: str) -> None:
    with pytest.raises(InvalidParameter):
        proxy.parse_proxy_url(proxy_url)


def test_http_tunnel_sends_basic_proxy_authorization(monkeypatch: Any) -> None:
    raw_socket = _ConnectResponseSocket()
    monkeypatch.setattr(
        proxy.socket,
        "create_connection",
        lambda _address, timeout: raw_socket,
    )

    result = proxy._open_http_tunnel(
        proxy.parse_proxy_url("http://user:pass@proxy.test:8080"),
        "target.test",
        443,
        5.0,
    )

    assert result is raw_socket
    assert b"CONNECT target.test:443 HTTP/1.1" in raw_socket.sent
    assert b"Proxy-Authorization: Basic dXNlcjpwYXNz" in raw_socket.sent


def test_https_proxy_wraps_proxy_connection_before_connect(monkeypatch: Any) -> None:
    raw_socket = _ConnectResponseSocket()
    tls_context = MagicMock()
    ssl_transport = MagicMock(return_value=raw_socket)
    monkeypatch.setattr(
        proxy.socket,
        "create_connection",
        lambda _address, timeout: raw_socket,
    )
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda: tls_context)
    monkeypatch.setattr(proxy, "SSLTransport", ssl_transport)

    result = proxy._open_http_tunnel(
        proxy.parse_proxy_url("https://proxy.test"),
        "target.test",
        443,
        5.0,
    )

    assert result is raw_socket
    ssl_transport.assert_called_once_with(
        raw_socket,
        tls_context,
        server_hostname="proxy.test",
    )


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (b"", "before completing"),
        (b"not-http\r\n\r\n", "invalid CONNECT"),
        (b"HTTP/1.1 407 denied\r\n\r\n", "HTTP status 407"),
        (b"x" * (proxy._MAX_CONNECT_RESPONSE_BYTES + 1), "safety limit"),
    ],
)
def test_validate_connect_response_rejects_proxy_failures(
    response: bytes,
    message: str,
) -> None:
    proxy_socket = _ConnectResponseSocket([response])

    with pytest.raises(OSError, match=message):
        proxy._validate_connect_response(proxy_socket)


def test_http_tunnel_closes_socket_when_connect_fails(monkeypatch: Any) -> None:
    raw_socket = _ConnectResponseSocket([b""])
    monkeypatch.setattr(
        proxy.socket,
        "create_connection",
        lambda _address, timeout: raw_socket,
    )

    with pytest.raises(OSError):
        proxy._open_http_tunnel(
            proxy.parse_proxy_url("http://proxy.test"),
            "target.test",
            443,
            5.0,
        )

    assert raw_socket.closed is True


def test_socks_tunnel_preserves_dns_and_credentials(monkeypatch: Any) -> None:
    connected = MagicMock()
    create_connection = MagicMock(return_value=connected)
    monkeypatch.setattr(proxy.socks, "create_connection", create_connection)

    result = proxy._open_socks_tunnel(
        proxy.parse_proxy_url("socks5h://user:pass@proxy.test:1080"),
        "target.test",
        443,
        6.0,
    )

    assert result is connected
    create_connection.assert_called_once_with(
        ("target.test", 443),
        timeout=6.0,
        proxy_type=socks.SOCKS5,
        proxy_addr="proxy.test",
        proxy_port=1080,
        proxy_rdns=True,
        proxy_username="user",
        proxy_password="pass",  # noqa: S106 — synthetic proxy credential
    )


@pytest.mark.parametrize(
    ("proxy_url", "tunnel_helper"),
    [
        ("http://proxy.test", "_open_http_tunnel"),
        ("socks5://proxy.test", "_open_socks_tunnel"),
    ],
)
def test_open_proxied_tls_socket_routes_through_configured_tunnel(
    monkeypatch: Any,
    proxy_url: str,
    tunnel_helper: str,
) -> None:
    tunnel = MagicMock()
    wrapped = MagicMock()
    context = MagicMock()
    context.wrap_socket.return_value = wrapped
    helper = MagicMock(return_value=tunnel)
    monkeypatch.setattr(proxy, tunnel_helper, helper)
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda: context)

    result = proxy.open_proxied_tls_socket(
        "target.test",
        443,
        timeout=7.0,
        proxy_url=proxy_url,
    )

    assert result is wrapped
    helper.assert_called_once()
    context.wrap_socket.assert_called_once_with(
        tunnel,
        server_hostname="target.test",
    )


def test_open_proxied_tls_socket_connects_directly_without_proxy(
    monkeypatch: Any,
) -> None:
    tunnel = MagicMock()
    wrapped = MagicMock()
    context = MagicMock()
    context.wrap_socket.return_value = wrapped
    connect = MagicMock(return_value=tunnel)
    monkeypatch.setattr(proxy.socket, "create_connection", connect)
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda: context)

    result = proxy.open_proxied_tls_socket(
        "target.test",
        443,
        timeout=8.0,
    )

    assert result is wrapped
    connect.assert_called_once_with(("target.test", 443), timeout=8.0)


def test_open_proxied_tls_socket_supports_tls_in_tls(monkeypatch: Any) -> None:
    tunnel = MagicMock()
    wrapped = MagicMock()
    context = MagicMock()
    monkeypatch.setattr(proxy, "_open_http_tunnel", MagicMock(return_value=tunnel))
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda: context)
    monkeypatch.setattr(proxy, "SSLTransport", MagicMock(return_value=wrapped))

    result = proxy.open_proxied_tls_socket(
        "target.test",
        443,
        timeout=9.0,
        proxy_url="https://proxy.test",
    )

    assert result is wrapped
    proxy.SSLTransport.assert_called_once_with(
        tunnel,
        context,
        server_hostname="target.test",
    )


def test_open_proxied_tls_socket_closes_tunnel_on_tls_failure(
    monkeypatch: Any,
) -> None:
    tunnel = MagicMock()
    context = MagicMock()
    context.wrap_socket.side_effect = OSError("TLS failed")
    monkeypatch.setattr(
        proxy.socket, "create_connection", MagicMock(return_value=tunnel)
    )
    monkeypatch.setattr(proxy.ssl, "create_default_context", lambda: context)

    with pytest.raises(OSError, match="TLS failed"):
        proxy.open_proxied_tls_socket(
            "target.test",
            443,
            timeout=8.0,
        )

    tunnel.close.assert_called_once_with()
