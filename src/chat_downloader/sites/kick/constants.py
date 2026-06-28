# SPDX-License-Identifier: MIT

"""Kick chat downloader constants.

URL patterns, API endpoints, Pusher configuration, message-type/group
definitions, emote parsing patterns, and Cloudflare/challenge markers for the
Kick site.

All values reflect Kick's *current* public, unauthenticated web/API behavior
and may break if Kick changes endpoints, the Pusher application key, websocket
details, or event payloads.
"""

from __future__ import annotations

import contextlib
import re

# ── HTTP API ──────────────────────────────────────────────────────────────────

#: Base site URL.
BASE_URL = "https://kick.com"

#: Channel metadata endpoint, formatted with a channel username/slug.
CHANNEL_API_TEMPLATE = "https://kick.com/api/v2/channels/{username}"

#: Preloaded (recent) messages endpoint, formatted with a numeric channel id.
MESSAGES_API_TEMPLATE = "https://kick.com/api/v2/channels/{channel_id}/messages"

# ── Pusher websocket ──────────────────────────────────────────────────────────

#: Default Pusher application key compiled into Kick's JS bundle.
#: This is not a secret; it is shipped in Kick's public JavaScript bundle and
#: grants only anonymous, read-only subscription to public chatroom channels.
_PUSHER_DEFAULT_KEY = "32cbd69e4b950bf97679"

#: Cached dynamically-discovered Pusher key (None = not yet discovered).
_PUSHER_DISCOVERED_KEY: str | None = None


def _is_kick_origin(url: str) -> bool:
    """Return True if *url* is an HTTPS URL on the ``kick.com`` domain.

    Used to constrain which script URLs the Pusher-key discovery loop will
    fetch, so a tampered homepage cannot redirect it to an arbitrary host.
    """
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host == "kick.com" or host.endswith(".kick.com")


def resolve_pusher_key(
    *, force_discover: bool = False
) -> str:  # pragma: no cover — network-dependent; tested at integration level
    """Return the current Pusher application key, discovering it if needed.

    The key lives in Kick's Next.js JS bundle as ``NEXT_PUBLIC_PUSHER_KEY``.
    It is stable across page loads but can change when Kick rebuilds their
    frontend. This function fetches the homepage on first call to extract the
    current value, falling back to the compiled-in default if discovery fails.

    Discovery scans at most 15 JS bundles with a per-bundle 10s timeout.
    Once resolved the key is cached for the process lifetime.

    Args:
        force_discover: If True, skip the cache and re-discover from the live
            page. Useful when a ``pusher:error`` suggests the key has rotated.

    Returns:
        The Pusher app key string.
    """
    global _PUSHER_DISCOVERED_KEY  # noqa: PLW0603 — lazy-init cache

    if _PUSHER_DISCOVERED_KEY is not None and not force_discover:  # pragma: no cover
        return _PUSHER_DISCOVERED_KEY  # pragma: no cover

    import requests  # pragma: no cover

    session = requests.Session()  # pragma: no cover
    try:  # pragma: no cover
        session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html",
            }
        )
        resp = session.get("https://kick.com/", timeout=10)
        if resp.ok:
            # Find all JS chunk URLs in the page (scan at most 15 chunks)
            script_urls = re.findall(r'<script[^>]*src="([^"]+\.js)"[^>]*>', resp.text)
            for url in script_urls[:15]:
                abs_url = url if url.startswith("http") else "https://kick.com" + url
                # Only fetch scripts served over HTTPS from Kick's own domain.
                # Without this guard a tampered/MITM'd homepage could point the
                # loader at an arbitrary host (SSRF, e.g. cloud metadata
                # endpoints).  ``*.kick.com`` is allowed so CDN-hosted bundles
                # still resolve.
                if not _is_kick_origin(abs_url):
                    continue
                try:
                    js_resp = session.get(abs_url, timeout=10)
                    if js_resp.ok:
                        try:
                            match = re.search(
                                r"NEXT_PUBLIC_PUSHER_KEY[^}]*?"
                                r'default\("([a-f0-9]+)"\)',
                                js_resp.text,
                            )
                            if match:
                                key = match.group(1)
                                _PUSHER_DISCOVERED_KEY = key
                                return key
                        except re.error:
                            continue
                except requests.RequestException:
                    continue
    except requests.RequestException:  # pragma: no cover
        pass  # pragma: no cover
    finally:  # pragma: no cover
        with contextlib.suppress(OSError, RuntimeError):
            session.close()

    _PUSHER_DISCOVERED_KEY = _PUSHER_DEFAULT_KEY
    return _PUSHER_DEFAULT_KEY


_PUSHER_WS_TEMPLATE = (
    "wss://ws-us2.pusher.com/app/{key}?protocol=7&client=js&version=7.6.0&flash=false"
)


def get_pusher_ws_url(*, force_discover: bool = False) -> str:
    """Return the Pusher websocket URL with the current app key.

    Args:
        force_discover: If True, force re-discovery of the app key from
            Kick's live JS bundle before building the URL.

    Returns:
        The full Pusher WebSocket URL.
    """
    key = resolve_pusher_key(force_discover=force_discover)
    return _PUSHER_WS_TEMPLATE.format(key=key)


#: Template for the public chatroom channel name to subscribe to.
CHATROOM_CHANNEL_TEMPLATE = "chatrooms.{chatroom_id}.v2"

#: Video metadata endpoint, formatted with a video UUID.
VIDEO_API_TEMPLATE = "https://kick.com/api/v1/video/{video_id}"

#: Channel messages endpoint (VOD replay), formatted with a channel id.
CHANNEL_MESSAGES_API = "https://kick.com/api/v2/channels/{channel_id}/messages"


def is_numeric_id(value: str) -> bool:
    """Return True if *value* is a plain ASCII-digit identifier.

    Kick channel and chatroom ids are integers.  Validating this before an id
    is interpolated into an API URL path or Pusher channel name prevents a
    tampered API response from injecting path segments (``../``) or other
    characters into the outbound request.
    """
    return value.isascii() and value.isdigit()


# Pusher protocol event names.
PUSHER_SUBSCRIBE = "pusher:subscribe"
PUSHER_CONNECTION_ESTABLISHED = "pusher:connection_established"
PUSHER_SUBSCRIPTION_SUCCEEDED = "pusher_internal:subscription_succeeded"
PUSHER_PING = "pusher:ping"
PUSHER_PONG = "pusher:pong"
PUSHER_ERROR = "pusher:error"

#: Kick chat-message event name carried inside a Pusher frame's ``event`` field.
CHAT_MESSAGE_EVENT = "App\\Events\\ChatMessageEvent"

#: Pusher event name for a message being deleted.
MESSAGE_DELETED_EVENT = "App\\Events\\MessageDeletedEvent"

#: Pusher event name for a message being pinned.
PINNED_MESSAGE_CREATED_EVENT = "App\\Events\\PinnedMessageCreatedEvent"

#: Pusher event name for a pinned message being removed.
PINNED_MESSAGE_DELETED_EVENT = "App\\Events\\PinnedMessageDeletedEvent"

#: Pusher event name for a user being banned.
USER_BANNED_EVENT = "App\\Events\\UserBannedEvent"

#: Pusher event name for a user being unbanned.
USER_UNBANNED_EVENT = "App\\Events\\UserUnbannedEvent"

#: Pusher event name for a subscription event.
SUBSCRIPTION_EVENT = "App\\Events\\SubscriptionEvent"

#: Pusher event name for a gifted subscriptions event.
GIFTED_SUBSCRIPTIONS_EVENT = "App\\Events\\GiftedSubscriptionsEvent"

#: Pusher event name for a stream host event.
STREAM_HOST_EVENT = "App\\Events\\StreamHostEvent"

#: Pusher event name for a chat clear event.
CHAT_CLEAR_EVENT = "App\\Events\\ChatClearMessagesEvent"

# ── Event-to-message-type mapping ─────────────────────────────────────────────

#: Maps raw Pusher event names to normalized message types.
EVENT_NAME_MAP: dict[str, str] = {
    CHAT_MESSAGE_EVENT: "text_message",
    MESSAGE_DELETED_EVENT: "message_deleted",
    PINNED_MESSAGE_CREATED_EVENT: "pinned_message",
    PINNED_MESSAGE_DELETED_EVENT: "pinned_message_deleted",
    USER_BANNED_EVENT: "user_banned",
    USER_UNBANNED_EVENT: "user_unbanned",
    SUBSCRIPTION_EVENT: "subscription",
    GIFTED_SUBSCRIPTIONS_EVENT: "gifted_subscriptions",
    STREAM_HOST_EVENT: "stream_host",
    CHAT_CLEAR_EVENT: "chat_clear",
}

# ── Message types and groups ──────────────────────────────────────────────────

#: Maps Kick chat ``type`` values to normalized ``message_type`` values.
MESSAGE_TYPE_REMAPPING = {
    "message": "text_message",
    "reply": "text_message",
    "subscription": "subscription",
    "gifted_subscriptions": "gifted_subscriptions",
    "user_banned": "user_banned",
    "user_unbanned": "user_unbanned",
    "message_deleted": "message_deleted",
    "pinned_message": "pinned_message",
    "pinned_message_deleted": "pinned_message_deleted",
    "stream_host": "stream_host",
    "chat_clear": "chat_clear",
}

#: Default normalized message type for a Kick chat message.
DEFAULT_MESSAGE_TYPE = "text_message"

#: Maps message-group names to the normalized message types they contain.
MESSAGE_GROUPS = {
    "messages": ["text_message"],
    "subscriptions": ["subscription", "gifted_subscriptions"],
    "moderation": ["user_banned", "user_unbanned", "message_deleted", "chat_clear"],
    "pins": ["pinned_message", "pinned_message_deleted"],
    "hosts": ["stream_host"],
}

# ── Emotes ────────────────────────────────────────────────────────────────────

#: Matches Kick inline emote markers, e.g. ``[emote:37233:PogU]`` or
#: ``[emote:37233:]`` (missing name). Group 1 = id, group 2 = optional name.
EMOTE_REGEX = re.compile(r"\[emote:(\d+):([^\]]*)\]")

#: Template for an emote's full-size image URL.
EMOTE_IMAGE_TEMPLATE = "https://files.kick.com/emotes/{emote_id}/fullsize"

#: Source platform recorded on structured emote metadata.
EMOTE_SOURCE = "kick"

# ── Cloudflare / challenge detection ──────────────────────────────────────────

#: Substrings that, when present in an HTML response body, strongly indicate a
#: Cloudflare challenge / bot-protection interstitial rather than real content.
CLOUDFLARE_MARKERS = (
    "/cdn-cgi/challenge-platform",
    "cf-challenge",
    "cf_chl_opt",
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "Checking your browser before accessing",
)

# ── URL routing ───────────────────────────────────────────────────────────────
#
# Kick channel pages live at ``kick.com/{username}``. Reserved first-path
# segments below are Kick's own site routes and must not be treated as channel
# names. The ``/{user}/videos/{uuid}`` form is matched separately for VOD chat
# replay (see ``_get_chat_by_video`` in ``VALID_URLS``).

#: First-path segments that are Kick site routes, not channel names.
RESERVED_PATHS = (
    "about",
    "browse",
    "categories",
    "category",
    "clips",
    "dashboard",
    "following",
    "help",
    "messages",
    "notifications",
    "popout",
    "privacy",
    "search",
    "settings",
    "subscriptions",
    "support",
    "terms",
    "video",
    "videos",
)

#: Allowed channel-username characters: letters, digits, underscore, hyphen.
_USERNAME_CHARS = r"[A-Za-z0-9_-]+"

#: Maps the handler method name to the URL regex it accepts.
VALID_URLS = {
    "_get_chat_by_channel": (
        r"(?x)"
        r"https?://"
        r"(?:www\.)?kick\.com/"
        # Exclude reserved site routes from being treated as channel names.
        rf"(?!(?:{'|'.join(RESERVED_PATHS)})(?:[/?#]|$))"
        rf"(?P<id>{_USERNAME_CHARS})"
        # Single path segment only: reject /{user}/videos/... style paths.
        r"/?(?:[?#]|$)"
    ),
    "_get_chat_by_video": (
        r"(?x)"
        r"https?://"
        r"(?:www\.)?kick\.com/"
        rf"(?P<id>{_USERNAME_CHARS})"
        r"/videos/"
        r"(?P<video_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
        r"/?(?:[?#]|$)"
    ),
}
