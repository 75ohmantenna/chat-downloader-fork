# SPDX-License-Identifier: MIT

"""Authentication helpers for the YouTube client."""

from __future__ import annotations

import contextlib
import hashlib
import time
from copy import copy
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode

from chat_downloader.sites.session import get_scoped_cookie
from chat_downloader.utils.json_types import get_str

if TYPE_CHECKING:
    from http.cookiejar import Cookie

    from chat_downloader.sites.base import BaseChatDownloader
    from chat_downloader.utils.json_types import JSONDict

from .constants_patterns import (
    _YT_DOMAIN,
    _YT_SAPISID_EXPIRE_SECONDS,
    _YT_SOCS_CONSENTED_PREFIX,
    _YT_SOCS_INIT_VALUE,
)

_AUTH_COOKIE_DOMAINS = frozenset({"youtube.com", "www.youtube.com"})
_AUTH_COOKIE_URL = "https://www.youtube.com/"


def _get_youtube_cookie(session: BaseChatDownloader, name: str) -> Cookie | None:
    """Read only cookies applicable to YouTube's canonical HTTPS origin."""
    return get_scoped_cookie(
        session.session.cookies, name, _AUTH_COOKIE_URL, _AUTH_COOKIE_DOMAINS
    )


def _get_youtube_cookie_value(session: BaseChatDownloader, name: str) -> str | None:
    """Return a scoped value for authentication and preference promotion."""
    cookie = _get_youtube_cookie(session, name)
    return cookie.value if cookie is not None else None


def _initialize_pref(session: Any) -> None:
    """Merge hl=en and tz=UTC into the YouTube PREF cookie.

    Reads the existing PREF cookie (if any), merges the two fields without
    clobbering other user preferences, and writes it back.  This prevents
    language or timezone mismatches when the user has a non-English PREF cookie
    in their browser-exported cookie file.
    """
    pref_cookie = _get_youtube_cookie_value(session, "PREF")
    pref: dict[str, str] = {}
    if pref_cookie:
        with contextlib.suppress(ValueError):
            pref = dict(parse_qsl(pref_cookie))
    pref.update({"hl": "en", "tz": "UTC"})
    session.set_cookie_value(_YT_DOMAIN, "PREF", urlencode(pref))


def _initialize_consent(session: Any) -> None:
    """Initialize YouTube consent cookies."""
    if _get_youtube_cookie_value(session, "__Secure-3PSID"):
        return
    socs = _get_youtube_cookie_value(session, "SOCS")
    if socs and not socs.startswith(_YT_SOCS_CONSENTED_PREFIX):  # not consented
        return
    session.set_cookie_value(_YT_DOMAIN, "SOCS", _YT_SOCS_INIT_VALUE, secure=True)


def _get_sid_cookies(session: Any) -> tuple[str | None, str | None, str | None]:
    """Extract SAPISID cookie variants from the session."""
    yt_sapisid = _get_youtube_cookie_value(session, "SAPISID")
    yt_1psapisid = _get_youtube_cookie_value(session, "__Secure-1PAPISID")
    yt_3psapisid = _get_youtube_cookie_value(session, "__Secure-3PAPISID")
    return yt_sapisid, yt_1psapisid, yt_3psapisid


def _parse_data_sync_id(
    data_sync_id: str | None,
) -> tuple[str | None, str | None]:
    """Parse YouTube DATASYNC_ID into delegated and user session IDs."""
    if not data_sync_id:
        return None, None
    first, _, second = data_sync_id.partition("||")
    if second:
        return first, second
    return None, first


def _make_sid_authorization(
    scheme: str,
    sid: str,
    origin: str,
    time_now: int,
    additional_parts: dict[str, str] | None = None,
) -> str:
    """Generate a single SAPISID authorization token."""
    hash_parts = []
    if additional_parts:
        hash_parts.append(":".join(additional_parts.values()))
    hash_parts.extend([str(time_now), sid, origin])

    # YouTube's SAPISIDHASH scheme is defined in terms of SHA-1.
    sidhash = hashlib.sha1(
        " ".join(hash_parts).encode("utf-8"), usedforsecurity=False
    ).hexdigest()

    auth_parts = [str(time_now), sidhash]
    if additional_parts:
        auth_parts.append("".join(additional_parts))

    return f"{scheme} {'_'.join(auth_parts)}"


def _ensure_primary_sapisid(
    session: Any,
    sids: tuple[str | None, str | None, str | None],
    time_now: int,
) -> str | None:
    """Promote a 1P/3P SID to SAPISID when the primary cookie is absent."""
    yt_sapisid, yt_1psapisid, yt_3psapisid = sids
    if yt_sapisid:
        return yt_sapisid
    sapisid_value = yt_3psapisid or yt_1psapisid
    source_name = "__Secure-3PAPISID" if yt_3psapisid else "__Secure-1PAPISID"
    source = _get_youtube_cookie(session, source_name)
    if sapisid_value and source is not None:
        expiry = time_now + _YT_SAPISID_EXPIRE_SECONDS
        if source.expires is not None:
            expiry = min(expiry, source.expires)
        promoted = copy(source)
        promoted.name = "SAPISID"
        promoted.secure = True
        promoted.expires = expiry
        session.session.cookies.set_cookie(promoted)
    return sapisid_value


def _session_id_parts(ytcfg: JSONDict | None) -> dict[str, str] | None:
    """Return session-id additional_parts from ytcfg, or None."""
    if not ytcfg:
        return None
    explicit_user_session_id = get_str(ytcfg, "USER_SESSION_ID")
    if explicit_user_session_id:
        return {"u": explicit_user_session_id}
    datasync_id = get_str(ytcfg, "DATASYNC_ID")
    if not datasync_id:
        return None
    _, user_session_id = _parse_data_sync_id(datasync_id)
    return {"u": user_session_id} if user_session_id else None


def _generate_sapisidhash_header(
    session: Any,
    yt_home: str,
    ytcfg: JSONDict | None = None,
) -> str | None:
    """Generate SAPISIDHASH authorization header for API requests."""
    sids = _get_sid_cookies(session)
    if not any(sids):
        return None

    time_now = round(time.time())
    yt_sapisid = _ensure_primary_sapisid(session, sids, time_now)
    _, yt_1psapisid, yt_3psapisid = sids
    additional_parts = _session_id_parts(ytcfg)

    authorizations = []
    for scheme, sid in (
        ("SAPISIDHASH", yt_sapisid),
        ("SAPISID1PHASH", yt_1psapisid),
        ("SAPISID3PHASH", yt_3psapisid),
    ):
        if sid:
            authorizations.append(
                _make_sid_authorization(
                    scheme, sid, yt_home, time_now, additional_parts
                ),
            )

    return " ".join(authorizations) if authorizations else None


__all__ = [
    "_generate_sapisidhash_header",
    "_get_sid_cookies",
    "_initialize_consent",
    "_initialize_pref",
    "_make_sid_authorization",
    "_parse_data_sync_id",
]
