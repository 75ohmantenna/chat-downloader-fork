# Python API Reference

The stable Python surface exposed by `chat-downloader-fork`. The authoritative
sources are `src/chat_downloader/__init__.py`, `src/chat_downloader/chat_downloader.py`,
and `src/chat_downloader/models/`; this document reflects them.

## Quick Start

Most integrations need only three objects:

- `ChatDownloader`
- `DownloaderConfig`
- `ChatRequest`

`RunConfig` and `SiteDefault` are also part of the typed model layer for
CLI-style execution and site-default markers. Import them from
`chat_downloader.models`; they are not re-exported from the top-level package.

Minimal example:

```python
from chat_downloader import ChatDownloader

downloader = ChatDownloader()
try:
    chat = downloader.get_chat("https://www.youtube.com/watch?v=QBFiiEVBWvE")
    for message in chat:
        print(message.get("author"), message.get("message"))
finally:
    downloader.close()
```

## Version

The package version is exposed as `chat_downloader.__version__`:

```python
from chat_downloader import __version__

print(__version__)
```

## Primary Entry Points

### `ChatDownloader`

`ChatDownloader` owns shared configuration, cookie state, and per-site session
instances.

Important methods:

- `get_chat(...)`: resolve the site from the URL and return a `Chat` object
- `get_chat_request(request)`: typed entry point that accepts a `ChatRequest`
- `create_session(...)`: create a session for a site class explicitly
- `get_session(...)`: return an existing site session if present
- `clear_cookies()`: clear local and session cookies
- `set_cookie_value(...)` and `get_cookie_value(...)`: manage cookies before or
  after site sessions exist; `set_cookie_value` raises `InvalidParameter` for
  empty, whitespace-padded, or unscoped (no dot) cookie domains
- `close()`: close all owned sessions

Use `ChatDownloader` when you want direct control over lifecycle, cookies, or
session reuse.

`ChatDownloader.get_chat(...)` still accepts keyword arguments for compatibility
with upstream-style usage, but those arguments map to `ChatRequest` fields. New
request options should be added to `ChatRequest` first.

### `run`

`run(propagate_interrupt=False, **kwargs)` is the CLI-oriented convenience
wrapper. It creates a `ChatDownloader`, separates init, request, and runtime
parameters through `runtime/cli_bridge.py`, iterates the chat, applies logging
behavior, finalizes attached writers, and closes resources.

Use `run()` when embedding CLI-like behavior.

`run()` also accepts runtime controls such as `quiet`, `exit_on_debug`,
`pause_on_debug`, and `max_seen_message_ids`. These fields are defined by
`RunConfig` in `chat_downloader.models`; `quiet`, `exit_on_debug`, and
`pause_on_debug` are exposed on the CLI.

Pass `propagate_interrupt=True` to re-raise `KeyboardInterrupt` instead of
swallowing it — useful when `run()` is called from within a larger
application that needs to handle interrupts itself.

Unlike `ChatRequest.from_kwargs()`, `run()` rejects unknown keyword arguments.
That keeps CLI/API drift visible when parameters move between dataclasses.

`run()` returns `chat_downloader.runtime.RunResult`, a structured summary with
these fields:

| Field | Meaning |
| --- | --- |
| `success` | Whether the run completed without a terminal error |
| `message_count` | Number of messages processed |
| `interrupted` | Whether execution ended through `KeyboardInterrupt` or `SIGTERM` |
| `error_message` | Terminal error text, or `None` on success |

`RunResult` is available from `chat_downloader.runtime`; it is not a top-level
`chat_downloader` export.

## Typed Configuration Objects

### `DownloaderConfig`

`DownloaderConfig` holds session-level settings passed into
`ChatDownloader(...)`.

| Field | Default | Description |
| --- | --- | --- |
| `headers` | `None` | Custom HTTP headers |
| `cookies` | `None` | Path to a Netscape-format cookies file |
| `proxy` | `None` | HTTP, HTTPS, or SOCKS proxy URL; `InvalidParameter` is raised for unknown schemes or a missing host. `None` permits standard environment proxies; `""` disables them. With `cookies`, any effective remote proxy raises `InvalidParameter`, while loopback proxies (`127.0.0.0/8`, `::1`, `localhost`) emit a warning. |
| `connect_timeout` | `10.0` | TCP connect timeout in seconds; must be finite and positive (`ValueError` otherwise) |
| `read_timeout` | `30.0` | HTTP read timeout in seconds; must be finite and positive (`ValueError` otherwise) |
| `request_profile` | `None` | Optional request-header preset (`youtube_web`, `youtube_android`, `youtube_ios`, `twitch_web`); any other value raises `ValueError` during configuration |
| `auto_profile_fallback` | `True` | Auto-rotate YouTube request profiles after repeated incomplete continuation responses |
| `twitch_client_id` | `None` | Optional Twitch Client-ID override for GraphQL and VOD comment requests |

Helper:

- `as_dict()`: convert the dataclass to the keyword shape accepted by
  `ChatDownloader(...)`

`headers` is part of the Python API but is handled specially by the CLI through
`--user-agent` and repeatable `--header "Name: Value"` flags. The CLI normalizes
header names to title-case (`content-type` → `Content-Type`).

Example:

```python
from chat_downloader import ChatDownloader, DownloaderConfig

config = DownloaderConfig(
    proxy="socks5://127.0.0.1:1080",
    read_timeout=45.0,
    request_profile="youtube_web",
)

downloader = ChatDownloader(**config.as_dict())
```

### `ChatRequest`

`ChatRequest` holds request-level settings passed into
`ChatDownloader.get_chat(...)` or `ChatDownloader.get_chat_request(...)`.

Fields appear in dataclass definition order. Contract tests compare every name
and default below with the dataclass definitions used by the facade and CLI:

| Field | Default | Description |
| --- | --- | --- |
| `url` | `""` | Stream, video, or clip URL |
| `start_time` | `None` | Replay start offset; supported by YouTube, Twitch replay, and Kick VOD URLs |
| `end_time` | `None` | Replay end offset; supported by YouTube, Twitch replay, and Kick VOD URLs |
| `max_attempts` | `15` | Maximum retry attempts |
| `retry_timeout` | `None` | Delay before retry; `None` uses exponential backoff, while a negative value waits for user input |
| `interruptible_retry` | `True` | Allow a waiting retry to be triggered immediately |
| `timeout` | `None` | Overall runtime limit |
| `inactivity_timeout` | `None` | Stop after idle period |
| `max_messages` | `None` | Stop after this many messages |
| `message_groups` | site default | High-level message filtering |
| `message_types` | `None` | Explicit message-type filtering; overrides `message_groups` when supplied |
| `output` | `None` | Output path or list of paths |
| `overwrite` | `True` | Replace existing output files |
| `sort_keys` | `True` | Sort keys in JSON output |
| `format` | site default | Text-format template override |
| `format_file` | `None` | Custom formatter definition |
| `chat_type` | `"live"` | YouTube chat mode: `"live"` or `"top"` |
| `ignore` | `None` | YouTube video IDs to skip during discovery |
| `message_receive_timeout` | `0.1` | Live socket receive-poll timeout; Twitch and Kick enforce a one-second minimum |
| `buffer_size` | `4096` | Twitch IRC receive-buffer size in bytes |

URL dispatch matches the complete input. Normal query strings and fragments are
accepted, while URLs embedded in unrelated text, reserved site routes, and
trailing non-URL text are rejected. Host/path inputs without a scheme and
protocol-relative inputs beginning with `//` are normalized to HTTPS.

For YouTube live chats, the site default text format renders absolute
timestamps before elapsed replay offsets and omits the author separator for
authorless system events. Live `time_in_seconds` and `time_text` values are
relative to capture startup: messages returned in the initial backlog use
negative values, while later messages use positive values. Replay chats keep
the standard elapsed-time rendering.

Kick VOD offsets are relative to the recording start and are clamped to its
duration. Kick live channel URLs reject `start_time` and `end_time` because the
public live feed cannot seek.

Validation raises `ValueError` for non-positive or non-integer message, retry,
or buffer counts; malformed or non-finite start/end times; a non-finite
`retry_timeout`; a `chat_type` other than `"live"` or `"top"`; a non-positive
or non-finite `timeout` or `inactivity_timeout` when set; and a non-positive or
non-finite `message_receive_timeout`.

Helpers:

- `from_kwargs(**kwargs)`: build a request from keyword arguments; pass
  `strict=True` to reject unknown keyword arguments instead of silently
  ignoring them
- `with_updates(**kwargs)`: clone with selected values replaced
- `resolved_for_site(site_object)`: resolve site defaults against a concrete
  site instance
- `as_dict()`: convert to the keyword shape accepted by
  `ChatDownloader.get_chat(...)`
- `retry_kwargs()`: return the retry-related subset used by site code

Example:

```python
from chat_downloader import ChatDownloader, ChatRequest

request = ChatRequest(
    url="https://www.twitch.tv/videos/123456789",
    max_messages=200,
    output=["chat.jsonl", "chat.txt"],
)

downloader = ChatDownloader()
chat = downloader.get_chat_request(request)
```

### `RunConfig`

`RunConfig` holds runtime-only controls for `run(...)`:

| Field | Default | Description |
| --- | --- | --- |
| `quiet` | `False` | Suppress formatted chat output to stdout |
| `max_seen_message_ids` | `10000` | Deduplication cache size for `run()` |
| `exit_on_debug` | `False` | Exit when unexpected debug conditions are hit |
| `pause_on_debug` | `False` | Pause when selected debug conditions are hit |

Helpers:

- `from_kwargs(**kwargs)`: build a config from keyword arguments, ignoring
  unrelated keys
- `as_dict()`: convert to a plain dictionary

The CLI exposes `quiet`, `exit_on_debug`, and `pause_on_debug`. The `--testing`
flag is a CLI convenience that enables debug logging and `pause_on_debug`.

### `SiteDefault`

`SiteDefault(name)` is the marker used by `ChatRequest.message_groups` and
`ChatRequest.format` to request a concrete site default during URL dispatch.
The canonical import path is `chat_downloader.models.SiteDefault`.
`chat_downloader.sites.models.SiteDefault` remains a compatibility alias to
the same class.

## `Chat` Objects

`get_chat()` returns a `Chat` object from `chat_downloader.sites.models`. It
wraps the underlying generator and carries metadata such as status and output
writer state.

In normal usage, treat it as an iterable of message dictionaries:

```python
for item in chat:
    print(item["message_type"], item.get("message"))
```

## Output and Formatting Helpers

- `ContinuousWriter` and `ContinuousFileWriter`: long-running output writers
- `ItemFormatter`: text-formatting engine used for stdout and `.txt` output
- `TimedGenerator`: generator wrapper that enforces timeout and inactivity
  limits

The runtime can attach multiple output writers when `output` is a list or when
the CLI receives repeated `--output` flags. Use `.jsonl` for structured chat
output. JSON-array `.json` output is not supported.

Output paths support `{title}` and `{id}` placeholders. The substituted values
are sanitized as single filename components. Targets are deduplicated after
placeholder expansion and canonical path resolution; existing hard links are
also treated as one destination.

File writers are crash-resilient: each record is flushed on write and the file
is synchronized to disk periodically (about every 60 seconds). Datetime values
serialized to JSONL output must be timezone-aware (UTC); a naive `datetime`
raises `ValueError` at the output boundary to keep timestamps unambiguous.

## Debugging Helpers

`chat_downloader.debugging` provides:

- `log(...)` and `debug_log(...)` for package logging
- `sanitize_for_log(...)` for redacting cookies, proxies, known secret fields,
  custom header names containing auth/token/secret/credential markers, API-key
  headers, and Basic/Bearer/OAuth/SAPISIDHASH values before logging or capture
- `capture_debug_sample(label, payload, *, sample_limit=None)` for opt-in
  sanitized sample capture

Project log handlers sanitize structured values, exception text, stack
information, URL credentials, sensitive query parameters, and terminal control
characters before rendering records.

`capture_debug_sample()` writes only when debug logging is enabled and
`CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES` is truthy. The target directory defaults
to a temporary directory and can be overridden with
`CHAT_DOWNLOADER_DEBUG_SAMPLE_DIR`. On supported POSIX systems, custom capture
directories must be owned by the current user with mode `0700`; sample files
use mode `0600`. Capture rejects symbolic links, unexpected file types, foreign
ownership, and broader permissions. Platforms without secure
directory-relative no-follow creation refuse capture rather than use a
path-based fallback. Set `sample_limit` to bound the number of unique payloads
written for one label and output directory during the current process;
duplicate payloads continue to resolve to their deterministic existing path.

`chat_downloader.debug_sample_utils` contains naming helpers used to turn
captured samples into stable fixture names.

## Public Errors

All package-specific errors derive from `ChatDownloaderError`.

Common exceptions:

- `InvalidURL`
- `URLNotProvided`
- `VideoNotFound`
- `VideoUnavailable`
- `LoginRequired`
- `CaptchaChallengeRequired`
- `NoChatReplay`
- `SiteNotSupported`
- `RetriesExceeded`
- `IncompleteContinuationError`
- `FormatNotFound`
- `FormatFileNotFound`

If you are writing an integration, catch `ChatDownloaderError` at the top level
and handle narrower subclasses only when you need custom recovery behavior.

`TwitchError` remains exported for compatibility with earlier releases. Current
Twitch paths classify failures with narrower shared exceptions such as
`VideoUnavailable`, `UserNotFound`, `NoChatReplay`, and `ParsingError`; callers
should not rely on new Twitch failures being wrapped in `TwitchError`.

## Top-Level Exports

Import these names directly from `chat_downloader`:

```python
from chat_downloader import (
    BaseChatDownloader,
    CaptchaChallengeRequired,
    Chat,
    ChatDisabled,
    ChatDownloader,
    ChatDownloaderError,
    ChatGeneratorError,
    ChatRequest,
    CookieError,
    ContinuousFileWriter,
    ContinuousWriter,
    DownloaderConfig,
    FormatError,
    FormatFileNotFound,
    FormatNotFound,
    Image,
    IncompleteContinuationError,
    ItemFormatter,
    InvalidParameter,
    InvalidURL,
    KickChatDownloader,
    KickError,
    LoginRequired,
    NoChatReplay,
    NoContinuation,
    NoVideos,
    ParsingError,
    Remapper,
    RetriesExceeded,
    SiteError,
    SiteNotSupported,
    TimedGenerator,
    TwitchChatDownloader,
    TwitchError,
    URLNotProvided,
    UserNotFound,
    VideoNotFound,
    VideoUnavailable,
    VideoUnplayable,
    YouTubeChatDownloader,
    __version__,
    get_all_sites,
    run,
)
```

Additional typed model helpers are available from `chat_downloader.models`,
including `RunConfig`, `coerce_chat_request` (converts a keyword-argument
dictionary to a `ChatRequest` with `strict=True`, rejecting unknown keys), and
the default constants used by the facade.

## Site Classes

In normal usage you do not touch site classes directly — `get_chat()` resolves
the site from the URL. When you need a concrete class (custom dispatch, type
checks), `TwitchChatDownloader`, `YouTubeChatDownloader`, `KickChatDownloader`,
and `KickError` are all re-exported at the top level:

```python
from chat_downloader import KickChatDownloader, KickError
```

`get_all_sites()` returns every registered site class, including
`KickChatDownloader`.

Kick URLs (`kick.com/{username}` for live chat and
`kick.com/{username}/videos/{uuid}` for VOD replay) work through the standard
`get_chat()` and `get_chat_request()` entry points like any other site.
