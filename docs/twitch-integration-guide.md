# Twitch Integration Guide

This guide explains how the Twitch integration works in
`chat-downloader-fork`. It is intended for maintainers debugging the live IRC
path or the GraphQL-backed replay path.

The Twitch stack is split across two transport families:

- IRC for live chat.
- GraphQL-backed APIs for metadata, replay comments, clips, and badges.

## What It Covers

The Twitch implementation is responsible for:

- matching Twitch live, VOD, and clip URLs
- retrieving stream or video metadata
- connecting to live IRC chat
- reading replay comments for VODs and clips
- parsing Twitch-specific message, badge, emote, reply, and system-event data

Primary entry point:

- `src/chat_downloader/sites/twitch/extractor.py`

Public site methods include `get_chat_by_stream_id`, `get_chat_by_vod_id`,
`get_chat_by_clip_id`, and `generate_urls`. URL matching routes live stream,
VOD, and clip URLs to those methods through `BaseChatDownloader.matches()`.

Main implementation areas:

- `src/chat_downloader/sites/twitch/`
- `src/chat_downloader/sites/twitch/parsing/`

## End-to-End Flow

The Twitch flow depends on the target type.

### Live streams

1. Resolve the channel from the URL.
2. Query Twitch GraphQL for stream metadata and status.
3. Refresh channel and global badge definitions.
4. Open an IRC connection to Twitch chat.
5. Join the channel and stream parsed IRC messages.
6. Filter, deduplicate, and normalize messages before yielding them.

### VODs and clips

1. Resolve the VOD or clip ID from the URL.
2. Query Twitch GraphQL for metadata.
3. Refresh badge definitions for the broadcaster.
4. Request replay comments in pages.
5. Parse comment nodes into normalized messages.
6. Apply time-range and message-type filtering until replay data is exhausted.

## Module Guide

### Site entry and orchestration

- `extractor.py`: `TwitchChatDownloader` and public site methods
- `live_service.py`: live chat orchestration
- `replay_service.py`: VOD and clip orchestration

### Transport and API access

- `irc_transport.py`: low-level Twitch IRC socket lifecycle
- `graphql_client.py`: persisted-query GraphQL requests and error mapping
- `replay_transport.py`: replay comment retrieval
- `discovery.py` and `url_generation.py`: discovery helpers and generated URLs

### Parsing and shared Twitch data

- `parsing/messages.py`: high-level IRC and replay parsing orchestration
  (entry points `_parse_item` / `_parse_irc_item`)
- `parsing/message_emotes.py`: emote regex, image-list generation, author
  images, and text-with-emotes assembly
- `parsing/message_irc_resolve.py`: IRC action type, message type, room-state,
  shared-chat, ban/clearchat, and follower/slow-mode resolution
- `parsing/tag_decoding.py`: IRC tag decoding and boolean parsing
- `parsing/badges.py`: badge parsing and icon shaping helpers
- `remappings.py`: remapping dictionary builders
- `validation_keys.py`: known-key sets and raw replay-shape debug validation
- `types.py`: badge cache and related immutable snapshots
- `constants.py`: persisted-query names and hashes, message groups, IRC
  constants, GraphQL operation payloads, and known-key builders

There is no current `client.py` facade in the Twitch package. Import focused
modules directly when tests or integrations need patch points:
`graphql_client.py` for GraphQL, `irc_transport.py` for live IRC,
`replay_transport.py` for replay comments, and `parsing/` for message shaping.

## Live Capture Details

### Metadata first

The live path starts with a GraphQL metadata lookup. That determines whether the
channel is live, rerunning older content, offline, or upcoming.

The integration keeps the live-chat entry path available even when the channel
is not live yet, which allows callers to wait for chat activity.

### Badge refresh

Before parsing live traffic, the downloader fetches channel and global badges.
The result is stored in an instance-owned `BadgeCache`.

The parser consumes a snapshot of badge data rather than mutating module-level
state. That keeps ownership clearer and testing simpler.

### IRC transport

Live chat uses Twitch IRC over TLS. The transport:

- requests Twitch IRC tags, commands, and membership capabilities
- connects anonymously with a `justinfan`-style nick
- joins the channel
- reads and parses IRC messages continuously
- responds to Twitch `PING` frames and sends periodic pings

The low-level `TwitchChatIRC` class lives in `irc_transport.py`. The live
service constructs it and passes parsed IRC frames through
`parsing/messages.py`.

Debug logging reports both the requested socket receive timeout and the
effective timeout. Twitch clamps values below one second to one second to
avoid idle CPU churn.

### Filtering and deduplication

Before yielding live messages, the service:

- deduplicates recent message IDs
- ignores expected control traffic
- validates unexpected keys during debugging
- filters messages through configured message groups and message types

### Shared Chat attribution

The IRC parser preserves raw Shared Chat tags and emits derived attribution
fields that are useful for analysis:

- `is_shared_chat_message`
- `shared_chat_effective_source_channel_id`
- `shared_chat_is_cross_channel`

## Replay Capture Details

### Replay pages

Replay chat depends on GraphQL metadata and replay comment pages rather than
IRC. The replay service:

- loads video or clip metadata
- determines duration and replay offsets
- pages through comment edges
- parses each comment node into the shared message schema

### Clip handling

Clips do not have their own standalone chat stream. The integration maps a clip
back to its source VOD and uses the clip offset plus clip duration to slice the
replay comment stream.

If the source VOD has expired, the integration raises `NoChatReplay`.

## GraphQL Dependency

Twitch metadata and replay capture depend on private persisted GraphQL queries.
That is the main fragility point in the Twitch stack.

If Twitch rotates a hash or changes required variables, failures usually appear
first in:

- `graphql_client.py`
- `constants.py`
- replay or metadata tests

The default request profile support also includes `twitch_web`. Request profile
headers are built before CLI/user header overrides, so `--user-agent` and
repeatable `--header` values can replace profile defaults.

Request-profile names are validated when `DownloaderConfig` is created;
unknown values fail with `ValueError` instead of silently falling back to
generic headers.

The integration maps several GraphQL failure classes into clearer
downloader exceptions such as:

- `VideoNotFound`
- `VideoUnavailable`
- `LoginRequired`
- `VideoUnplayable`
- `ParsingError`

A Twitch `service error` for the optional `user.primaryTeam` field retains the
usable response data and logs at debug level. The same error on an unfamiliar
GraphQL path remains a warning so new partial-response failures stay visible,
including when Twitch returns multiple errors together.

The default public Client-ID is defined in `constants.py`; callers can override
it with `DownloaderConfig(twitch_client_id=...)` or `--twitch_client_id`.

Twitch HTTP and IRC transports share the configured proxy policy. When no
explicit proxy is supplied, standard environment proxy variables may apply.
Combining cookies with an effective remote proxy is rejected before a session
is created; `proxy=""` explicitly opts out of environment proxies.

### GraphQL hash rotation

When Twitch rotates a persisted-query hash:

1. Update `OPERATION_HASHES` in `src/chat_downloader/sites/twitch/constants.py`.
2. Run `tests/test_twitch_drift_harness_unit.py`. Its coverage and orphan
   checks keep the table aligned with the operation names used by the client.

These are structural offline checks; no network access is required.

## Capture and fix parser drift

For a bounded clean-run inspection of ordinary IRC traffic, enable
`CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1`,
`CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1`, and debug logging. The first
three successfully parsed raw IRC frames are sanitized and captured across the
entire live run, including reconnects. This explicit second opt-in is required
because valid frames contain ordinary public chat data.

When a live IRC message or GraphQL response contains an unknown type, tag, or
shape, the runtime emits a drift diagnostic and saves a sanitized snapshot when
debug-sample capture is enabled. Unknown IRC actions, message types, tags, and
unmatched IRC shapes retain the original IRC line, including its `\r\n` line
terminator, so the snapshot can be promoted directly into a drift fixture.
Unexpected replay shapes retain the complete original GraphQL edge, including
fields that normalized remapping would otherwise discard. Each Twitch drift
label captures at most ten unique payloads per process and output directory, so
a newly ubiquitous provider field cannot create one file per message.
See [Debug sample capture](development-workflow-guide.md#debug-sample-capture)
for capture configuration. Promote reviewed samples into
`tests/fixtures/twitch/`.

For a bounded full-spectrum inspection of a live channel's message-retrieval
window, use the `all` message group and write both supported output formats:

```bash
capture_dir="$(mktemp -d)"
CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR="${capture_dir}/samples" \
CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1 \
CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1 \
uv run chat_downloader "https://www.twitch.tv/auronplay" \
  --message_groups all \
  --logging debug \
  --timeout 240 \
  --output "${capture_dir}/chat.jsonl" \
  --output "${capture_dir}/chat.txt" \
  --quiet \
  2> "${capture_dir}/debug.log"
```

`--quiet` suppresses the duplicate formatted stream on standard output; it does
not disable debug logging or either output writer. After the run, inspect
`debug.log` for warnings, errors, reconnects, and drift diagnostics; inspect the
sanitized samples under `samples/`; and compare `chat.jsonl` with `chat.txt`.
`--timeout 240` bounds chat-message retrieval after setup; metadata and badge
setup plus final output shutdown can make the command's total wall time longer
than four minutes.

JSONL is the lossless record for checking message types and provider metadata,
while TXT verifies user-visible formatting. The raw-frame option captures only
the first three successfully parsed IRC frames, not the entire conversation.
Review captured public chat data before sharing it.

Use `--message_groups all` for this output inspection because Twitch's default
`messages` group emits ordinary chat messages but omits known system and
moderation events such as subscriptions, room-state changes, deleted messages,
and bans. Unknown-action, unknown-type, unknown-tag, unmatched-shape, and
unexpected-key diagnostics run during parsing or on the parsed record before
message-group filtering. They therefore remain active with the default filter;
`all` removes message-group filtering from normal parsed output records observed
during the window rather than enabling drift detection itself. Transport-control
records and duplicate message IDs can still be handled internally. The option
also cannot make rare events occur; use curated fixtures or repeated targeted
runs when the complete parser surface needs coverage.

To turn a captured drift sample into a permanent regression anchor:

1. Reproduce the failure with
   `CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1` and `--logging debug`.
2. Read and review the sanitized snapshot. The harness rejects any
   unexpected-data `debug_log()` call, unmatched IRC fixture, unexpected
   normalized IRC key, or raw GraphQL schema drift.
3. Update the relevant parser contract:
   - New IRC action type → extend `ACTION_TYPE_REMAPPING` in `constants.py`.
   - New IRC message type → extend `MESSAGE_GROUP_REMAPPINGS` in
     `constants.py` and assign it to the appropriate message group.
   - New IRC tag → add it to `build_irc_remapping()` or
     `build_message_param_remapping()` in `remappings.py`; the known-key set
     derives from those mappings.
   - New replay field or typename → update the VOD edge schema in
     `validation_keys.py`, then update the parser/remapping when the field is
     useful.
   - GraphQL hash rotation → update `OPERATION_HASHES` in `constants.py`;
     see GraphQL Hash Rotation below.
4. Promote reviewed IRC samples into `tests/fixtures/twitch/live_events/` and
   replay samples into `tests/fixtures/twitch/graphql/`. The drift harness runs
   both fixture families through their real parser composition.
5. Run the drift harness, then the canonical validation:
   ```bash
   uv run pytest -q tests/test_twitch_drift_harness_unit.py
   make ci
   ```
   It replays every fixture and asserts no drift report fires. A passing harness
   makes the fix a permanent regression anchor.

## Common Failure Points

The Twitch stack is most sensitive to changes in:

- persisted GraphQL hashes
- replay comment schema
- IRC tags and message variants
- badge payload shape

When debugging Twitch breakage, inspect modules in this order:

1. `graphql_client.py` — GraphQL request structure and error mapping
2. `constants.py` — persisted-query hashes (`OPERATION_HASHES`) and known-key sets
3. `replay_service.py` or `live_service.py` — service-layer orchestration
4. `irc_transport.py` — low-level IRC socket and capability negotiation
5. `parsing/message_irc_resolve.py` — IRC action/message-type resolution
6. `parsing/message_emotes.py` — emote parsing and image-list assembly
7. `parsing/messages.py` — high-level orchestration and field assembly

## Testing

Parser, GraphQL, replay, and IRC transport tests are offline by default.
Reviewed drift fixtures live under `tests/fixtures/twitch/`; live-network tests
must carry `@pytest.mark.network` and require `--run-network`.
