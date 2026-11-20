# SPDX-License-Identifier: MIT

"""Load UTF-8 Netscape cookie files without exposing malformed rows."""

from __future__ import annotations

import re
from http.cookiejar import Cookie
from pathlib import Path
from typing import TYPE_CHECKING

from chat_downloader.errors import CookieError

if TYPE_CHECKING:
    from http.cookiejar import MozillaCookieJar

_NETSCAPE_HEADER = re.compile(r"#( Netscape)? HTTP Cookie File")
_HTTP_ONLY_PREFIX = "#HttpOnly_"
_INVALID_COOKIE_FILE = "Could not load the cookie file. Expected UTF-8 Netscape format."


def _parse_cookie_row(line: str) -> Cookie | None:
    http_only = line.startswith(_HTTP_ONLY_PREFIX)
    if http_only:
        line = line.removeprefix(_HTTP_ONLY_PREFIX)
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "$")):
        return None

    domain, include_subdomains, path, secure, expires, name, value = line.rstrip(
        "\n"
    ).split("\t")
    domain_specified = include_subdomains == "TRUE"
    initial_dot = domain.startswith(".")
    if domain_specified != initial_dot:
        msg = "Cookie domain does not match its subdomain flag."
        raise ValueError(msg)

    return Cookie(
        version=0,
        name=name or value,
        value=value if name else None,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=domain_specified,
        domain_initial_dot=initial_dot,
        path=path,
        path_specified=False,
        secure=secure == "TRUE",
        expires=float(expires) if expires else None,
        discard=not expires,
        comment=None,
        comment_url=None,
        rest={"HTTPOnly": ""} if http_only else {},
    )


def load_cookie_file(cookie_jar: MozillaCookieJar, filename: str) -> None:
    """Load session and expired cookies as well as persistent, active cookies."""
    try:
        with Path(filename).open(encoding="utf-8") as cookie_file:
            if not _NETSCAPE_HEADER.match(cookie_file.readline()):
                raise CookieError(_INVALID_COOKIE_FILE)
            for line in cookie_file:
                if (cookie := _parse_cookie_row(line)) is not None:
                    cookie_jar.set_cookie(cookie)
    except FileNotFoundError:
        message = f'The file "{filename}" could not be found.'
    except (ValueError, OverflowError):
        message = _INVALID_COOKIE_FILE
    else:
        return
    # Raise after the handler to keep parser details out of the exception chain.
    raise CookieError(message)
