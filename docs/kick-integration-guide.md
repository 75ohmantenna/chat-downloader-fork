# Kick Integration Guide

This guide explains how the Kick integration works in
`chat-downloader-fork`. It is intended for maintainers debugging the live
Pusher path or the REST-backed VOD and clip replay paths.

The Kick stack is split across two transport families:

- A Pusher (WebSocket) feed for live chat.
- Kick's unauthenticated web JSON endpoints (`api/v2` for channel, clip, and
  message data, plus `api/v1/video` for VOD metadata).

Kick's OAuth-scoped official Public API is a useful schema reference, but it
does not expose the unauthenticated read-chat or replay stream this tool needs.
See [Official Public API reference](#official-public-api-reference).

Unlike Twitch and YouTube, Kick exposes no private GraphQL/InnerTube layer; the
fragility points are the Pusher application key, the WebSocket event payload
shapes, and Cloudflare bot-protection on the REST endpoints.

## What It Covers

The Kick implementation is responsible for:

- matching Kick live channel, VOD, and clip URLs
- retrieving channel, video, and clip metadata
- streaming live chat from the Pusher WebSocket (live *and* offline channels —
  the chatroom stays active when the stream is down)
- emitting preloaded recent history and current pin state on connect, then
  deduplicating them against the live feed
- backfilling recent history after reconnects and Pusher-key recovery so
  messages received during an outage are not silently lost
- reading historical chat for VODs and clips by paginating the channel message
  API and filtering to the selected replay window
- parsing Kick-specific message, badge, emote, subscription, moderation, pin,
  and host event data

Primary entry point:

- `src/chat_downloader/sites/kick/extractor.py`

Public site methods are `get_chat_by_channel`, `get_chat_by_video`, and
`get_chat_by_clip`. URL matching routes live channel, VOD, and clip URLs to
those methods through `BaseChatDownloader.matches()`.

Main implementation areas:

- `src/chat_downloader/sites/kick/`
- `src/chat_downloader/sites/kick/parsing/`

## Supported URLs

| Pattern | Handler | Capture |
| --- | --- | --- |
| `kick.com/{username}` | `_get_chat_by_channel` | Live chat (works while offline) |
| `kick.com/{username}/videos/{uuid}` | `_get_chat_by_video` | VOD chat replay |
| `kick.com/{username}/clips/{clip_id}` | `_get_chat_by_clip` | Bounded clip chat replay |

URL matching lives in `constants.py::VALID_URLS`. Reserved first-path segments
(`about`, `browse`, `videos`, `settings`, …) in `RESERVED_PATHS` are Kick site
routes and are never treated as channel names. The VOD pattern requires a
canonical UUID for the `video_id` group. Clip IDs are restricted to bounded,
path-safe provider identifiers before they are interpolated into an API URL.

## End-to-End Flow

The Kick flow depends on the target type.

### Live channels

1. Resolve the username from the URL.
2. Fetch channel metadata from `api/v2/channels/{username}` (retried on
   transient failures).
3. Resolve the channel ID, chatroom ID, and title. Offline channels are *not*
   rejected — the chatroom is still active.
4. Fetch preloaded recent messages and the current pin state (best-effort;
   non-fatal on failure). The API returns messages newest-first; they are
   reversed into chronological order before the current pin is emitted.
5. Open the Pusher WebSocket with the compiled public application key,
   subscribe to the public chatroom channel, and stream live frames.
6. Dispatch each frame to a typed parser, deduplicate against preloaded and
   recent message IDs, filter by message groups/types, and yield.
7. On disconnect, reconnect and resubscribe. If Pusher rejects the application
   key, force one fresh discovery before treating a repeated rejection as
   terminal. After either recovery path, refetch recent history and emit only
   records not already present in the bounded deduplication cache.

### VODs

1. Resolve the username and video UUID from the URL.
2. Fetch video metadata from `api/v1/video/{video_id}`.
3. Derive the channel ID and the VOD time window (`start_time` plus
   `duration`).
4. Narrow the metadata window with request-relative `start_time` and
   `end_time` offsets when supplied.
5. Seed `api/v2/channels/{id}/messages` at the selected end timestamp, then
   page backward (newest-first) using the returned cursor.
6. Keep messages whose `created_at` falls inside the selected window; stop once
   messages predate the window start.
7. Reverse into chronological order, apply `max_messages`, and yield.

### Clips

1. Resolve the channel slug and clip ID from the URL.
2. Fetch `api/v2/clips/{clip_id}` and validate the returned identity, source
   VOD UUID, channel ID, non-negative VOD offset, and positive duration.
3. Fetch the source VOD metadata and require its channel to agree with the
   clip. A missing source VOD is reported as `NoChatReplay`.
4. Treat request `start_time` and `end_time` as clip-relative, clamp them to
   the clip duration, then translate them to source-VOD offsets.
5. Clamp the translated window again to the source recording. Timestamp-seeded
   VOD pagination then retrieves only the clip interval and emits it in
   chronological order.

Kick's clip `started_at` can include a short HLS keyframe lead-in. Chat mapping
therefore uses `vod_starts_at` plus `duration`, which describes the intended
source-VOD interval, rather than the playlist's padded wall-clock start.

## Module Guide

### Site entry and orchestration

- `extractor.py`: `KickChatDownloader`, URL matching, public site methods
- `live_service.py`: live chat orchestration (metadata, chatroom resolution,
  preloaded history, WebSocket loop, deduplication, reconnect)
- `replay_service.py`: VOD orchestration (metadata, time-window pagination)
- `clip_service.py`: clip metadata validation and source-VOD replay assembly

### Transport and API access

- `api_client.py`: downloader-owned HTTP client for the unauthenticated
  `kick.com/api/v1` and `api/v2` channel, history, VOD, and clip JSON endpoints.
  It owns endpoint status, challenge, JSON, and object-shape classification but
  does no chat parsing.
- `http_session.py`: constructs the client's isolated curl-cffi,
  cloudscraper, or requests transport and defines its narrow session Protocol.
- `websocket_transport.py`: the *only* module that imports `websocket-client`.
  Exposes a small parsing-free interface (`connect` / `subscribe` / `recv` /
  `send_pong` / `close`) plus the `read_frames` generator. The connector is
  injectable so the live path is fully unit-testable without network access.

### Parsing and shared Kick data

- `parsing/events.py`: Pusher frame dispatch. Maps raw event names to
  normalized message types via `EVENT_NAME_MAP`, then to parser functions via
  `_PARSER_DISPATCH`. Decodes Kick's double-encoded `data` field. Control
  frames are omitted from output; unknown events are captured when explicitly
  enabled, debug-logged by name, and skipped; a `pusher:error` frame raises
  `KickError`.
- `parsing/messages.py`: chat-message normalization for both live
  `ChatMessageEvent` payloads and preloaded history (same shape); badge and
  timestamp handling, including reply context from object- or string-encoded
  metadata. Entry points `parse_chat_message` /
  `parse_preloaded_messages`. Sender badges merge Kick's legacy `badges` and
  image-backed `badges_v2` arrays in stable provider order. Structured output
  retains v2 image URLs, selection state, badge type, provider metadata, and
  sort order without applying the mobile client's display-count limit.
- `parsing/emotes.py`: inline emote-marker parsing (`[emote:ID:NAME]` →
  `:NAME:` in plain text, or `:emote_ID:` when no name is present) and
  structured emote metadata/image URLs.
- `parsing/subscriptions.py`: `SubscriptionEvent` and
  `GiftedSubscriptionsEvent` normalization.
- `parsing/moderation.py`: ban, unban, message-delete, and chat-clear
  normalization, including Kick's AI-moderation flag and violated-rule labels.
- `parsing/pins.py`: pinned-message created/deleted normalization.
- `parsing/hosts.py`: stream-host normalization.
- `constants.py`: URL patterns, REST endpoints, Pusher/event name constants,
  event-to-message-type and
  message-type-to-group maps, emote patterns, and Cloudflare markers.
- `pusher_discovery.py`: default-first Pusher application-key selection,
  best-effort refresh, cache ownership, and WebSocket URL construction.
- `errors.py`: `KickError` and the retryable `KickServerError` subclass.

There is no `client.py` facade in the Kick package. Import focused modules
directly for patch points: `api_client.py` for REST, `websocket_transport.py`
for the live feed, and `parsing/` for message shaping.

## Official Public API reference

The official Kick Dev Docs are useful maintenance references, but they are not
drop-in replacements for this tool's current capture path.

Use the official [API documentation](https://docs.kick.com/) and
[documentation changelog](https://github.com/KickEngineering/KickDevDocs/blob/main/changelog.md)
when reviewing these external surfaces. Runtime behavior remains defined by
this repository's constants, parsers, fixtures, and tests.

Relevant documented surfaces:

| Official surface | Usefulness to this project |
| --- | --- |
| `GET /public/v1/channels` | Authenticated channel metadata by slug or broadcaster ID. Useful as a field-name reference (`slug`, `broadcaster_user_id`, `stream`, `viewer_count`, `stream_title`) and a possible future authenticated metadata fallback. |
| `GET /public/v2/livestreams` and `GET /public/v1/users/livestreams` | Authenticated, paginated livestream metadata and per-user live status. These are useful for comparing live-status semantics, but are not needed for unauthenticated capture. The older `GET /public/v1/livestreams` surface is deprecated. |
| `POST /public/v1/chat` and `DELETE /public/v1/chat/{message_id}` | Write/moderation APIs only. They do not read chat and should not be wired into the downloader's read-only capture flow. |
| Webhook event `chat.message.sent` | Best official schema reference for message fields (`message_id`, `replies_to`, `sender.identity.badges`, `content`, `emotes`, `created_at`). Use it to verify parser fixtures and output-field expectations. |
| Webhook events `channel.subscription.*`, `moderation.banned`, `kicks.gifted` | Useful shape references for subscription/moderation/gift-style events. They are webhook payloads, not Pusher payloads, so treat differences as evidence to investigate rather than direct parser contracts. |

Known gaps:

- The official docs do not document `https://kick.com/api/v2/channels/{slug}`,
  `https://kick.com/api/v2/channels/{id}/messages`,
  `https://kick.com/api/v2/clips/{clip_id}`, or the VOD
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

The live path begins with an `api/v2/channels/{username}` lookup. A missing
channel ID or chatroom ID is a terminal `KickError`. An absent `livestream`
object means the channel is offline — this is logged but **not** an error,
because Kick keeps the chatroom active and the Pusher feed flowing regardless of
stream status. The reported `Chat.status` is `"live"` when a livestream is
present and `"idle"` otherwise.

### Preloaded history

Recent messages are fetched from `api/v2/channels/{id}/messages` before the
WebSocket opens and emitted first. This fetch is best-effort: expected provider,
challenge, and transport errors yield an empty list, since the live feed is the
primary source. Process interrupts still propagate. Preloaded IDs seed the
deduplication cache so they are not repeated when they also arrive over the
socket.
The response's current pin state is emitted after recent messages. Current Kick
pin events omit a top-level event ID, so the parser derives a namespaced event
ID from the nested message ID to avoid colliding with the original chat message.
It keeps the nested sender as the message author and the `pinnedBy`/`pinned_by`
actor as pin metadata. A live pin event's top-level `timestamp` is the time of
the pin action. A current pin loaded from REST has no action time and therefore
omits that field rather than inventing one. In both shapes,
`metadata.original_message_created_at` records the nested chat message's own
creation time; `metadata.pinned_message_created_at` remains as a compatibility
alias with the same value.

### Pusher transport

Live chat uses the public Pusher WebSocket (`wss://ws-us2.pusher.com/app/...`).
The transport:

- builds the URL from the resolved Pusher app key
- subscribes anonymously (`auth: ""`) to `chatrooms.{chatroom_id}.v2`
- applies the one-second receive-timeout minimum and debug-logs the requested
  and effective values
- answers Pusher `ping` frames with `pong` inside `read_frames`, then exposes
  them to the orchestration layer for connection diagnostics
- treats timed-out or malformed reads as skippable (`None`)
- raises `ConnectionError` on a closed socket, which drives reconnect

Successful debug runs include live-connection diagnostics in the final run
summary: decoded, control, parsed, unsupported, unknown-message-type,
malformed, and invalid-frame counts; successful reconnect and Pusher-key
recovery counts; and the last decoded-frame timestamp in UTC microseconds.
Per-type output counts remain separate because filtering and preloaded history
can make them differ from raw Pusher counts.

Kick live channel URLs reject `start_time` and `end_time` because the public
Pusher feed and short preloaded history cannot seek. Use a Kick VOD or clip URL
for bounded replay. VOD offsets are relative to the recording start; clip
offsets are relative to the clip. Both are clamped to the available recording.

### Pusher application key discovery

The compiled Pusher app key is not a secret — it grants only anonymous,
read-only subscription to public chatrooms. The normal connection path uses
that key without fetching Kick's homepage or JavaScript bundles. This avoids a
bounded but unnecessary scan on every new CLI process.

If Pusher returns `pusher:error`, `pusher_discovery.py::resolve_pusher_key`
performs one best-effort compatibility scan for the historical
`NEXT_PUBLIC_PUSHER_KEY` marker, caches a discovered replacement, and
reconnects. Current Kick bundles may omit that marker, so refresh falls back to
the compiled key when the homepage, bundle requests, or extraction do not
succeed. Discovery follows the downloader's HTTP timeout policy, does not
follow redirects, and retains a three-second per-request and ten-second total
budget. A second Pusher error is terminal, preventing an invalid key from
causing an unbounded reconnect loop. `force_discover=True` remains an internal
test and maintenance seam on the transport.

### Dedup and filtering

Before yielding, the live service:

- deduplicates against a bounded `_SeenMessageCache`
  (`_KICK_LIVE_SEEN_MESSAGE_LIMIT = 10_000`) keyed on `message_id`
- filters through configured message groups and types via `MessageFilter`

### Reconnect

The receive loop runs under a reconnect wrapper: a `ConnectionError` closes the
transport, reopens it, and resubscribes, retrying per the request's retry
policy. A Pusher error gets the separate one-shot forced-discovery recovery
described above. Both recovery paths refetch recent messages and current pin
state, then use the shared ID cache to backfill only records missed during the
outage. The loop carries `# noqa: C901` for its intrinsic branchiness.

## Replay Capture Details

VOD chat is served by the same `api/v2/channels/{id}/messages` endpoint as
preloaded live history, not a dedicated replay API. `replay_service.py`:

- loads video metadata and derives the `(start, end)` window from `start_time`
  and `duration`
- pages newest-first using the timestamp `cursor` until the time window is
  exhausted; repeated cursors or pages stop pagination safely
- classifies each message as in-window, after-window (skip), or before-window
  (stop — older messages cannot belong to the VOD)
- reverses the collected messages into chronological order and applies
  `max_messages`

The VOD orchestration and spooled reverse-pagination path are covered by
offline service tests. Coverage pragmas remain only on defensive or
network-only branches with inline rationale.

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

The default message group surfaces only `messages`. Use `--message_groups all`
for full-spectrum diagnostics, or pass a comma-separated subset such as
`messages,subscriptions,moderation` when only selected non-text events are
needed. `all` is the shared unfiltered selector rather than an entry in the
site-specific group map.

Kick's default text formatter labels subscription, pin, host, and moderation
events. Empty-message events such as deletions and chat clears render bracketed
notices rather than blank lines. Live WebSocket events without a valid provider
`timestamp` retain a distinct UTC-microsecond `received_timestamp`; the Kick
formatter uses it only as a fallback and marks it `[received]` in TXT.
Preloaded history plus VOD and clip replay do not receive this live-arrival
field.
AI-moderated deletion notices append `[AI moderated]` and any violated-rule
labels, while ordinary deletion notices stay compact.

## Cloudflare Dependency

The REST endpoints sit behind Cloudflare. `http_session.py` uses a three-tier
session strategy for the client-owned transport. Standard installations include
all three dependencies; the fallbacks also keep degraded or partial
environments diagnosable:

1. **curl-cffi with Chrome 124 TLS impersonation** — avoids Cloudflare
   challenges at the TLS-fingerprint level before they are even presented.
2. **cloudscraper** — JS-challenge solver for simpler challenges (used if
   curl-cffi cannot be imported or initialized).
3. **Plain requests session** with browser-like headers — last resort when
   neither specialized backend can be imported or initialized.

When a response body looks like a challenge page (Cloudflare markers, or an HTML
body where JSON was expected) or returns HTTP 403, the client raises
`CaptchaChallengeRequired` — neither curl-cffi nor cloudscraper could bypass the
challenge (modern Cloudflare challenges may require tooling updates or an
endpoint with better IP reputation).

Status mapping in `api_client.py::_check_status`:

- channel `404` → `UserNotFound`; VOD/clip/history `404` → terminal `KickError`
- `403` / challenge body → `CaptchaChallengeRequired`
- `429` / `5xx` → `KickServerError` (transient, retried)
- other non-`200` → `KickError`

The isolated REST transport follows the downloader's proxy configuration. With
no explicit proxy it also preserves the backend's normal environment-proxy
behavior; `proxy=""` disables that behavior. Cookie authentication is checked
against the same effective-proxy safety policy before provider setup.

## Common Failure Points

The Kick stack is most sensitive to changes in:

- the Pusher application key (rotated when Kick rebuilds the frontend)
- Pusher event names and the double-encoded `data` payload shapes
- Cloudflare bot-protection on the REST endpoints
- channel/video metadata structure (`chatroom.id`, `livestream`, `start_time`,
  `duration`)
- clip metadata identity, source-VOD, channel, `vod_starts_at`, and `duration`
  fields
- divergence between official webhook schemas and live Pusher payloads; the
  official docs are schema hints, not authoritative contracts for the Pusher
  path

When debugging Kick breakage, inspect modules in this order:

1. `api_client.py` — REST status mapping and challenge detection
2. `http_session.py` — optional backend selection and session setup
3. `pusher_discovery.py` — Pusher-key discovery and fallback
4. `constants.py` — endpoints and event/group maps
5. `live_service.py` or `replay_service.py` — service-layer orchestration
6. `websocket_transport.py` — Pusher framing, subscribe, reconnect signals
7. `parsing/events.py` and per-event parsers — dispatch and field assembly

## Debug Sample Capture

Kick can capture sanitized diagnostic samples for unsupported event names,
unknown chat-message types, malformed event/preloaded payloads, Pusher errors,
and invalid WebSocket shapes. Capture requires both debug logging and explicit
opt-in:

```bash
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
chat_downloader "https://kick.com/xqc" --logging debug
```

For clean-run schema review, a second explicit opt-in captures the first three
raw WebSocket frames for each normalized event type that successfully parses.
Type-specific per-run attempt bounds survive reconnects and exclude Pusher
control, unknown, and malformed frames:

```bash
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES=1 \
chat_downloader "https://kick.com/xqc" --logging debug
```

Set `CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR` to retain samples in a chosen private
directory. Each anomaly label captures at most ten unique payloads per process
and directory; successful frame capture attempts at most three payloads per
normalized event type and retrieval run. Type-specific labels make the sampled
surface visible without opening every file. The shared sanitizer redacts
credential-bearing fields and sensitive URL or labeled values before secure
`0600` files are written. Samples can still contain public chat content, so
review them before sharing or promoting one into `tests/fixtures/kick/`.

## Testing

The Kick suite is offline by default. The WebSocket connector, frame iterator,
and HTTP session are injectable, so the live and replay paths run without
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
