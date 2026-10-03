# Kick Integration Guide

This guide explains how the Kick integration works in
`chat-downloader-fork`. It is intended for maintainers debugging the live
public realtime path or the REST-backed VOD and clip replay paths.

The Kick stack is split across two transport families:

- Independently negotiated Pusher or Centrifugo WebSockets for public chat
  and channel events. Anonymous Centrifugo connection tokens are requested and
  renewed automatically; no account, cookie, OAuth token, or login is needed.
- Kick's public web JSON endpoints (`api/v2` for channel, clip, and message data,
  plus `api/v1/video` for VOD metadata) and the anonymous mobile `api/v1/clips`
  fallback. When a cookie file supplies a `kick.com` `session_token`, primary
  web API calls authenticate with its URL-decoded bearer value.

Kick's OAuth-scoped official Public API is a useful schema reference, but it
does not expose the unauthenticated read-chat or replay stream this tool needs.
See [Official Public API reference](#official-public-api-reference).

The implementation uses REST and negotiated WebSockets. Fragility points
include anonymous connection descriptors and token renewal, public feed names,
WebSocket event payload shapes, and Cloudflare bot-protection on HTTP endpoints.

## What It Covers

The Kick implementation is responsible for:

- matching Kick live channel, VOD, and clip URLs
- retrieving channel, video, and web/mobile clip metadata
- streaming live chat from negotiated public WebSockets (live *and* offline channels —
  the chatroom stays active when the stream is down)
- emitting preloaded recent history and current pin state on connect, then
  deduplicating them against the live feed
- backfilling a ten-second timestamp baseline, widened conservatively for
  bounded clock or delivery skew, after reconnects and Pusher-key recovery so
  short outage gaps are not silently lost
- reading historical chat for VODs and clips by paginating the channel message
  API and filtering to the selected replay window
- parsing Kick-specific message, badge, emote, subscription, moderation, pin,
  poll, host, and public lifecycle event data
- polling anonymous channel and viewer snapshots and following category drop
  feeds as the current category changes

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
5. Negotiate `web.kick.com/api/v1/realtime/connection` and
   `realtime/channels/{channel_id}/chat/connection` independently. Each selects
   Pusher credentials or a regional Centrifugo endpoint. Sessions are isolated
   from account credentials and redirects are disabled. Open both sockets with
   the Kick browser origin and subscribe to the public feeds listed below.
   `proxy=""` opens a direct TLS socket even when environment proxy variables
   are set; a configured proxy uses the same explicit socket path. Environment
   proxy and `NO_PROXY` rules are evaluated for each negotiated hostname;
   anonymous HTTP sessions retain the explicit proxy configuration separately.
6. Confirm each subscription, answer heartbeats, decode publications, and
   dispatch typed events. Deduplicate recent chat IDs and gift chunk identities,
   filter by message groups/types, and yield. Poll anonymous channel metadata and
   current viewer counts every 60 seconds; emit only changed snapshots.
7. On a temporary failure, reconnect and renegotiate both connections. After
   the primary chat subscription is confirmed, fetch a ten-second timestamp
   baseline through the bounded clock/latency-safe envelope, emit unseen records,
   and refresh the current pin. Reconnect when the polled category changes so
   drop feeds follow current categories. Permanent Centrifugo command rejections
   terminate the run. Deadline cancellation wakes socket and queue readers.

### VODs

1. Resolve the username and video UUID from the URL.
2. Try `api/v1/video/{video_id}`. Only a video HTTP 404 activates the current
   website fallback: resolve the channel and request
   `web.kick.com/api/v1/channels/{channel_id}/videos/{video_id}` through an
   isolated anonymous session. Validate the returned video/channel identity
   and public completed state. Challenges and country restrictions are terminal.
3. Derive the UTC recording window from `start_time` plus `duration`; legacy
   duration is milliseconds, while the current website reports seconds.
   Do not substitute `end_time`: observed metadata can disagree with media duration.
   A material website `end_time` disagreement is logged and retained as signed
   `metadata_end_disagreement_seconds` in replay diagnostics.
4. Narrow the window using recording-relative request bounds.
5. Page backwards from the inclusive end using the message API's reverse
   `cursor`, buffering the selected messages in a temporary spool that spills
   to disk after one MiB. Empty pages with continuation do not establish exhaustion.
6. Emit chronologically with ID deduplication and relative time fields. Filtering
   precedes the message limit. Reading the selected history before emission
   trades startup latency for verified traversal semantics.

### Clips

1. Resolve the channel slug and clip ID from the URL.
2. Prefer `kick.com/api/v2/clips/{clip_id}` and validate the returned identity,
   source VOD UUID, channel ID, non-negative VOD offset, and positive duration.
3. When that web request, its required replay fields, or its source-VOD metadata
   are unavailable, fetch `mobile.kick.com/api/v1/clips/{clip_id}` and validate
   its `data.id`, `data.channel.id`, timezone-qualified `data.started_at`, and a
   positive duration no greater than the provider's 180-second clip limit.
   Known invalid start-time sentinels are rejected. Challenge responses remain
   terminal and do not activate the alternate endpoint.
4. On the web path, fetch the source VOD metadata and require its channel to
   agree with the clip. On the mobile path, use the returned absolute
   `started_at` and channel directly, without treating the mobile `video.id` as
   a web VOD UUID. If usable web metadata already established a channel or
   duration, the mobile response must agree with it.
5. Treat request `start_time` and `end_time` as clip-relative and clamp them to
   the clip duration. Translate them to source-VOD offsets on the web path or
   absolute timestamps on the mobile path.
6. Reverse pagination retrieves the selected clip interval and emits it in
   chronological order with clip-relative offsets.

Kick's web clip `started_at` can include a short HLS keyframe lead-in. The
preferred path therefore uses `vod_starts_at` plus `duration`, which describes
the intended source-VOD interval. The mobile fallback has no compatible web
VOD UUID and deliberately follows its absolute `started_at` contract instead.

## Module Guide

### Site entry and orchestration

- `extractor.py`: `KickChatDownloader`, URL matching, public site methods
- `live_service.py`: live chat orchestration (metadata, chatroom resolution,
  preloaded history, WebSocket loop, deduplication, reconnect)
- `replay_window.py`: pure replay bounds, cursor construction, and record classification
- `replay_service.py`: VOD orchestration (metadata, time-window pagination)
- `clip_service.py`: web/mobile clip metadata validation and source-VOD or
  absolute-time replay assembly
- `history.py`: five-second forward windows and bounded ID deduplication for
  reconnect recovery, plus shared history-page validation
- `vod_metadata.py`: legacy/current video identity reconciliation and validated
  current website metadata normalization
- `request_retry.py`: shared retry policy for transient Kick service requests

### Transport and API access

- `api_client.py`: downloader-owned HTTP client for the public `kick.com/api/v1`
  and `api/v2` channel, history, VOD, and clip JSON endpoints, plus
  `mobile.kick.com/api/v1` clip metadata. It owns endpoint status,
  challenge, JSON, and object-shape classification but does no chat parsing.
  The mobile origin uses a separate session that retains proxy, trust,
  timeout, browser-profile, and safe custom-header policy while excluding
  credential-shaped user headers and cookies from the main origin.
- `http_session.py`: constructs the client's isolated curl-cffi,
  cloudscraper, or requests transport and defines its narrow session Protocol.
- `websocket_transport.py`: the *only* module that imports `websocket-client`.
  Exposes a small parsing-free interface (`connect` / `subscribe` / `recv` /
  `send_pong` / `close`) plus the `read_frames` generator. The connector is
  injectable so the live path is fully unit-testable without network access.

- `realtime_connection.py`: isolated anonymous negotiation and token requests.
- `centrifugo_transport.py`: public protocol replies, publications, and lease renewal.
- `public_transport.py`: independent connections, bounded queues, feed acknowledgements,
  and snapshot polling.
- `public_state.py`: anonymous channel/viewer state and category discovery.
- `live_iterator.py`: interruptible ownership of the active live transport.
- `capture_inspection.py`: content-free JSONL validation and live/replay ledger
  reconciliation shared by automatic live verification and the offline command.

### Parsing and shared Kick data

- `parsing/public_events.py`: complete public lifecycle payloads and gift chunks.
- `parsing/events.py`: public frame dispatch. Maps raw event names to
  normalized message types via `EVENT_NAME_MAP`, then to parser functions via
  `_PARSER_DISPATCH`. Decodes Kick's double-encoded `data` field. Control
  frames are omitted from output; unknown events are captured when explicitly
  enabled, debug-logged by name, and skipped; a `pusher:error` frame raises
  `KickError`. Live-only compact subscription/host and empty-array pin-clear shapes
  plus ID-less poll state events are expanded with namespaced receive-time IDs
  before normal parsing.
- `parsing/messages.py`: chat-message normalization for both live
  `ChatMessageEvent` payloads and preloaded history (same shape); badge and
  timestamp handling, including reply context from object- or string-encoded
  metadata. Subscription-renewal `celebration` payloads remain text messages
  while retaining their provider ID, kind, total-month count, and normalized
  event timestamp under `metadata.celebration`. Entry points `parse_chat_message` /
  `parse_preloaded_messages` (eager list) / `iter_preloaded_messages` (streaming).
  Sender badges merge Kick's legacy `badges` and
  image-backed `badges_v2` arrays in stable provider order. Structured output
  retains v2 image URLs, selection state, badge type, provider metadata, and
  sort order without applying the mobile client's display-count limit.
- `parsing/emotes.py`: inline emote-marker parsing (`[emote:ID:NAME]` →
  `:NAME:` in plain text, or `:emote_ID:` when no name is present) and
  structured emote metadata/image URLs.
- `parsing/subscriptions.py`: `SubscriptionEvent` and
  `GiftedSubscriptionsEvent` normalization.
- `parsing/moderation.py`: ban, unban, message-delete, and chat-clear
  normalization, including temporary-ban duration/permanence, Kick's
  AI-moderation flag, and violated-rule labels.
- `parsing/pins.py`: pinned-message created/deleted normalization.
- `parsing/polls.py`: poll update/delete normalization. Updates retain the poll
  title, countdown, result-display duration, options, vote counts, and optional
  viewer state. Delete payload contents are intentionally ignored because the
  provider frontend treats the event itself as the complete state signal.
- `parsing/hosts.py`: stream-host normalization.
- `constants.py`: URL patterns, REST endpoints, Pusher/event name constants,
  event-to-message-type and
  message-type-to-group maps, emote patterns, and Cloudflare markers.
- `pusher_discovery.py`: default-first Pusher application-key selection,
  best-effort refresh, cache ownership, and WebSocket URL construction.
- `errors.py`: `KickError`, the terminal `KickCountryBlocked` subtype, and the
  retryable `KickServerError` subclass.

There is no `client.py` facade in the Kick package. Import focused modules
directly for patch points: `api_client.py` for REST, `public_transport.py`
for negotiated live feeds, `websocket_transport.py` for socket/Pusher mechanics,
and `parsing/` for message shaping.

## Official Public API reference

The official Kick Dev Docs are useful maintenance references, but they are not
drop-in replacements for this tool's current capture path.

Use the official [API documentation](https://docs.kick.com/) and
[documentation changelog](https://github.com/KickEngineering/KickDevDocs/blob/main/changelog.md)
when reviewing these external surfaces. Runtime behavior remains defined by
this repository's code and configuration; fixtures and tests verify its contracts.

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
  `https://kick.com/api/v2/clips/{clip_id}`,
  `https://mobile.kick.com/api/v1/clips/{clip_id}`, or the VOD
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
because the live path keeps the public chat connection available regardless of
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

### Public realtime transports

`public_transport.py` owns two independent negotiated connections and a bounded
publication queue. Each protocol transport receives complete connection
configuration at construction; the public owner does not change its private
socket settings. Live orchestration uses the same cancellable iterator for
production and injected transports. Cancellation during setup closes late
resources before another connection or subscription can begin, and terminal
setup failures close the transport without retrying.

The website's primary `chatrooms.{chatroom_id}.v2` feed uses
its channel-chat connection; the other feeds use the global connection:

| Public feed | Available events |
| --- | --- |
| `chatrooms.{chatroom_id}.v2` | Chat, deletion, ban/unban, subscription, clear, pin, poll |
| `chatrooms.{chatroom_id}` | Stream hosting |
| `chatroom_{chatroom_id}` | Gifted subscription chunks and reward redemption |
| `channel.{channel_id}` | Stream start/stop and chat movement |
| `channel_{channel_id}` | Settings, Kicks, gift/Kicks leaderboards, goals, participants |
| `predictions-channel-{channel_id}` | Prediction creation and updates |
| `drops_category_{category_id}` | Category drop campaign start |

Anonymous probes on 2026-10-03 confirmed all these subscription families,
including predictions. Subscription confirmation establishes access, not proof
that every rare event occurred during the sample. Curated fixtures distinguish
observed wire contracts from examples reconstructed from website bindings.

Pusher uses negotiated application keys and empty public subscription auth.
The legacy `pusher_discovery.py` compiled-key/bundle scan remains available for
its standalone transport and maintenance tests; production reconnects perform
fresh anonymous negotiation rather than relying on that historical marker.

Centrifugo translates newline-batched replies and publications into the same
internal event envelope. Command acknowledgement waits are bounded. Empty JSON
ping frames receive empty JSON pongs when the server requests them. Connection
leases renew before expiry through anonymous `realtime/auth/connection`; tokens
are never logged or persisted. Temporary command errors reconnect; permanent
rejections terminate. See the [Centrifugo JSON protocol](https://centrifugal.dev/docs/transports/client_protocol).

Both providers use short receive polls, bounded subscription waits, and idle
watchdogs. Invalid shapes and unsupported event names remain visible in bounded
diagnostics and opt-in sanitized samples. Counters include provider connections,
confirmed public subscriptions, snapshot polls/failures, WebSocket frames, and
synthetic REST frames separately. These fixed counters are retained in manifests.

Channel snapshots supply public livestream status, title, category, follower
count, and chat settings. `kick.com/current-viewers` supplies viewer counts for
the current livestream. These are polled observations with receive timestamps,
not private push events. Viewer-count text summaries include the count when the
snapshot contains a single nonnegative integer count, including zero (for
example, `[viewer count: 42]`). Empty or unrecognized snapshots retain the
`[viewer count]` label without assuming a count. Public lifecycle events retain
their complete payload under `metadata.data`, plus event name, channel, and
source. A provider entity ID is not used for deduplication because successive
state updates may share it.
Compact gifts preserve `gifted_total`, `gifter_total`, and `chunk_details` under
`metadata.kick_event.data`; normalized `quantity` counts recipients in that
chunk, preventing the overall gift total from being counted once per chunk.

Private account, points, notifications, ads, age verification, and
`private-livestream` update feeds remain outside anonymous coverage. Public
metadata polling supplies snapshots of livestream changes. Reconnect history
recovers chat and the current pin; the public APIs do not provide equivalent
history for arbitrary lifecycle events, so transitions during an outage can be
missed. Snapshot polling may miss transitions between polls. Long in-flight HTTP
requests can delay shutdown; incomplete deadline accounting is explicitly
reported for review by automatic live verification and the offline inspector
rather than certified.

The live service reports truncated reconnect windows and uncovered time;
both automatic and offline inspection flag these known gaps for review.
The inspector subtracts counted deadline-prefetched records when reconciling
emissions against persisted records. The default filter remains `messages`.
Live URLs cannot seek with `start_time` or `end_time`; use a VOD or clip URL.

### Live diagnostics

Each live `Chat` exposes this schema in `chat.diagnostics`. Debug run summaries
retain it on success and failure. Run manifests select bounded integer counters
from it. Automatic live inspection uses in-memory run counts and diagnostics;
the offline inspector uses the debug summary for reconciliation.

| Field | Meaning |
| --- | --- |
| `websocket_frame_count` | Decoded socket event envelopes |
| `control_frame_count` | Recognized connection, subscription, and heartbeat controls |
| `parsed_event_count` | Events successfully normalized by the parser |
| `unsupported_event_count` | Unrecognized event names skipped by dispatch |
| `unknown_message_type_count` | Chat payloads with an unrecognized subtype |
| `malformed_event_count` | Known events rejected for malformed payloads |
| `malformed_event_type_counts` | Bounded per-type malformed-event counts |
| `invalid_websocket_frame_count` | Invalid frame JSON or envelope shapes |
| `websocket_reconnect_count` | Successful reconnects after temporary connection failures |
| `pusher_error_count` | Pusher error events |
| `pusher_key_recovery_count` | Successful recovery reconnects after Pusher protocol errors |
| `pusher_connection_count` | Opened negotiated Pusher connections |
| `centrifugo_connection_count` | Opened negotiated Centrifugo connections |
| `public_state_poll_count` | Successful public channel metadata polls |
| `public_state_poll_failure_count` | Failed channel metadata or viewer polls |
| `public_subscription_count` | Confirmed public feed subscriptions |
| `synthetic_frame_count` | Event envelopes generated from REST snapshots |
| `preloaded_emitted_count` | Emitted startup history and current pin records |
| `live_emitted_count` | Emitted socket and REST snapshot records |
| `reconnect_backfill_emitted_count` | Emitted reconnect history and current pin records |
| `reconnect_backfill_truncated_count` | Outages extending beyond the bounded recovery window |
| `reconnect_backfill_truncated_microseconds` | Accumulated uncovered time before the recovery floor |
| `last_websocket_frame_timestamp` | UTC receive microseconds of the last decoded or synthetic frame |

### Dedup and filtering

Before yielding, the live service:

- deduplicates against a bounded `_SeenMessageCache`
  (`_KICK_LIVE_SEEN_MESSAGE_LIMIT = 10_000`) keyed on `message_id`
- filters through configured message groups and types via `MessageFilter`

### Reconnect

The receive loop runs under a reconnect wrapper: a `ConnectionError` closes the
transport, reopens it, and resubscribes, retrying per the request's retry
policy. The legacy Pusher seam retains its one-shot key-discovery recovery;
production transports renegotiate both descriptors. Recovery waits for the
primary chat feed acknowledgement or a text chat message before history
backfill. A lifecycle feed acknowledgement or REST snapshot cannot confirm
that primary chat has resubscribed. They then query forward timestamp history from the newest provider
timestamp already observed, or from the last parsed message's receive timestamp
when no provider time exists. When the latest provider/client clock delta is
within the ten-second recovery window, the envelope uses the earlier of local
confirmation and the provider-time estimate for its ten-second floor, and the
later value for its query end. This treats a negative timestamp delta as either
clock skew or ordinary delivery latency without opening a gap; larger or
malformed offsets are ignored. A future or absent checkpoint uses the full
baseline window. The reconnect fetch is additionally capped at 100 pages and
10,000 raw records so a degraded endpoint cannot indefinitely block the
confirmed socket. A fresh call to the preload endpoint is always reconciled as
a best-effort, time-filtered secondary source, with forward history preferred
for overlapping message IDs. Its current pin state is emitted after recovered
messages. The loop carries `# noqa: C901` for its intrinsic branchiness.

## Replay Capture Details

VOD and clip chat use `api/v2/channels/{id}/messages`. Reverse pagination
passes the returned cursor unchanged and emits the selected history from a
bounded-memory spool. Repeated pages or cursors fail as incomplete retrieval,
instead of reporting a successful truncated capture. The final debug summary
includes the protocol, page and record counts, selected/observed timestamps,
HTTP status totals and latency, and an explicit termination reason. Deadline
cancellation stops buffered pagination between page fetches. An in-flight
fetch remains subject to the configured HTTP timeouts and retry policy.

Forward `start_time` queries describe five-second windows. The returned cursor
is not a forward continuation token: converting it into the next query start
skipped most messages in an observed recording. Reconnect recovery follows the
website's five-second increments, including empty windows, within its existing
page and record limits. Curated sparse-window fixtures and real-client
composition tests document that distinction.

Replay messages include `time_in_seconds` and `time_text` relative to the VOD
or clip origin. Request-relative bounds do not reset that origin. Absolute
`timestamp` values and provider metadata remain in JSONL.

For recoverable shutdown checkpoints and automatic JSONL/TXT verification, see
[the CLI guide](cli-usage.md#replay-checkpoints-and-output-verification).

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
| `polls` | `poll_update`, `poll_deleted` |
| `channel` | `stream_started`, `stream_stopped`, `chat_moved`, `chat_settings_changed`, `chatroom_updated`, `channel_metadata` |
| `rewards` | `reward_redeemed` |
| `kicks` | `kicks_gifted`, `kicks_gifted_deleted` |
| `leaderboards` | `gifts_leaderboard_updated`, `kicks_leaderboard_updated` |
| `goals` | `goal_created`, `goal_updated`, `goal_progress_updated`, `goal_achieved`, `goal_canceled` |
| `events` | `event_participant_joined`, `event_participant_left` |
| `drops` | `drops_campaign_started` |
| `viewers` | `viewer_count` |
| `predictions` | `prediction_created`, `prediction_updated` |

The default message group surfaces only `messages`. Use `--message_groups all`
for full-spectrum diagnostics, or pass a comma-separated subset such as
`messages,subscriptions,moderation` when only selected non-text events are
needed. `all` is the shared unfiltered selector rather than an entry in the
site-specific group map. Kick `celebration` chat payloads remain in `messages`
because they carry user-authored chat text; their subscription-renewal details
remain available as structured JSONL metadata.

Kick TXT replies include `[replying to NAME]`, preferring the parent author's
display name and falling back to their name. When only a parent message or
thread ID is available, TXT uses `[reply to message ID]`. Ordinary messages
retain their existing rendering; JSONL retains the full reply context.

Kick's default text formatter labels subscription, pin, host, and moderation
events. Empty-message events such as deletions and chat clears render bracketed
notices rather than blank lines. Live WebSocket events without a valid provider
`timestamp` retain a distinct UTC-microsecond `received_timestamp`; the Kick
formatter uses it only as a fallback and marks it `[received]` in TXT.
Compact subscriptions containing only `chatroom_id`, `username`, and `months`
retain the username and month count; empty-array pin deletions emit an ID-less
provider state change as `[Pinned message removed]`. Both receive namespaced
IDs derived from their live receive timestamp so JSONL records remain complete.
Preloaded history plus VOD and clip replay do not receive this live-arrival
field.
AI-moderated deletion notices append `[AI moderated]` and any violated-rule
labels, while ordinary deletion notices stay compact.
Poll events are live-only and opt-in through `polls` or `all`. Kick does not
supply IDs or provider timestamps for the observed poll frames, so both event
types receive monotonic, namespaced receive-time IDs and
`received_timestamp`. Poll updates preserve each changing state rather than
deduplicating by title; TXT labels the update or deletion while JSONL retains
the structured option and vote data.

## Cloudflare Dependency

The REST endpoints sit behind Cloudflare. `http_session.py` uses a three-tier
session strategy for the client-owned transport. Standard installations include
all three dependencies; the fallbacks also keep degraded or partial
environments diagnosable:

1. **curl-cffi with its current Chrome TLS impersonation alias** — tracks the
   dependency's supported browser identity without a stale version pin.
2. **cloudscraper** — JS-challenge solver for simpler challenges (used if
   curl-cffi cannot be imported).
3. **Plain requests session** with browser-like headers — last resort when
   neither specialized backend can be imported.

When a response body looks like a challenge page (Cloudflare markers, or an HTML
body where JSON was expected) or returns HTTP 403, the client raises
`CaptchaChallengeRequired` — neither curl-cffi nor cloudscraper could bypass the
challenge (modern Cloudflare challenges may require tooling updates or an
endpoint with better IP reputation).

Status mapping in `api_client.py::_check_status`:

- channel `404` → `UserNotFound`; video `404` → the internal
  `KickVideoNotFound` signal used by the current website fallback; clip and
  history `404` → `KickError` (the clip service may then try mobile metadata)
- `403` / challenge body → `CaptchaChallengeRequired`
- provider-specific `423` → `KickCountryBlocked` (terminal; no fallback/retry)
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
- web clip metadata identity, source-VOD, channel, `vod_starts_at`, and
  `duration` fields, plus mobile fallback `data`, `started_at`, and channel
  fields
- divergence between official webhook schemas and live Pusher payloads; the
  official docs are schema hints, not authoritative contracts for the Pusher
  path

When debugging Kick breakage, inspect modules in this order:

1. `api_client.py` — REST status mapping and challenge detection
2. `http_session.py` — optional backend selection and session setup
3. `realtime_connection.py` — anonymous connection negotiation and token requests
4. `public_transport.py`, `centrifugo_transport.py`, and `websocket_transport.py`
   — public subscriptions, framing, heartbeats, snapshots, and reconnect signals
5. `constants.py` — endpoints and event/group maps
6. `live_service.py` or `replay_service.py` — service-layer orchestration
7. `parsing/events.py` and per-event parsers — dispatch and field assembly

For failures in the legacy standalone Pusher transport, also inspect
`pusher_discovery.py` for compiled-key selection and bundle discovery.

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
control, unknown, and malformed frames.

Replies, emote-bearing text, and badge-bearing text each have an additional
independent three-attempt quota under `text-shape-in-reply-to`,
`text-shape-emotes`, and `text-shape-badges` labels. Shape detection uses the
normalized reply, emote, and author-badge fields. These quotas add at most nine
samples per run; overlapping shapes can capture the same frame under multiple
labels. All quotas span reconnects, run before deduplication and message
filtering, and retain the shared capture opt-in, redaction, and private-file
requirements. Failed writes consume an attempt. Shared per-label limits can
reduce the number of new samples when reusing a directory in one process.

Example:

```bash
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES=1 \
chat_downloader "https://kick.com/xqc" --logging debug
```

Set `CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR` to retain samples in a chosen private
directory. With debug sampling enabled, CLI and public API retrieval preflight
this directory before opening a provider session. Existing directories must be
owned by the current user, have mode `0700`, and not be symlinks; an unsafe
directory stops retrieval with an actionable error before sampling quotas are
consumed. Missing directories are created privately. Each write still checks
directory and file safety. Most anomaly labels capture at most ten unique
payloads per process and directory. Unsupported event names use isolated
three-sample labels plus a ten-sample aggregate cap, so a noisy event cannot
hide a different event name.
Successful frame capture attempts at most three payloads per normalized event
type and retrieval run. Type-specific labels make the sampled surface visible
without opening every file. The shared sanitizer redacts
credential-bearing fields and sensitive URL or labeled values before secure
`0600` files are written. Samples can still contain public chat content, so
review them before sharing or promoting one into `tests/fixtures/kick/`.

Compact live `StreamHostEvent` payloads carry `host_username`, `number_viewers`,
`chatroom_id`, and optional `optional_message` without an event ID. Valid shapes
receive a namespaced `kick-stream-host:` ID based on monotonic receive-time
microseconds. JSONL preserves host/viewer metadata and a separate
`received_timestamp`; no provider timestamp is invented. TXT includes the host,
viewer count, and any optional message. Existing wrapped host payloads keep
their provider IDs and timestamps. Blank IDs are rejected rather than repaired.

For offline inspection of a completed all-groups live capture, run:

```bash
uv run python scripts/inspect_kick_capture.py capture.jsonl --debug-log debug.log
```

This detects recorded parser drops as well as output/diagnostic count gaps;
it supplements exact TXT/JSONL parity. See the
[capture inspection workflow](development-workflow-guide.md#kick-capture-inspection)
for report and exit-code semantics.

For live capture, `--verify_output` automatically runs this inspector after
shutdown using the same record checks and counter reconciliation without a
debug log. The content-free report appears in the run result, debug summary,
and manifest under `provider_inspection`; `review` or `error` fails the run
even when JSONL/TXT parity passes. Offline-channel chat uses the same behavior.
Partial captures after retrieval errors are inspected while preserving the
original error. Kick VOD and clip verification remains parity-only: replay
inspection uses the offline command with one debug log per appended run.

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

### External replay validation

`tests/test_kick_recorded_replay_unit.py` composes the real metadata client,
reverse paginator, parser, and recording-relative timing against curated
responses from a public Sam recording. The fixture retains four cursor-linked
pages, same-second messages, two records before the selected window, emotes,
and string-encoded reply metadata. Its provenance records the source URL,
capture time, original page counts and hashes, and all curation steps.

The external recording check requires an explicitly selected contract because
Kick VODs expire. A contract contains the URL, recording ID, relative start/end,
independently observed message IDs and timestamps, and plain-text probes. While
the recorded Sam asset remains available, run:

```bash
KICK_TEST_REPLAY_CASE=tests/fixtures/kick/replay_network_case_sam.json \
  uv run pytest -q tests/test_kick_replay_network.py --run-network
```

This test uses the `network_environment` scope because the asset must be
configured and refreshed. It does not run in the weekly stable replay job.
Without a configured contract it reports a skip; with one, expired assets,
challenge blocks, missing messages, and protocol failures fail the check.
Refresh the contract from independently fetched provider responses rather than
using downloader output as its own expected result. Observed history
timestamps have second precision, so checks require chronological timestamps
and exactly one occurrence of each expected ID without imposing an order on
equal times.

For external validation, capture a bounded window with `--require_complete`,
`--verify_output`, `--run_manifest`, and debug logging, then inspect it with
`scripts/inspect_kick_capture.py`. Compare all selected IDs and timestamps
against separate HTTP history requests and confirm text probes directly from
those responses. Keep raw responses privately, review their contents, and
promote only curated fixtures. API agreement establishes correct retrieval of
the history Kick currently supplies; it cannot prove that every original live
message was retained by the provider.

## Replay audit diagnostics

Replay summaries retain the aggregate `skipped_records` counter and add
`before_start`, `after_end`, `malformed_timestamp`, `malformed_object`, and
`parse_error` reason counts. `selected_records` counts eligible buffered records
before message limits or checkpoint overlap suppression. Expected boundary
exclusions do not imply parser loss. Collection progress is emitted at info
level, with page/record counts, elapsed time, and the earliest timestamp on the
current page; no message content is logged.

`scripts/inspect_kick_capture.py capture.jsonl --debug-log run.log` detects replay
summaries and reconciles raw, skipped, filtered, duplicate, selected, emitted,
and written counts independently of live Pusher accounting. It reports transport
statuses, selected/observed time bounds, termination and parity. For appended
replay output, repeat `--debug-log` with every run log in append order. Each log
must contain exactly one summary. Missing runs cause accounting mismatches;
incomplete captures require review even when their JSONL/TXT parity passed.
Logs alone cannot authenticate archive provenance or provider completeness.

The generic `--require_complete` and `--run_manifest` controls are documented in
[CLI usage](cli-usage.md#replay-completion-and-run-manifests). Reverse pagination
and duration-derived VOD cutoffs remain authoritative; progress and metadata
notices do not change traversal behavior.
