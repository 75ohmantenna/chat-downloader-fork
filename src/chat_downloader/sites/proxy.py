# SPDX-License-Identifier: MIT

"""Proxy resolution and TLS tunnel helpers for non-HTTP site transports."""

from __future__ import annotations

import base64
import socket
import ssl
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import unquote, urlparse

import requests
import socks  # type: ignore[import-untyped]  # PySocks ships without type stubs
from urllib3.util.ssltransport import SSLTransport

from chat_downloader.errors import InvalidParameter

_DEFAULT_PROXY_PORTS = {
    "http": 80,
    "https": 443,
    "socks4": 1080,
    "socks5": 1080,
    "socks5h": 1080,
}
_MAX_CONNECT_RESPONSE_BYTES = 64 * 1024


class _ProxySocket(Protocol):
    """Socket operations used by the Twitch and Kick live transports."""

    def sendall(self, data: bytes) -> None: ...
    def recv(self, buffer_size: int) -> bytes: ...
    def settimeout(self, timeout: float | None) -> None: ...
    def shutdown(self, how: int) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    """Parsed proxy endpoint and optional credentials."""

    scheme: str
    host: str
    port: int
    username: str | None
    password: str | None


def parse_proxy_url(proxy_url: str) -> ProxyConfig:
    """Parse and validate a configured proxy URL."""
    parsed = urlparse(proxy_url)
    scheme = parsed.scheme.lower()
    if scheme not in _DEFAULT_PROXY_PORTS or parsed.hostname is None:
        allowed = ", ".join(sorted(_DEFAULT_PROXY_PORTS))
        msg = f"Invalid proxy URL {proxy_url!r}; expected scheme in {{{allowed}}}"
        raise InvalidParameter(msg)
    try:
        port = parsed.port or _DEFAULT_PROXY_PORTS[scheme]
    except ValueError as error:
        msg = f"Invalid proxy port in {proxy_url!r}"
        raise InvalidParameter(msg) from error
    return ProxyConfig(
        scheme=scheme,
        host=parsed.hostname,
        port=port,
        username=unquote(parsed.username) if parsed.username is not None else None,
        password=unquote(parsed.password) if parsed.password is not None else None,
    )


def resolve_session_proxy(session: object | None, target_url: str) -> str | None:
    """Resolve the effective explicit or environment proxy for ``target_url``."""
    if session is None:
        return None
    configured = dict(getattr(session, "proxies", {}) or {})
    if getattr(session, "trust_env", True):
        environment = requests.utils.get_environ_proxies(target_url)
        environment.update(configured)
        configured = environment
    proxy_url = requests.utils.select_proxy(target_url, configured)
    return proxy_url if isinstance(proxy_url, str) and proxy_url else None


def _target_authority(host: str, port: int) -> str:
    bracketed_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"{bracketed_host}:{port}"


def _open_http_tunnel(
    config: ProxyConfig,
    target_host: str,
    target_port: int,
    timeout: float,
) -> _ProxySocket:
    raw_socket: _ProxySocket = socket.create_connection(
        (config.host, config.port),
        timeout=timeout,
    )
    try:
        if config.scheme == "https":
            raw_socket = cast(
                "_ProxySocket",
                SSLTransport(
                    cast("socket.socket", raw_socket),
                    ssl.create_default_context(),
                    server_hostname=config.host,
                ),
            )
        authority = _target_authority(target_host, target_port)
        headers = [
            f"CONNECT {authority} HTTP/1.1",
            f"Host: {authority}",
            "Proxy-Connection: Keep-Alive",
        ]
        if config.username is not None:
            password = config.password or ""
            credentials = base64.b64encode(
                f"{config.username}:{password}".encode()
            ).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {credentials}")
        raw_socket.sendall(("\r\n".join(headers) + "\r\n\r\n").encode("ascii"))

        _validate_connect_response(raw_socket)
    except BaseException:
        with suppress(OSError):
            raw_socket.close()
        raise
    else:
        return raw_socket


def _validate_connect_response(proxy_socket: _ProxySocket) -> None:
    """Read and validate an HTTP proxy CONNECT response."""
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = proxy_socket.recv(4096)
        if not chunk:
            msg = "Proxy closed the connection before completing CONNECT."
            raise OSError(msg)
        response.extend(chunk)
        if len(response) > _MAX_CONNECT_RESPONSE_BYTES:
            msg = "Proxy CONNECT response exceeded the safety limit."
            raise OSError(msg)
    status_line = bytes(response).split(b"\r\n", 1)[0]
    parts = status_line.split(maxsplit=2)
    if len(parts) < 2 or not parts[1].isdigit():
        msg = "Proxy returned an invalid CONNECT response."
        raise OSError(msg)
    status_code = int(parts[1])
    if status_code // 100 != 2:
        msg = f"Proxy CONNECT failed with HTTP status {status_code}."
        raise OSError(msg)


def _open_socks_tunnel(
    config: ProxyConfig,
    target_host: str,
    target_port: int,
    timeout: float,
) -> _ProxySocket:
    proxy_types = {
        "socks4": socks.SOCKS4,
        "socks5": socks.SOCKS5,
        "socks5h": socks.SOCKS5,
    }
    return cast(
        "_ProxySocket",
        socks.create_connection(
            (target_host, target_port),
            timeout=timeout,
            proxy_type=proxy_types[config.scheme],
            proxy_addr=config.host,
            proxy_port=config.port,
            proxy_rdns=config.scheme == "socks5h",
            proxy_username=config.username,
            proxy_password=config.password,
        ),
    )


def open_proxied_tls_socket(
    target_host: str,
    target_port: int,
    *,
    timeout: float,
    proxy_url: str | None = None,
) -> _ProxySocket:
    """Open a TLS socket to a target, optionally through the configured proxy."""
    config = parse_proxy_url(proxy_url) if proxy_url else None
    if config is None:
        tunnel: _ProxySocket = socket.create_connection(
            (target_host, target_port),
            timeout=timeout,
        )
    elif config.scheme in {"http", "https"}:
        tunnel = _open_http_tunnel(config, target_host, target_port, timeout)
    else:
        tunnel = _open_socks_tunnel(config, target_host, target_port, timeout)

    try:
        context = ssl.create_default_context()
        if config is not None and config.scheme == "https":
            return cast(
                "_ProxySocket",
                SSLTransport(
                    tunnel,  # type: ignore[arg-type]  # SSLTransport supports TLS-in-TLS
                    context,
                    server_hostname=target_host,
                ),
            )
        return cast(
            "_ProxySocket",
            context.wrap_socket(
                cast("socket.socket", tunnel),
                server_hostname=target_host,
            ),
        )
    except BaseException:
        with suppress(OSError):
            tunnel.close()
        raise
