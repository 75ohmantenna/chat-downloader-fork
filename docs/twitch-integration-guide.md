# Twitch Integration Guide

How the Twitch integration works in `chat-downloader-fork`. For maintainers
debugging the live IRC path or the GraphQL-backed replay path.

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

- `chat_downloader/sites/twitch/extractor.py`

Public site methods include `get_chat_by_stream_id`, `get_chat_by_vod_id`,
`get_chat_by_clip_id`, and `generate_urls`. URL matching routes live stream,
VOD, and clip URLs to those methods through `BaseChatDownloader.matches()`.

Main implementation areas:

- `chat_downloader/sites/twitch/`
- `chat_downloader/sites/twitch/parsing/`

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
- `parsing/tag_decoding.py`: IRC tag decoding and boolean parsing
- `parsing/badges.py`: badge parsing and icon shaping helpers
- `remappings.py`: remapping dictionary builders
- `validation_keys.py`: known-key sets used by debug validation
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

The integration maps several GraphQL failure classes into clearer
downloader exceptions such as:

- `VideoNotFound`
- `VideoUnavailable`
- `LoginRequired`
- `VideoUnplayable`
- `ParsingError`

The default public Client-ID is defined in `constants.py`; callers can override
it with `DownloaderConfig(twitch_client_id=...)` or `--twitch_client_id`.

## Common Failure Points

The Twitch stack is most sensitive to changes in:

- persisted GraphQL hashes
- replay comment schema
- IRC tags and message variants
- badge payload shape

When debugging Twitch breakage, inspect modules in this order:

1. `graphql_client.py`
2. `constants.py`
3. `replay_service.py` or `live_service.py`
4. `irc_transport.py`
5. `parsing/messages.py`
