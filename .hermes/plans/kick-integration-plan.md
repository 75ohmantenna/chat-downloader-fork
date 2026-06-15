# Kick Integration Plan — Comprehensive Chat Event Support

## Objective
Build a reliable Kick.com chat integration for `chat-downloader-fork` that captures all chatroom events (messages, subscriptions, gifts, bans, pins, hosts, etc.) using Cloudscraper for the REST API (Cloudflare-bypass) and the Pusher WebSocket for real-time events.

## Architecture Overview

```
User URL (kick.com/{username})
  → site_dispatch.py matches KickChatDownloader
    → extractor.py routes to live_service.get_chat_by_channel()
      → api_client.py (cloudscraper-based): GET /api/v2/channels/{username}
        → resolves chatroom_id + channel_id + stream status
      → live_service:
        1. Fetch preloaded messages (best-effort)
        2. Open Pusher WebSocket to chatrooms.{id}.v2
        3. Stream + deduplicate + filter all events
```

## Key Design Decisions

### 1. Event Types to Support
All Pusher events in the `chatrooms.{id}.v2` channel:

| Pusher Event Name | Normalized message_type | Priority |
|---|---|---|
| `App\Events\ChatMessageEvent` | `text_message` | DONE (from claude branch) |
| `App\Events\MessageDeletedEvent` | `message_deleted` | HIGH |
| `App\Events\PinnedMessageCreatedEvent` | `pinned_message` | HIGH |
| `App\Events\PinnedMessageDeletedEvent` | `pinned_message_deleted` | HIGH |
| `App\Events\UserBannedEvent` | `user_banned` | HIGH |
| `App\Events\UserUnbannedEvent` | `user_unbanned` | HIGH |
| `App\Events\SubscriptionEvent` | `subscription` | HIGH |
| `App\Events\GiftedSubscriptionsEvent` | `gifted_subscriptions` | HIGH |
| `App\Events\StreamHostEvent` | `stream_host` | MEDIUM |
| `App\Events\ChatClearMessagesEvent` | `chat_clear` | MEDIUM |
| `RewardRedeemedEvent` | `reward_redeemed` | LOW |

### 2. Cloudscraper for REST API
- Replace direct `requests` session calls with `cloudscraper` for the Kick API v2 endpoints
- Cloudscraper handles Cloudflare JS challenge automatically
- Pusher WebSocket still uses `websocket-client` (direct connection to Pusher CDN, bypasses Cloudflare)

### 3. Dependency Additions
- `websocket-client>=1.7.0,<2.0.0` — sync websocket for Pusher
- `cloudscraper>=1.2.0,<2.0.0` — Cloudflare-bypass HTTP client

### 4. Message Schema for Non-Text Events
- All events get `message_id`, `message_type`, `timestamp`
- Sub/gift events: include `author` (gifter), `recipients` list, `plan` info
- Ban events: include `target_user`, `moderator`, `expires_at`
- Pin events: include full original message
- Host events: include `host_username`, `viewer_count`

### 5. Message Groups
Extend `MESSAGE_GROUPS`:
- `"messages"` → text messages
- `"subscriptions"` → sub + gift events
- `"moderation"` → ban/unban/message_deleted/chat_clear
- `"pins"` → pinned_message created/deleted
- `"hosts"` → stream host events

## Implementation Plan

### Phase 1: Foundation (from dev/kickchat-claude)
- Branch: `dev/kickchat-deepseek` (DONE — created from master)
- Add `cloudscraper`, `websocket-client` deps to `pyproject.toml`
- Import existing Kick source from dev/kickchat-claude into our branch
- Update `sites/__init__.py` to register KickChatDownloader
- Update import-linter contract for kick independence

### Phase 2: Enhanced Event Parsing
- Add all Pusher event name constants
- Expand `parsing/events.py` dispatch registry
- Create `parsing/subscriptions.py` — sub/gift event parsers
- Create `parsing/moderation.py` — ban/unban/delete event parsers
- Create `parsing/pins.py` — pin event parsers
- Create `parsing/hosts.py` — host event parsers
- Update `constants.py` with MESSAGE_TYPE_REMAPPING and MESSAGE_GROUPS
- Update `parsing/messages.py` to support metadata field

### Phase 3: Cloudscraper Integration
- Update `api_client.py` to use `cloudscraper.create_scraper()` instead of base session
- Keep fallback: if cloudscraper unavailable, use requests session with warning
- Add Cloudflare challenge detection improvements

### Phase 4: Deduplication and Filtering
- Ensure all event types go through `_SeenMessageCache`
- Ensure all event types go through `MessageFilter`
- Extend preloaded message handling to include other event types if present

### Phase 5: Testing
- Update existing test fixtures with new event types
- Write parsers tests for each event type
- Write event dispatch tests
- Write integration test for live_service with injected transports
- Run `make ci` and verify 100% coverage

## Files to Create/Modify

### New files (in src/chat_downloader/sites/kick/parsing/):
- `subscriptions.py` — subscription/gift event parsers
- `moderation.py` — ban/unban/delete/clear event parsers  
- `pins.py` — pinned message event parsers
- `hosts.py` — stream host event parser

### New test files:
- `tests/test_kick_parsing_subscriptions_unit.py`
- `tests/test_kick_parsing_moderation_unit.py`
- `tests/test_kick_parsing_pins_unit.py`
- `tests/test_kick_parsing_hosts_unit.py`

### New fixtures (in tests/fixtures/kick/):
- `subscription_event.json`
- `gifted_subscriptions_event.json`
- `user_banned_event.json`
- `user_unbanned_event.json`
- `message_deleted_event.json`
- `pinned_message_created_event.json`
- `pinned_message_deleted_event.json`
- `stream_host_event.json`
- `chat_clear_event.json`

### Modified files:
- `pyproject.toml` — add dependencies, update import-linter
- `src/chat_downloader/metadata.py` — bump version to 1.3.0
- `src/chat_downloader/sites/__init__.py` — register Kick
- `src/chat_downloader/sites/kick/__init__.py` — update exports
- `src/chat_downloader/sites/kick/constants.py` — add all event names, message types
- `src/chat_downloader/sites/kick/api_client.py` — cloudscraper integration
- `src/chat_downloader/sites/kick/parsing/events.py` — full dispatch
- `src/chat_downloader/sites/kick/parsing/messages.py` — metadata field support
- `docs/` — update architecture, capability-inventory
- `CHANGELOG.md` — add 1.3.0 entry