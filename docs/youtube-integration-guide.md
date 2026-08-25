# YouTube Integration Guide

How the YouTube integration works in `chat-downloader-fork`. For maintainers and
advanced users who need a reliable mental model of the capture pipeline.

## What It Covers

The YouTube implementation is responsible for:

- matching supported YouTube URLs
- discovering channel, handle, custom-user, playlist, and live-page targets
- loading video metadata and chat bootstrap state
- selecting live chat versus replay chat
- polling continuation endpoints
- parsing platform actions into normalized message dictionaries

Primary entry point:

- `src/chat_downloader/sites/youtube/extractor.py`

Public site methods are assembled from focused mixins and include
`get_chat_by_video_id`, `get_chat_by_clip_id`, `get_chat_by_channel_id`,
`get_chat_by_user_id`, `get_chat_by_custom_username`, `get_chat_by_handle`,
`get_user_videos`, and `get_playlist_items`.

Main implementation areas:

- `src/chat_downloader/sites/youtube/`
- `src/chat_downloader/sites/youtube/parsing/`

## End-to-End Flow

The normal YouTube flow is:

1. Match the incoming URL to a supported YouTube target.
2. Resolve a channel, user, or handle `/live` shortcut to the canonical video
   ID so video-specific playability errors remain visible.
3. Load the watch page and parse initial JSON state.
4. If the watch page is challenged or cannot be parsed, fall back to the
   InnerTube `player` and `next` endpoints for live-video bootstrap metadata.
5. Extract video details, playability information, client config, and initial
   chat continuation hints.
6. Load the chat page once to recover the active `Top chat` and `Live chat`
   continuation tokens.
7. Build browser-like request headers, optionally adding auth headers when
   cookies are available.
8. Poll the private InnerTube chat continuation endpoint.
9. Parse actions into normalized chat messages.
10. Continue until replay data ends, live continuations stop, or the caller
   reaches a configured limit.

## Module Guide

### Bootstrap and metadata

- `extractor.py`: top-level `YouTubeChatDownloader`
- `video_initialization.py`: video bootstrap plus initial chat continuation
  recovery
- `video_metadata.py`: metadata helpers
- `playability.py`: YouTube error-screen, popup, and replay-availability
  checks mapped to downloader exceptions
- `video_status.py`: canonical internal video-status parsing boundary;
  delegates to `video_status_helpers.py` (`_determine_status`,
  `_determine_video_type`, `_extract_continuation_info`) and
  `video_status_models.py` (`VideoDetails`)
- `discovery.py`: cohesive channel/handle discovery, browse pagination, and
  live-page test URL generation
- `chat_users_router.py`: channel/user/handle routing and direct `/live`
  shortcut resolution
- `discovery_playlists.py`: playlist discovery and pagination

### Request construction

- `client_context.py`: request headers and client context construction
- `client_requests_bootstrap.py`: fallback InnerTube `player` and `next`
  bootstrap requests
- `client_requests_initial.py`: initial request helpers
- `client_requests_continuation.py`: continuation request helpers
- `client_requests_errors.py`: shared HTTP, JSON, CAPTCHA, and retry
  classification for request helpers
- `client_auth.py`: cookie initialization, SAPISID cookie parsing, and auth
  header derivation

### Continuation loop

- `chat_streams.py`: site-level chat entry methods
- `continuation.py`: cohesive live/replay polling loop, request-profile
  fallback, response handling, and action iteration
- `continuation_helpers.py`: pure state, timing, filter, and URL helpers
- `chat_users_router.py` and `chat_users_retrieval.py`: optional chat-user
  lookup routing and retrieval helpers
- `continuations.py`: continuation parsing models and utilities
- `message_pipeline.py`: filtering and action-to-message pipeline boundary

### Parsing

- `parsing/actions_router.py`, `parsing/actions_handlers_parser.py`, and
  `parsing/actions_handlers_validation.py`: action routing, parsing, and
  validation
- `parsing/message_content_badges.py`,
  `parsing/message_content_text_parser.py`,
  `parsing/message_items_content_parser.py`,
  `parsing/message_items_video.py`, and `parsing/message_links.py`: message
  content, links, video metadata, and shared normalization helpers
- `parsing/__init__.py`: package-level parsing surface; focused modules own the
  implementations directly

## Capture Behavior

### Watch-page bootstrap

The downloader starts with the YouTube watch page. That page provides:

- `ytInitialData`
- `ytcfg`
- playability metadata
- initial continuation hints

This is enough to determine whether the target is live, replay, post-live, or
unavailable.

When the watch page is blocked by a YouTube/Google challenge or the page no
longer exposes parseable initial JSON, regular video targets can fall back to
InnerTube `player` and `next` requests. That fallback preserves the active
request profile, builds a minimal `ytcfg`, and seeds live chat with the primary
`liveChatRenderer` continuation from the `next` response. The fallback is
intended for live-video bootstrap; clips remain on the normal page bootstrap
because clip time ranges are page-specific.

### Chat-page continuation recovery

The integration then loads either the live chat page or the live chat replay
page once to recover the active chat-view continuation tokens.

This matters because the watch page alone is not always enough to choose the
right continuation reliably, especially when both `Top chat` and `Live chat`
views exist.

### Header and auth preparation

Before polling continuations, the downloader builds browser-style headers.

If the cookie jar contains the right auth cookies, the code can also attach
SAPISIDHASH-style authorization headers. Public chats often work without
cookies, but cookies help when YouTube wants stronger session context.

### Continuation polling and parsing

The runtime repeatedly calls the InnerTube continuation endpoint with:

- the current continuation token
- the current client context
- replay offsets when replay chat is being read

Each response is parsed for actions, new continuation tokens, and timeout
hints. YouTube chat polling respects server-provided delay values when present
and otherwise falls back to five seconds, clamped to 0.5-8 seconds. Completed
replays may explicitly override that delay with `youtube_replay_poll_interval`;
the bounded override is opt-in because faster polling can be rate-limited. This
polling delay is separate from HTTP connect/read timeout settings.

Actions then pass through the message pipeline, which:

- filters by requested message groups and message types
- applies replay time-range filtering
- maps YouTube-specific payloads into the shared output schema
- adds timing metadata when enough context exists

## Live and Replay Semantics

### Live streams

For live streams, polling continues until:

- YouTube stops returning usable continuations
- the caller hits `max_messages`, `timeout`, or `inactivity_timeout`
- an unrecoverable error occurs

The live path also derives capture-relative presentation timing when a message
timestamp is available. Messages returned from before retrieval started keep a
negative `time_in_seconds` value and signed `time_text`; later messages use
positive values. The separate InnerTube `playerOffsetMs` polling position stays
nonnegative and never moves backward when YouTube delivers an older message
late.

### Replays and completed streams

For replay content, the loop uses replay offsets and a `TimeRangeFilter` to
support `start_time` and `end_time`. Before-start actions remain skippable
across continuation-page boundaries because dense chats can require more than
one response to reach the requested offset. Those pages must still advance the
continuation token or their greatest replay offset; repeated stale responses
eventually trigger the bounded no-progress guard.

Replay-wrapper offsets are used only when they are finite and nonnegative.
Positive offsets provide authoritative millisecond precision. At the zero
preroll floor, nested renderer display timing remains authoritative so signed
negative paid and ticker items stay aligned. Malformed offsets fall back to
the renderer's display timing instead of aborting the replay or replacing
valid nested timing. Clip offsets are applied once after nested renderer timing
is merged.

`NoChatReplay` is most likely to appear in replay mode, when YouTube simply
does not expose replay chat for the target.

### Request profiles and fallback

`request_profile` can select `youtube_web`, `youtube_android`, or
`youtube_ios`. The CLI and Python API both carry this through
`DownloaderConfig`. Any other profile name raises `ValueError` during
configuration.

When `auto_profile_fallback` is enabled, initial bootstrap rotates YouTube
profiles when an `UNPLAYABLE` response contains only a generic reason such as
`Video unavailable`. A more specific response (for example, a country
restriction) is surfaced without further rotation. The continuation loop can
also rotate profiles after repeated incomplete continuation payloads. The
fallback logic lives in `video_initialization.py` and `continuation.py`;
profile headers and InnerTube context adjustments live in `client_context.py`
and `request_profiles.py`.

The session owns profile-generated headers. Explicit `--user-agent` and
`--header` overrides remain unchanged during fallback, as do runtime headers
such as authorization and visitor identifiers.

The initial InnerTube fallback uses the same request-profile context fields
when constructing its `player` and `next` payloads. Continuation-loop profile
fallback remains separate and still handles incomplete chat-poll responses
after bootstrap has succeeded.

Android and iOS `next` responses use mobile-specific `playerOverlays` and
`engagementPanels` layouts rather than the desktop conversation bar. The
bootstrap recognizes their filter-mode models to preserve distinct Top and
Live chat selections. Mobile continuation responses may also wrap text
messages in `elementRenderer`; these are normalized into the same text-message
shape used by the web client, including author identity, membership badge,
avatar, message ID, and timestamp fields.

`client_auth.py` handles SAPISIDHASH-style authorization when suitable cookies
are available. Header values are sanitized before they appear in debug logs.
Cookie-authenticated sessions reject effective remote proxies, including
proxies inherited from the environment. A loopback proxy emits a warning;
`proxy=""` explicitly disables environment proxies.

## Observed Live Message Types

The current implementation has been exercised against these live YouTube
message types:

- `text_message`
- `viewer_engagement_message`
- `banner`
- `paid_message`
- `paid_sticker`
- `ticker_paid_message_item`
- `ticker_paid_sticker_item`
- `membership_item`
- `ticker_sponsor_item`
- `sponsorships_gift_purchase_announcement`
- `sponsorships_gift_redemption_announcement`
- `gift_message_view_model`
- `banner_chat_summary`
- `ban_user` (confirmed via `remove_chat_item` action; see Moderation Actions
  below for the full mapping)

`gift_message_view_model` covers both regular `giftMessageViewModel` chat items
and Jewels-powered `updateOrAddInteractivityWidgetAction` gift attributions.
The widget form preserves the gifter identity, gift image and accessibility
label, and `combo_count`; repeated widget updates keep their shared message ID
so consumers can track a growing gift combo.

`ban_user` is the normalized `message_type` for three distinct wire actions:
`remove_chat_item`, `remove_chat_item_by_author`, and
`mark_chat_items_by_author_as_deleted`. All three appeared in fixture captures
from the Shu/Pokopia and Shapy/Crimson Desert live streams
(`tests/fixtures/youtube/live_events/`).

`deleted_message` (from `markChatItemAsDeletedAction`) is implemented but has
not yet appeared in any observed live stream capture.

The parser also recognizes these live-chat renderer shapes from YouTube.js
coverage, with synthetic regression tests in
`tests/test_youtube_parsing_actions_unit.py`:

- `purchased_product_message` from `liveChatProductItemRenderer`
- `auto_mod_message` from `liveChatAutoModMessageRenderer`
- `restricted_participation` from
  `liveChatRestrictedParticipationRenderer`

## Moderation Actions

YouTube exposes four wire-level actions related to moderation. Three of them
normalize to `message_type = "ban_user"` in the output dictionary; one
normalizes to `message_type = "deleted_message"`.

| Wire action key | `action_type` in output | Notes |
| --- | --- | --- |
| `removeChatItemAction` | `remove_chat_item` | Removes a specific message; carries `target_message_id` |
| `removeChatItemByAuthorAction` | `remove_chat_item_by_author` | Author-wide ban; carries `author.id` (`externalChannelId`), `author.name = ""`, `message = null` |
| `markChatItemsByAuthorAsDeletedAction` | `mark_chat_items_by_author_as_deleted` | Author-wide retroactive delete; carries `author.id` and optionally `message` from `deletedStateMessage.runs[].text` |
| `markChatItemAsDeletedAction` | `mark_chat_item_as_deleted` | Single-message delete; normalizes to `message_type = "deleted_message"`, not `ban_user`; implemented but not yet observed in live captures |

The mapping from wire action key to output `action_type` and `message_type`
lives in `_KNOWN_REMOVE_ACTION_TYPES` in
`src/chat_downloader/sites/youtube/constants_actions_messages_core.py`.

## Capture and fix parser drift

When a real stream produces a new renderer or action type that the parser
doesn't recognize, the runtime emits a debug sentinel and can save a sanitized
snapshot. See [Debug sample capture](development-workflow-guide.md#debug-sample-capture)
for capture configuration. Promote reviewed samples into
`tests/fixtures/youtube/`.

To turn a captured drift sample into a permanent regression anchor:

1. Reproduce the failure with
   `CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1` to confirm the snapshot file
   is written.
   When a clean raw response is also useful, set
   `CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES=1`; this captures at most the
   first three structurally valid responses and requires debug logging.
2. Read the snapshot. The `"Unknown action"` variant has
   `{"action": {...}, ...}`; the `"Missing keys"` variant has
   `{"original_item": {...}, ...}`.
3. Update the relevant parser contract:
   - New action type → add to the right constant set in
     `src/chat_downloader/sites/youtube/constants_actions_messages_core.py`.
   - New renderer → also ensure the derived `message_type` (strip `liveChat`
     prefix and `Renderer` suffix, then `camel_case_split`) appears in a group
     in `_MESSAGE_GROUPS` inside
     `src/chat_downloader/sites/youtube/constants_message.py`; the invariant
     test `test_every_routed_renderer_has_a_message_group` (in
     `tests/test_youtube_remapping_invariants_unit.py`) will fail if you miss
     this step. Contentless transient UI renderers that must never be emitted
     belong in `_KNOWN_IGNORE_MESSAGE_TYPES` instead and must not have a public
     message group; `test_ignored_renderers_are_not_advertised_as_message_types`
     guards that contract.
   - New field → add to `build_remapping()` in
     `src/chat_downloader/sites/youtube/constants_message.py`; `known_keys()`
     is derived from that mapping.
   - New field to suppress → add to `_KEYS_TO_IGNORE` in the same file;
     `test_remapping_contributor_sets_are_disjoint` will catch any duplicate
     between `build_remapping()` and `_KEYS_TO_IGNORE`.
4. Save the raw InnerTube continuation JSON that triggered the drift in
   `tests/fixtures/youtube/live_events/` with a descriptive name.
5. Run the drift harness, then the canonical validation:
   ```bash
   uv run pytest -q tests/test_youtube_drift_harness_unit.py
   make ci
   ```
   It parameterizes over every dictionary-shaped `live_events/*.json` file and asserts
   no drift sentinel fires. If the harness passes, the fix is complete and the
   fixture is a permanent regression anchor.

The four sentinel phrases checked by the harness are `"Unknown action"`,
`"Unknown message type"`, `"Missing keys found"`, and
`"Parse of action returned empty"`.

## Common Failure Points

The YouTube stack is most sensitive to changes in:

- watch-page JSON structure
- chat-page bootstrap structure
- live discovery page structure
- InnerTube continuation response structure
- required request headers
- auth and cookie behavior

The initial watch-page fetch raises `RetriesExceeded` after all retry attempts
on 5xx responses are exhausted.
Invalid or negative continuation delay values from the InnerTube response use
the five-second polling fallback unless a completed replay has an explicit
`youtube_replay_poll_interval` override.

When debugging YouTube breakage, inspect modules in this order:

1. `video_initialization.py`
2. `discovery.py`
3. `client_context.py`
4. `client_auth.py`
5. `client_requests_continuation.py`
6. `continuation.py`
7. `continuations.py`
8. `parsing/`

## Testing

Bootstrap, continuation, discovery, and parser coverage is offline by default.
Reviewed drift fixtures live under `tests/fixtures/youtube/`; live-network
tests must carry `@pytest.mark.network` and require `--run-network`.
