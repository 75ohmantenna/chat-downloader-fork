# SPDX-License-Identifier: MIT

"""Kick chat downloader constants.

URL patterns, API endpoints, Pusher configuration, message-type/group
definitions, emote parsing patterns, and Cloudflare/challenge markers for the
Kick site.

These values encode the endpoints and payload shapes that the implementation
expects. Kick changes to endpoints, the Pusher application key, WebSocket
details, or event payloads may require corresponding updates here.
"""

from __future__ import annotations

import re

# ── HTTP API ──────────────────────────────────────────────────────────────────

#: Base site URL.
BASE_URL = "https://kick.com"

#: Channel metadata endpoint, formatted with a channel username/slug.
CHANNEL_API_TEMPLATE = "https://kick.com/api/v2/channels/{username}"

#: Preloaded (recent) messages endpoint, formatted with a numeric channel id.
MESSAGES_API_TEMPLATE = "https://kick.com/api/v2/channels/{channel_id}/messages"

#: Template for the public chatroom channel name to subscribe to.
CHATROOM_CHANNEL_TEMPLATE = "chatrooms.{chatroom_id}.v2"

#: Video metadata endpoint, formatted with a video UUID.
VIDEO_API_TEMPLATE = "https://kick.com/api/v1/video/{video_id}"

#: Clip metadata endpoint, formatted with a provider clip ID.
CLIP_API_TEMPLATE = "https://kick.com/api/v2/clips/{clip_id}"

#: Mobile clip metadata fallback, formatted with a provider clip ID.
MOBILE_CLIP_API_TEMPLATE = "https://mobile.kick.com/api/v1/clips/{clip_id}"

#: Maximum duration accepted from the mobile clip contract.
MOBILE_CLIP_MAX_DURATION_SECONDS = 180

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


_VIDEO_ID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CLIP_ID_PATTERN = r"clip_[A-Za-z0-9_-]{1,128}"


def is_video_id(value: str) -> bool:
    """Return whether *value* is a canonical lowercase Kick VOD UUID."""
    return re.fullmatch(_VIDEO_ID_PATTERN, value) is not None


def is_clip_id(value: str) -> bool:
    """Return whether *value* is safe to interpolate into a clip API path."""
    return re.fullmatch(_CLIP_ID_PATTERN, value) is not None


# Pusher protocol event names.
PUSHER_SUBSCRIBE = "pusher:subscribe"
PUSHER_CONNECTION_ESTABLISHED = "pusher:connection_established"
PUSHER_SUBSCRIPTION_SUCCEEDED = "pusher_internal:subscription_succeeded"
PUSHER_PING = "pusher:ping"
PUSHER_PONG = "pusher:pong"
PUSHER_ERROR = "pusher:error"

#: Maximum unique diagnostic payloads captured for one Kick anomaly label.
KICK_DEBUG_SAMPLE_LIMIT = 10

#: Maximum unique payloads captured for one unsupported Kick event name.
KICK_UNKNOWN_EVENT_SAMPLE_LIMIT = 3

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

#: Pusher event name for poll state updates.
POLL_UPDATE_EVENT = "App\\Events\\PollUpdateEvent"

#: Pusher event name for a poll being removed.
POLL_DELETE_EVENT = "App\\Events\\PollDeleteEvent"

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
    POLL_UPDATE_EVENT: "poll_update",
    POLL_DELETE_EVENT: "poll_deleted",
}

# ── Message types and groups ──────────────────────────────────────────────────

#: Maps Kick chat ``type`` values to normalized ``message_type`` values.
MESSAGE_TYPE_REMAPPING = {
    "celebration": "text_message",
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
    "polls": ["poll_update", "poll_deleted"],
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
        r"/?(?=[?#]|$)"
    ),
    "_get_chat_by_video": (
        r"(?x)"
        r"https?://"
        r"(?:www\.)?kick\.com/"
        rf"(?P<id>{_USERNAME_CHARS})"
        r"/videos/"
        rf"(?P<video_id>{_VIDEO_ID_PATTERN})"
        r"/?(?=[?#]|$)"
    ),
    "_get_chat_by_clip": (
        r"(?x)"
        r"https?://"
        r"(?:www\.)?kick\.com/"
        rf"(?P<id>{_USERNAME_CHARS})"
        r"/clips/"
        rf"(?P<clip_id>{_CLIP_ID_PATTERN})"
        r"/?(?=[?#]|$)"
    ),
}
