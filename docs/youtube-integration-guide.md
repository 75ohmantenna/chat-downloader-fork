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
2. Load the watch page and parse initial JSON state.
3. If the watch page is challenged or cannot be parsed, fall back to the
   InnerTube `player` and `next` endpoints for live-video bootstrap metadata.
4. Extract video details, playability information, client config, and initial
   chat continuation hints.
5. Load the chat page once to recover the active `Top chat` and `Live chat`
   continuation tokens.
6. Build browser-like request headers, optionally adding auth headers when
   cookies are available.
7. Poll the private InnerTube chat continuation endpoint.
8. Parse actions into normalized chat messages.
9. Continue until replay data ends, live continuations stop, or the caller
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
- `discovery_playlists.py`, `discovery_channels_runtime_iteration.py`, and
  `discovery_helpers.py`: URL discovery and extraction support, including
  live-page discovery

### Request construction

- `client_context.py`: request headers and client context construction
- `client_requests_initial.py`: initial request helpers
- `client_requests_continuation.py`: continuation request helpers
- `client_auth.py`: cookie initialization, SAPISID cookie parsing, and auth
  header derivation

### Continuation loop

- `chat_streams.py`: site-level chat entry methods
- `chat_streams_runtime_iteration.py`: main polling loop
- `chat_users_router.py` and `chat_users_retrieval.py`: optional chat-user
  lookup routing and retrieval helpers
- `continuations.py`: continuation parsing models and utilities
- `continuation_loop.py`, `continuation_loop_runtime.py`, and
  `continuation_loop_state.py`: loop state and live timing helpers
- `message_pipeline.py`: filtering and action-to-message pipeline boundary

### Parsing

- `parsing/actions_router.py`, `parsing/actions_handlers.py`,
  `parsing/actions_handlers_parser.py`, and
  `parsing/actions_handlers_validation.py`: action routing, parsing, and
  validation
- `parsing/message_content_badges.py`,
  `parsing/message_content_text_parser.py`,
- `parsing/message_items_content_parser.py`,
  `parsing/message_items_video.py`, `parsing/message_links.py`, and
  `parsing/message_utils.py`: message content, links, video metadata, and
  shared normalization helpers
- `parsing/messages.py`: message-level parsing helpers

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
and otherwise falls back to 5 seconds, clamped to 0.5-8 seconds. This polling
delay is separate from HTTP connect/read timeout settings.

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

The live path also derives a live offset when possible so downstream timing is
more consistent.

### Replays and completed streams

For replay content, the loop uses replay offsets and a `TimeRangeFilter` to
support `start_time` and `end_time`.

`NoChatReplay` is most likely to appear in replay mode, when YouTube simply
does not expose replay chat for the target.

### Request profiles and fallback

`request_profile` can select `youtube_web`, `youtube_android`, or
`youtube_ios`. The CLI and Python API both carry this through
`DownloaderConfig`.

When `auto_profile_fallback` is enabled, the continuation loop can rotate
YouTube profiles after repeated incomplete continuation payloads. The fallback
logic lives in `chat_streams_runtime_iteration.py`; profile headers and
InnerTube context adjustments live in `client_context.py` and
`request_profiles.py`.

The initial InnerTube fallback uses the same request-profile context fields
when constructing its `player` and `next` payloads. Continuation-loop profile
fallback remains separate and still handles incomplete chat-poll responses
after bootstrap has succeeded.

`client_auth.py` handles SAPISIDHASH-style authorization when suitable cookies
are available. Header values are sanitized before they appear in debug logs.

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

`ban_user` is the normalized `message_type` for three distinct wire actions:
`remove_chat_item`, `remove_chat_item_by_author`, and
`mark_chat_items_by_author_as_deleted`. All three appeared in fixture captures
from the Shu/Pokopia and Shapy/Crimson Desert live streams
(`tests/fixtures/youtube/live_events/`).

`deleted_message` (from `markChatItemAsDeletedAction`) is implemented but has
not yet appeared in any observed live stream capture.

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

## Common Failure Points

The YouTube stack is most sensitive to changes in:

- watch-page JSON structure
- chat-page bootstrap structure
- live discovery page structure
- InnerTube continuation response structure
- required request headers
- auth and cookie behavior

The initial watch-page fetch raises `RetriesExceeded` when all retry attempts
are exhausted on 5xx responses (previously fell through silently).
Invalid or negative continuation delay values from the InnerTube response use
the 5 second polling fallback.

When debugging YouTube breakage, inspect modules in this order:

1. `video_initialization.py`
2. `discovery_helpers.py`
3. `client_context.py`
4. `client_auth.py`
5. `client_requests_continuation.py`
6. `chat_streams_runtime_iteration.py`
7. `continuations.py`
8. `parsing/`

## Compatibility Note

YouTube functionality is exposed through the boundary modules listed in the
Module Guide. When extending or debugging, import from those current boundaries
rather than adding broad compatibility facades. Text, link, badge, action, and
message-item parsing now lives in focused modules under `parsing/`.
