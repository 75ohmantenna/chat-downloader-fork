# SPDX-License-Identifier: MIT

"""Context and header helpers for YouTube requests."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from chat_downloader.request_profiles import (
    get_request_profile_innertube_context,
)
from chat_downloader.utils.dict_utils import multi_get
from chat_downloader.utils.json_types import get_str

if TYPE_CHECKING:
    from chat_downloader.utils.json_types import JSONDict

from .client_auth import _parse_data_sync_id


def _extract_account_syncid(ytcfg: JSONDict) -> str | None:
    """Extract account sync ID from YouTube config."""
    datasync_id = get_str(ytcfg, "DATASYNC_ID")
    if datasync_id:
        delegated_session_id, _ = _parse_data_sync_id(datasync_id)
        if delegated_session_id:
            return delegated_session_id
    return get_str(ytcfg, "DELEGATED_SESSION_ID") or None


def _generate_headers(
    ytcfg: JSONDict,
    session: Any,
    yt_home: str,
    sapisidhash_generator: Any,
) -> dict[str, str]:
    """Generate headers for YouTube API requests."""
    headers = {
        "origin": yt_home,
        "x-youtube-client-name": str(ytcfg.get("INNERTUBE_CONTEXT_CLIENT_NAME")),
        "x-youtube-client-version": str(ytcfg.get("INNERTUBE_CLIENT_VERSION")),
        "x-origin": yt_home,
        "x-goog-authuser": "0",
    }

    identity_token = get_str(ytcfg, "ID_TOKEN")
    if identity_token:
        headers["x-youtube-identity-token"] = identity_token

    account_syncid = _extract_account_syncid(ytcfg)
    if account_syncid:
        headers["x-goog-pageid"] = account_syncid

    session_index = ytcfg.get("SESSION_INDEX")
    if account_syncid or session_index:
        headers["x-goog-authuser"] = str(session_index or 0)

    visitor_data = multi_get(ytcfg, "INNERTUBE_CONTEXT", "client", "visitorData")
    if visitor_data:
        headers["x-goog-visitor-id"] = visitor_data

    user_agent = multi_get(ytcfg, "INNERTUBE_CONTEXT", "client", "userAgent")
    if user_agent:
        headers["user-agent"] = str(user_agent)

    if ytcfg.get("LOGGED_IN"):
        headers["x-youtube-bootstrap-logged-in"] = "true"

    auth = sapisidhash_generator(session, yt_home, ytcfg)
    if auth:
        headers["authorization"] = auth

    return headers


def _get_innertube_context(ytcfg: JSONDict) -> dict[str, Any]:
    """Return normalized InTube context."""
    context = copy.deepcopy(ytcfg.get("INNERTUBE_CONTEXT") or {})
    if not isinstance(context, dict):
        return {}

    client = context.get("client")
    if not isinstance(client, dict):
        client = {}
        context["client"] = client

    client.update({"hl": "en", "timeZone": "UTC", "utcOffsetMinutes": 0})
    return context


def apply_request_profile_to_innertube_context(
    context: dict[str, Any],
    profile_name: object,
) -> dict[str, Any]:
    """Apply request-profile client fields to a copied Innertube context."""
    profile_context = get_request_profile_innertube_context(profile_name)
    profile_client = profile_context.get("client")
    if not isinstance(profile_client, dict):
        return context

    updated_context = copy.deepcopy(context)
    client = updated_context.get("client")
    if not isinstance(client, dict):
        client = {}
        updated_context["client"] = client
    client.update(profile_client)
    client.update({"hl": "en", "timeZone": "UTC", "utcOffsetMinutes": 0})
    return updated_context
