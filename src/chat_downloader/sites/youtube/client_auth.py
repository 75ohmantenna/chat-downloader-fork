# SPDX-License-Identifier: MIT

"""Authentication helpers for the YouTube client."""

import contextlib
import hashlib
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode


def _initialize_pref(session: Any) -> None:
    """Merge hl=en and tz=UTC into the YouTube PREF cookie.

    Reads the existing PREF cookie (if any), merges the two fields without
    clobbering other user preferences, and writes it back.  This prevents
    language or timezone mismatches when the user has a non-English PREF cookie
    in their browser-exported cookie file.
    """
    pref_cookie = session.get_cookie_value("PREF")
    pref: dict[str, str] = {}
    if pref_cookie:
        with contextlib.suppress(ValueError):
            pref = dict(parse_qsl(pref_cookie))
    pref.update({"hl": "en", "tz": "UTC"})
    session.set_cookie_value(".youtube.com", "PREF", urlencode(pref))


def _initialize_consent(session: Any) -> None:
    """Initialize YouTube consent cookies."""
    if session.get_cookie_value("__Secure-3PSID"):
        return
    socs = session.get_cookie_value("SOCS")
    if socs and not socs.startswith("CAA"):  # not consented
        return
    session.set_cookie_value(".youtube.com", "SOCS", "CAI", secure=True)


def _get_sid_cookies(session: Any) -> tuple[str | None, str | None, str | None]:
    """Extract SAPISID cookie variants from the session."""
    yt_sapisid = session.get_cookie_value("SAPISID")
    yt_1psapisid = session.get_cookie_value("__Secure-1PAPISID")
    yt_3psapisid = session.get_cookie_value("__Secure-3PAPISID")
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
        auth_parts.append("".join(additional_parts.values()))

    return f"{scheme} {'_'.join(auth_parts)}"


def _generate_sapisidhash_header(
    session: Any,
    yt_home: str,
    ytcfg: dict[str, Any] | None = None,
) -> str | None:
    """Generate SAPISIDHASH authorization header for API requests."""
    yt_sapisid, yt_1psapisid, yt_3psapisid = _get_sid_cookies(session)

    if not any([yt_sapisid, yt_1psapisid, yt_3psapisid]):
        return None

    time_now = round(time.time())

    if not yt_sapisid:
        sapisid_value = yt_3psapisid or yt_1psapisid
        if sapisid_value:
            session.set_cookie_value(
                ".youtube.com",
                "SAPISID",
                sapisid_value,
                secure=True,
                expire_time=time_now + 3600,
            )
            yt_sapisid = sapisid_value

    additional_parts = None
    if ytcfg:
        datasync_id = ytcfg.get("DATASYNC_ID")
        if datasync_id:
            _, user_session_id = _parse_data_sync_id(datasync_id)
            if user_session_id:
                additional_parts = {"session_id": user_session_id}

    authorizations = []
    for scheme, sid in (
        ("SAPISIDHASH", yt_sapisid),
        ("SAPISID1PHASH", yt_1psapisid),
        ("SAPISID3PHASH", yt_3psapisid),
    ):
        if sid:
            authorizations.append(
                _make_sid_authorization(
                    scheme,
                    sid,
                    yt_home,
                    time_now,
                    additional_parts,
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
