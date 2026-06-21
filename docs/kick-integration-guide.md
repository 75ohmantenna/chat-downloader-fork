# Kick Integration Guide

How the Kick integration works in `chat-downloader-fork`. For maintainers
debugging the live Pusher path or the REST-backed VOD replay path.

The Kick stack is split across two transport families:

- A Pusher (WebSocket) feed for live chat.
- Kick's public, unauthenticated `api/v2` JSON REST API for channel metadata,
  preloaded history, and VOD replay messages.

Kick also publishes official developer documentation at
<https://docs.kick.com/> and source docs at
<https://github.com/KickEngineering/KickDevDocs>. That official Public API is
OAuth-scoped and lives under `https://api.kick.com/public/v1/...`. The current
downloader does **not** use it for capture, because the documented Chat API is
for sending/deleting messages and the documented Events API is webhook-based;
neither provides unauthenticated live chat replay or a public read-chat stream.

Unlike Twitch and YouTube, Kick exposes no private GraphQL/InnerTube layer; the
fragility points are the Pusher application key, the WebSocket event payload
shapes, and Cloudflare bot-protection on the REST endpoints.

## What It Covers

The Kick implementation is responsible for:

- matching Kick live channel and VOD URLs
- retrieving channel and video metadata
- streaming live chat from the Pusher WebSocket (live *and* offline channels —
  the chatroom stays active when the stream is down)
- emitting preloaded recent history on connect and deduplicating it against the
  live feed
- reading historical chat for VODs by paginating the channel message API and
  filtering to the VOD time window
- parsing Kick-specific message, badge, emote, subscription, moderation, pin,
  and host event data

Primary entry point:

- `src/chat_downloader/sites/kick/extractor.py`

Public site methods are `get_chat_by_channel` and `get_chat_by_video`. URL
matching routes live channel URLs and VOD URLs to those methods through
`BaseChatDownloader.matches()`.

Main implementation areas:

- `src/chat_downloader/sites/kick/`
- `src/chat_downloader/sites/kick/parsing/`

## Supported URLs

| Pattern | Handler | Capture |
| --- | --- | --- |
| `kick.com/{username}` | `_get_chat_by_channel` | Live chat (works while offline) |
| `kick.com/{username}/videos/{uuid}` | `_get_chat_by_video` | VOD chat replay |

URL matching lives in `constants.py::VALID_URLS`. Reserved first-path segments
(`about`, `browse`, `videos`, `settings`, …) in `RESERVED_PATHS` are Kick site
routes and are never treated as channel names. The VOD pattern requires a
canonical UUID for the `video_id` group.

## End-to-End Flow

The Kick flow depends on the target type.

### Live channels

1. Resolve the username from the URL.
2. Fetch channel metadata from `api/v2/channels/{username}` (retried on
   transient failures).
3. Resolve the channel id, chatroom id, and title. Offline channels are *not*
   rejected — the chatroom is still active.
4. Fetch preloaded recent messages (best-effort; non-fatal on failure). The
   API returns them newest-first; they are reversed into chronological order
   before being emitted.
5. Open the Pusher WebSocket, subscribe to the public chatroom channel, and
   stream live frames.
6. Dispatch each frame to a typed parser, deduplicate against preloaded and
   recent message ids, filter by message groups/types, and yield.
7. On disconnect, reconnect and resubscribe.

### VODs

1. Resolve the username and video UUID from the URL.
2. Fetch video metadata from `api/v1/video/{video_id}`.
3. Derive the channel id and the VOD time window (`start_time` plus
   `duration`).
4. Page through `api/v2/channels/{id}/messages` (newest-first) using the
   timestamp cursor.
5. Keep messages whose `created_at` falls inside the VOD window; stop once
   messages predate the window start.
6. Reverse into chronological order, apply `max_messages`, and yield.

## Module Guide

### Site entry and orchestration

- `extractor.py`: `KickChatDownloader`, URL matching, public site methods
- `live_service.py`: live chat orchestration (metadata, chatroom resolution,
  preloaded history, websocket loop, dedup, reconnect)
- `replay_service.py`: VOD orchestration (metadata, time-window pagination)

### Transport and API access

- `api_client.py`: Cloudflare-aware HTTP client for the `api/v2` JSON
  endpoints served from `kick.com`. This is the unauthenticated web/API path,
  not Kick's official OAuth-scoped Public API at `api.kick.com/public/v1`.
  Owns the lazy session singleton (`_get_kick_session`), challenge detection,
  not-found handling, and transient-error classification. Does no content
  parsing.
- `websocket_transport.py`: the *only* module that imports `websocket-client`.
  Exposes a small parsing-free interface (`connect` / `subscribe` / `recv` /
  `send_pong` / `close`) plus the `read_frames` generator. The connector is
  injectable so the live path is fully unit-testable without network access.

### Parsing and shared Kick data

- `parsing/events.py`: Pusher frame dispatch. Maps raw event names to
  normalized message types via `EVENT_NAME_MAP`, then to parser functions via
  `_PARSER_DISPATCH`. Decodes Kick's double-encoded `data` field. Control
  frames are ignored; unknown events are debug-logged by name only and skipped;
  a `pusher:error` frame raises `KickError`.
- `parsing/messages.py`: chat-message normalization for both live
  `ChatMessageEvent` payloads and preloaded history (same shape); badge and
  timestamp handling. Entry points `parse_chat_message` /
  `parse_preloaded_messages`.
- `parsing/emotes.py`: inline emote-marker parsing (`[emote:ID:NAME]`) and
  structured emote metadata/image URLs.
- `parsing/subscriptions.py`: `SubscriptionEvent` and
  `GiftedSubscriptionsEvent` normalization.
- `parsing/moderation.py`: ban, unban, message-delete, and chat-clear
  normalization.
- `parsing/pins.py`: pinned-message created/deleted normalization.
- `parsing/hosts.py`: stream-host normalization.
- `constants.py`: URL patterns, REST endpoints, Pusher config and key
  discovery, Pusher/event name constants, event-to-message-type and
  message-type-to-group maps, emote patterns, and Cloudflare markers.
- `errors.py`: `KickError` and the retryable `KickServerError` subclass.

There is no `client.py` facade in the Kick package. Import focused modules
directly for patch points: `api_client.py` for REST, `websocket_transport.py`
for the live feed, and `parsing/` for message shaping.

## Official Public API Reference

The official Kick Dev Docs are useful maintenance references, but they are not
drop-in replacements for this tool's current capture path.

Relevant documented surfaces:

| Official surface | Usefulness to this project |
| --- | --- |
| `GET /public/v1/channels` | Authenticated channel metadata by slug or broadcaster id. Useful as a field-name reference (`slug`, `broadcaster_user_id`, `stream`, `viewer_count`, `stream_title`) and a possible future authenticated metadata fallback. |
| `GET /public/v1/livestreams` | Authenticated livestream metadata (`channel_id`, `slug`, `started_at`, `viewer_count`, title/category fields). Useful for comparing live-status semantics, but not currently needed for unauthenticated capture. |
| `POST /public/v1/chat` and `DELETE /public/v1/chat/{message_id}` | Write/moderation APIs only. They do not read chat and should not be wired into the downloader's read-only capture flow. |
| Webhook event `chat.message.sent` | Best official schema reference for message fields (`message_id`, `replies_to`, `sender.identity.badges`, `content`, `emotes`, `created_at`). Use it to sanity-check parser fixtures and output-field expectations. |
| Webhook events `channel.subscription.*`, `moderation.banned`, `kicks.gifted` | Useful shape references for subscription/moderation/gift-style events. They are webhook payloads, not Pusher payloads, so treat differences as evidence to investigate rather than direct parser contracts. |

Known gaps:

- The official docs do not document `https://kick.com/api/v2/channels/{slug}`,
  `https://kick.com/api/v2/channels/{id}/messages`, or the VOD
  `api/v1/video/{uuid}` endpoint this tool currently uses.
- The official docs do not document Pusher event names such as
  `App\Events\ChatMessageEvent`, `App\Events\PinnedMessageCreatedEvent`, or
  `App\Events\PinnedMessageDeletedEvent`.
- The official docs do not document the Pusher app-key discovery path
  (`NEXT_PUBLIC_PUSHER_KEY`) or the anonymous `chatrooms.{id}.v2`
  subscription channel.
- A future authenticated mode would need new user-facing init/request fields in
  `src/chat_downloader/models/` first, so CLI help and the typed API remain in
  sync.

## Live Capture Details

### Metadata and offline channels

The live path begins with a `api/v2/channels/{username}` lookup. A missing
channel id or chatroom id is a terminal `KickError`. An absent `livestream`
object means the channel is offline — this is logged but **not** an error,
because Kick keeps the chatroom active and the Pusher feed flowing regardless of
stream status. The reported `Chat.status` is `"live"` when a livestream is
present and `"idle"` otherwise.

### Preloaded history

Recent messages are fetched from `api/v2/channels/{id}/messages` before the
WebSocket opens and emitted first. This fetch is best-effort: any
`KickServerError` is swallowed and yields an empty list, since the live feed is
the primary source. Preloaded ids seed the dedup cache so they are not repeated
when they also arrive over the socket.

### Pusher transport

Live chat uses the public Pusher WebSocket (`wss://ws-us2.pusher.com/app/...`).
The transport:

- builds the URL from the resolved Pusher app key
- subscribes anonymously (`auth: ""`) to `chatrooms.{chatroom_id}.v2`
- answers Pusher `ping` frames with `pong` inside `read_frames`
- treats timed-out or malformed reads as skippable (`None`)
- raises `ConnectionError` on a closed socket, which drives reconnect

### Pusher application key discovery

The Pusher app key is shipped in Kick's public Next.js JS bundle
(`NEXT_PUBLIC_PUSHER_KEY`) and is not a secret — it grants only anonymous,
read-only subscription to public chatrooms. `constants.py::resolve_pusher_key`
fetches the homepage, scans the JS chunks for the key, caches it, and falls back
to the compiled-in `_PUSHER_DEFAULT_KEY` if discovery fails. Pass
`force_discover=True` to re-discover when a `pusher:error` suggests the key has
rotated.

### Dedup and filtering

Before yielding, the live service:

- deduplicates against a bounded `_SeenMessageCache`
  (`_KICK_LIVE_SEEN_MESSAGE_LIMIT = 10_000`) keyed on `message_id`
- filters through configured message groups and types via `MessageFilter`

### Reconnect

The receive loop runs under a reconnect wrapper: a `ConnectionError` closes the
transport, reopens it, and resubscribes, retrying per the request's retry
policy. The loop carries `# noqa: C901` for its intrinsic branchiness.

## Replay Capture Details

VOD chat is served by the same `api/v2/channels/{id}/messages` endpoint as
preloaded live history, not a dedicated replay API. `replay_service.py`:

- loads video metadata and derives the `(start, end)` window from `start_time`
  and `duration`
- pages newest-first using the timestamp `cursor` (capped at 500 pages)
- classifies each message as in-window, after-window (skip), or before-window
  (stop — older messages cannot belong to the VOD)
- reverses the collected messages into chronological order and applies
  `max_messages`

Most of the VOD path carries `# pragma: no cover — network-dependent`; the
classification helper is covered by unit tests.

## Message Groups and Types

`constants.py::MESSAGE_GROUPS` maps `--message_groups` names to normalized
message types:

| Group | Message types |
| --- | --- |
| `messages` | `text_message` |
| `subscriptions` | `subscription`, `gifted_subscriptions` |
| `moderation` | `user_banned`, `user_unbanned`, `message_deleted`, `chat_clear` |
| `pins` | `pinned_message`, `pinned_message_deleted` |
| `hosts` | `stream_host` |

The default message group surfaces only `messages`. Use, for example,
`--message_groups messages subscriptions moderation` to capture non-text
events.

## Cloudflare Dependency

The REST endpoints sit behind Cloudflare. `api_client.py` uses a three-tier
session strategy:

1. **``curl-cffi`` with Chrome 124 TLS impersonation** — avoids Cloudflare
   challenges at the TLS-fingerprint level before they are even presented.
2. **``cloudscraper``** — JS-challenge solver for simpler challenges (falls
   through if curl-cffi is unavailable).
3. **Plain ``requests.Session``** with browser-like headers — last resort when
   neither optional library is installed.

When a response body looks like a challenge page (Cloudflare markers, or an HTML
body where JSON was expected) or returns HTTP 403, the client raises
`CaptchaChallengeRequired` — neither curl-cffi nor cloudscraper could bypass the
challenge (modern Cloudflare challenges may require tooling updates or an
endpoint with better IP reputation).

Status mapping in `api_client.py::_check_status`:

- `404` → `UserNotFound`
- `403` / challenge body → `CaptchaChallengeRequired`
- `429` / `5xx` → `KickServerError` (transient, retried)
- other non-`200` → `KickError`

## Common Failure Points

The Kick stack is most sensitive to changes in:

- the Pusher application key (rotated when Kick rebuilds the frontend)
- Pusher event names and the double-encoded `data` payload shapes
- Cloudflare bot-protection on the REST endpoints
- channel/video metadata structure (`chatroom.id`, `livestream`, `start_time`,
  `duration`)
- divergence between official webhook schemas and live Pusher payloads; the
  official docs are schema hints, not authoritative contracts for the Pusher
  path

When debugging Kick breakage, inspect modules in this order:

1. `api_client.py` — REST status mapping, challenge detection, session setup
2. `constants.py` — endpoints, `resolve_pusher_key`, event/group maps
3. `live_service.py` or `replay_service.py` — service-layer orchestration
4. `websocket_transport.py` — Pusher framing, subscribe, reconnect signals
5. `parsing/events.py` — event-name resolution and dispatch
6. `parsing/messages.py` and the per-event parsers — field assembly

## Testing

The Kick suite is fully offline. The WebSocket connector and frame iterator,
and the HTTP session, are injectable, so the live and replay paths run without
network access. Fixtures live under `tests/fixtures/kick/`; shared fakes live in
`tests/kick_helpers.py`. Live-network smoke tests are marked
`@pytest.mark.network` in `tests/test_kick_network.py` and run only with
`--run-network`.

To add coverage for a new event type:

1. Add a raw fixture under `tests/fixtures/kick/`.
2. Add the event name to `constants.py` (`*_EVENT`, `EVENT_NAME_MAP`, and the
   relevant `MESSAGE_GROUPS` entry).
3. Write the parser under `parsing/` and register it in
   `events.py::_PARSER_DISPATCH`.
4. Add a parser unit test and run `make ci`.
