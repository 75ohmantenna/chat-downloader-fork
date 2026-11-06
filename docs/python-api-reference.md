# Python API Reference

The stable Python surface exposed by `chat-downloader-fork`. The authoritative
sources are `src/chat_downloader/__init__.py`,
`src/chat_downloader/chat_downloader.py`, and `src/chat_downloader/models/`;
this document reflects them.

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
    with downloader.get_chat(
        "https://www.youtube.com/watch?v=QBFiiEVBWvE"
    ) as chat:
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

Each returned `Chat` owns its message source, deadline worker, and attached
outputs separately from the downloader's shared provider sessions. Use
`with downloader.get_chat(...) as chat:` (or `with
downloader.get_chat_request(request) as chat:`) so an early `break` or exception
in consumer code closes the chat. Without a context manager, call `chat.close()`
in `finally`, before `downloader.close()`. Closing a chat leaves its provider
session available for another request; closing the downloader does not close
returned chats. A deadline does not replace explicit cleanup after abandoning
iteration.

Public `get_cookie_value()` remains name-only introspection. Authentication
instead selects cookies using provider domain, path, expiry, secure-transport,
and port scope. Conflicting applicable credential values are not used.
YouTube SID promotion preserves the selected cookie's scope and does not extend
its existing expiration. Provider API requests reject HTTP redirects; YouTube
initial pages permit at most five same-origin HTTPS redirects.

Shared YouTube/Twitch requests consume at most 16 MiB of decoded HTTP body before
`.text` or `.json()` can parse it. Gzip and deflate are supported; unsupported
or stacked content encodings and decoded-body overflow raise `ParsingError`.
A separate 32 MiB HTTP-wire quota, including headers and framing, also bounds
compressed decoder input that produces no output.
Responses are released on every exit, while their owning sessions remain
reusable. A monotonic completion deadline spans connect, response headers,
decoded body reads, and any permitted redirect chain; its budget is the
configured connect plus read timeouts. The configured read timeout remains an
inactivity limit, not a 0.1-second polling timeout. Timed-worker cancellation is
checked during socket reads, SOCKS negotiation, nested HTTPS-proxy TLS reads,
urllib3 retry sleeps, and blocking connection-pool acquisition without spawning
body-reader threads. Direct exposed-session requests share one budget across
their complete redirect chain. Unused intermediate retry bodies close without
being drained into memory. Host-specific adapter mounts retain supported
Requests TLS, retry, and pool settings under the same bounds; custom senders,
response builders, connection readers, retry implementations, and pool policies
that cannot enforce the policy fail explicitly before transport. Short-lived
custom adapter and pool classes are not strongly cached globally. User-supplied
callbacks and hooks are not preempted; they must terminate cooperatively.
Platform DNS resolution is synchronous and cannot be preempted by this policy;
an expired budget is enforced when resolution returns.

`ChatDownloader.get_chat(...)` still accepts keyword arguments for compatibility
with upstream-style usage, but those arguments map to `ChatRequest` fields. New
request options should be added to `ChatRequest` first.

### `run`

`run(propagate_interrupt=False, **kwargs)` is the CLI-oriented convenience
wrapper. It creates a `ChatDownloader`, separates init, request, and runtime
parameters through `runtime/cli_bridge.py`, iterates the chat, applies logging
behavior, finalizes attached writers, and closes resources.

Use `run()` when embedding CLI-like behavior.

`run()` also accepts runtime controls such as `quiet`, `compact_console`,
`exit_on_debug`,
`pause_on_debug`, `resume`, `verify_output`, `require_complete`,
`run_manifest`, and `max_seen_message_ids`. These fields are defined by
`RunConfig` in `chat_downloader.models`. Every field except
`max_seen_message_ids` is exposed on the CLI. `compact_console=True` affects
stdout only, currently suppressing identical Kick prediction updates; capture
files and retrieval counts retain every accepted event. It is a runtime control
for `run()`, rather than a `get_chat()` request parameter.

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
| `message_type_counts` | Per-type counts for processed messages; partial counts remain available after an error, and messages without a string type use the `<missing>` key |
| `parity_status` | `not_requested`, `not_run`, `passed`, or `failed`; parity verifies artifacts, not provider completeness |
| `termination_reason` | Completion, message limit, timeout, interruption, or error; successful retrieval may be intentionally bounded |
| `provider_inspection` | Content-free YouTube, Twitch live, or Kick live inspection report when output verification runs, including closed partial captures after retrieval failures; otherwise `None` |
| `elapsed_seconds` | Monotonic duration from initialization through capture shutdown, excluding output verification and manifest writing |
| `prefetched_after_deadline_count` | One observation after bounded capture shutdown; an incomplete count is a lower bound |
| `deadline_prefetch_count_complete` | Whether deadline accounting had finished when observed; inspection, manifest, and debug summary use the same observation |
| `provider_diagnostics` | Detached snapshot of finalized chat diagnostics, including nested counters, and the surviving request-scoped setup ledger; inspection, manifests, and debug summaries use this observation even when empty |
| `failure_stage` | Failed lifecycle phase, or `None` on success; see the CLI manifest reference for phase names |

`RunResult` is available from `chat_downloader.runtime`; it is not a top-level
`chat_downloader` export.

Bounded shutdown can return while a source worker is still unwinding. Built-in
dictionary, list, and tuple containers are detached; opaque adapter values
retain their identity without invoking adapter copy hooks. Later updates to
provider counters do not revise the returned observation. An incomplete deadline
count remains the observed lower bound; it does not imply that cleanup has
finished.

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
| `auto_profile_fallback` | `True` | Auto-rotate YouTube request profiles after unknown broadcast status, generic initial playability, repeated incomplete continuation responses, or an initial replay `INVALID_ARGUMENT` rejection |
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
| `start_time` | `None` | Inclusive replay start offset; must not exceed `end_time`; supported by YouTube, Twitch replay, and Kick VOD/clip URLs |
| `end_time` | `None` | Replay end offset; supported by YouTube, Twitch replay, and Kick VOD/clip URLs |
| `max_attempts` | `15` | Maximum attempts per retry loop, including the initial attempt; reconnect recovery uses a consecutive-failure budget |
| `retry_timeout` | `None` | Delay before retry; `None` uses exponential backoff, while a negative value requires an interactive terminal |
| `interruptible_retry` | `True` | Allow an interactive user to trigger a waiting retry immediately; redirected stdin uses the full delay |
| `timeout` | `None` | Message-retrieval duration limit after chat configuration; provider setup and finalization can extend total wall time |
| `inactivity_timeout` | `None` | Stop when the configured message iterator yields no message within this period; filtered provider traffic does not reset it |
| `max_messages` | `None` | Stop after this many messages |
| `message_groups` | site default | High-level message filtering; `["all"]` accepts every supported provider event type |
| `message_types` | `None` | Explicit provider message types; unknown names fail unless the list contains `all`, which accepts every type and overrides other entries; a nonempty list overrides `message_groups`, while `None` or an empty list uses groups |
| `output` | `None` | Output path or list of paths |
| `overwrite` | `True` | Replace existing explicit-path output files; metadata-generated destinations must be new |
| `sort_keys` | `True` | Sort keys in JSON output |
| `format` | site default | Text-format template override |
| `format_file` | `None` | Custom formatter definition |
| `chat_type` | `"live"` | YouTube chat mode: `"live"` or `"top"` |
| `ignore` | `None` | YouTube video IDs to skip during discovery |
| `youtube_replay_poll_interval` | `None` | Optional completed-replay polling override in seconds from `0.5` through `8`; `None` respects YouTube's delay hint |
| `message_receive_timeout` | `1.0` | Live receive-poll timeout; Twitch and legacy standalone Kick enforce a one-second minimum, while Kick's negotiated public transport caps polls at one second |
| `buffer_size` | `4096` | Twitch IRC receive-buffer size in bytes |

URL dispatch matches the complete input. Normal query strings and fragments are
accepted, while URLs embedded in unrelated text, reserved site routes, and
trailing non-URL text are rejected. Host/path inputs without a scheme and
protocol-relative inputs beginning with `//` are normalized to HTTPS.

For YouTube live chats, the site default text format renders absolute
UTC clock timestamps as `HH:MM:SS` (for example, `01:21:15`) before elapsed
replay offsets and omits the author separator for
authorless system events. Moderation events without message text render a
bracketed notice containing the removed-message or affected-author identifier
when available. Live `time_in_seconds` and `time_text` values are relative to
capture startup: messages returned in the initial backlog use negative values,
while later messages use positive values. Replay chats keep the standard
elapsed-time rendering. YouTube live records without source timing include a
separate UTC-microsecond `received_timestamp` for the continuation batch; default
live text output labels it `[received]`. This is receipt time, including for
backlog, and does not establish an event timestamp or replay offset. Capture
inspection reports source omissions and receive fallbacks separately.

Kick VOD offsets are relative to the recording start and are clamped to its
duration. Kick clip offsets are relative to the clip; the web path maps them
onto its source VOD, while the mobile fallback maps them onto a validated
absolute timestamp window. Kick live channel URLs reject `start_time` and
`end_time` because the public live feed cannot seek. A Kick live WebSocket event
that lacks a valid provider `timestamp` receives a distinct
UTC-microsecond `received_timestamp`; provider timestamps retain priority, TXT
labels the fallback `[received]`, including `kicks_gifted` notices.
Replay/preloaded records are unchanged.

Twitch channel URLs also reject `start_time` and `end_time`, including offline
channels and reruns, with `InvalidParameter` before metadata or IRC access.
Use a Twitch VOD or clip URL for replay offsets, or `timeout` to limit live
capture duration.

Validation raises `ValueError` for non-positive or non-integer message, retry,
or buffer counts; malformed or non-finite start/end times; a non-finite
`retry_timeout`; a `chat_type` other than `"live"` or `"top"`; a non-positive
or non-finite `timeout` or `inactivity_timeout` when set; and a non-positive or
non-finite `message_receive_timeout`. A non-finite
`youtube_replay_poll_interval` or a value outside `0.5` through `8` also raises
`ValueError`.
Time offsets accept an optional leading `+` or `-` applying to the whole value.
Surrounding whitespace is ignored; signs inside colon-separated components are
rejected.

Helpers:

- `from_kwargs(**kwargs)`: build a request from keyword arguments, rejecting
  unknown names with `TypeError` by default; use `strict=False` only when
  deliberately extracting request fields from a mixed mapping
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
try:
    with downloader.get_chat_request(request) as chat:
        for message in chat:
            print(message)
finally:
    downloader.close()
```

### `RunConfig`

`RunConfig` holds runtime-only controls for `run(...)`:

| Field | Default | Description |
| --- | --- | --- |
| `quiet` | `False` | Suppress formatted chat output to stdout |
| `compact_console` | `False` | Suppress unchanged provider-selected state updates on stdout; retain file records and capture counts |
| `resume` | `None` | Create or resume a validated replay shutdown checkpoint |
| `verify_output` | `False` | Verify one JSONL/TXT pair after successful, interrupted, or broken-stdout retrieval with safe cleanup; inspect supported provider captures even after other retrieval errors |
| `require_complete` | `False` | Fail unless the selected completed replay is exhausted without known record loss |
| `run_manifest` | `None` | New filename for a JSON outcome, recording, and output-hash report |
| `max_seen_message_ids` | `10000` | Deduplication cache size for `run()` |
| `exit_on_debug` | `False` | Exit when unexpected debug conditions are hit |
| `pause_on_debug` | `False` | Pause when selected debug conditions are hit |

Helpers:

- `from_kwargs(**kwargs)`: build a config from keyword arguments, ignoring
  unrelated keys
- `as_dict()`: convert to a plain dictionary

The CLI exposes every `RunConfig` field except `max_seen_message_ids`.
The `--testing` flag is a CLI convenience that enables debug logging and
`pause_on_debug`.

### `SiteDefault`

`SiteDefault(name)` is the marker used by `ChatRequest.message_groups` and
`ChatRequest.format` to request a concrete site default during URL dispatch.
The canonical import path is `chat_downloader.models.SiteDefault`.
`chat_downloader.sites.models.SiteDefault` remains a compatibility alias to
the same class.

## `Chat` Objects

`get_chat()` returns a `Chat` object from `chat_downloader.sites.models`. It
wraps the underlying generator and carries metadata such as `title`, `id`,
`status`, `video_type`, `start_time`, and `duration`. For Kick live channels,
`start_time` is the initial livestream start time in UTC Unix-epoch microseconds,
or `None` when offline, missing, or invalid; Kick replay start times remain
offsets in seconds. Provider-specific live diagnostics, when available, are
exposed through `chat.diagnostics` and included
in debug run summaries, including failed runs. Twitch live diagnostics use
fixed-schema counters for recognized optional metadata degradations, connection
setup and reconnects, IRC frames, recognized benign control frames, parsed
messages, receive timeouts and idle-watchdog expirations, `PING`/`PONG` keepalives,
duplicate suppression, filtering, and source emission. They contain no
endpoint, error path/message, or chat-content fields.
Kick live diagnostics likewise expose fixed-schema WebSocket, control, parsed,
unsupported, malformed, reconnect, Pusher-key recovery, Pusher/Centrifugo
connection, public subscription, snapshot polling, synthetic-frame, and
preload/live/backfill emission counters. They also retain bounded
malformed-event-type counts, truncated backfill counts and uncovered time, and
the last decoded or synthetic frame's receive timestamp. See the
[Kick diagnostic schema](kick-integration-guide.md#live-diagnostics).

Use the chat as a context-managed iterable of message dictionaries:

```python
with chat:
    for item in chat:
        print(item["message_type"], item.get("message"))
```

Context exit closes the message source and attached outputs on early exit,
consumer failure, or exhaustion. It does not suppress consumer exceptions.
Source shutdown keeps its existing bounded-worker behavior; an in-flight
provider request may finish later under its transport timeout.

## Output and Formatting Helpers

- `ContinuousWriter` and `ContinuousFileWriter`: long-running output writers
- `ItemFormatter`: text-formatting engine used for stdout and `.txt` output
- `TimedGenerator`: generator wrapper that enforces timeout and inactivity
  limits

`ContinuousWriter` accepts `overwrite`, `format`, `lazy_initialise`, and
`sort_keys` as keyword-only options. Unknown names raise `TypeError` before any
output file or parent directory is created, repaired, or truncated, including
with lazy initialization. `sort_keys` defaults to `True` and affects JSONL only;
the factory accepts it for TXT output without changing the text. Concrete text
writers do not accept JSON-specific options. Use `overwrite=False` to append to
an explicit output path; a misspelling such as `over_write` is an error.

`TimedGenerator` transfers source exceptions to the consuming thread, including
`SystemExit` and `GeneratorExit`. Call `close()` when stopping consumption early
to request worker shutdown and cancel its timers.
Restarting an overall timer cancels its predecessor; callbacks from superseded
timers cannot expire the new deadline. Timer starts after closure do nothing.

Custom `ItemFormatter` field definitions may provide a `template`, an optional
`singular_template`, and `omit_if_false: true`. The singular template is used
only for an exact numeric value of one; booleans and numeric strings continue to
use the normal template. A field definition with `format: "json"` renders its
value as compact JSON with sorted keys and readable Unicode characters.
JSON formatting bypasses separators and escapes lone Unicode surrogates so
the result can be written as UTF-8.
A field definition with `format: "poll"` renders a poll metadata object as
` | Option: 17 votes (25.4%) | Other: 50 votes (74.6%) | Total: 67 | Remaining: 3s`.
It derives the total from complete nonnegative integer option counts, omits
percentages for a zero total, and uses `votes unknown` with no total or
percentages for incomplete counts. Remaining seconds are optional. The built-in
Kick format applies this conversion to `metadata` for poll updates.
A field definition with `format: "count"` renders nonnegative integers and
ASCII decimal strings, preserving zero even with `omit_if_false: true`;
booleans and invalid counts render empty. `format: "viewer_count"` renders a
single valid viewer count, an explicitly offline public REST snapshot, or the
generic `Viewer count` label when no count is known.
A field definition with `format: "leaderboard"` summarizes valid entries in
the first three supplied ranking slots of each enabled weekly, monthly, and
lifetime list, using its `unit` setting for quantities.
`format: "subscription_leaderboard"` instead accepts `weekly_leaderboard`,
`monthly_leaderboard`, and `leaderboard` without enable flags, labeling the
last list Overall. Each absent subscription field permits the corresponding
enabled legacy `gifts_*` list as a fallback. Valid empty lists display `empty`.
`format: "goal"` summarizes available integer
current/target values and string status. These conversions preserve the
structured records; the built-in Kick formats apply them to `metadata.data`.
A field definition with `format: "prediction"` summarizes a prediction object:
question, string state, outcome vote counts, and a complete total. Invalid or
missing counts display as `votes unknown`; a total requires every supplied
outcome to have a nonnegative integer count. Non-object outcomes are omitted
from display and prevent a total. Untitled outcomes use `Outcome N` based on
their original position. In `RESOLVED` state, a nonblank explicit winner ID must
match exactly one outcome before a winner is shown. The built-in Kick formats
apply this conversion to `metadata.data.prediction`; raw payloads are preserved.
A field definition with `format: "nonnegative_integer"` renders nonnegative
integer counts, including zero. Booleans, negative numbers, numeric strings,
and other malformed values suppress the entire field template. The built-in
Kick activity formats use this conversion for subscriber counts and months.
A field definition with `format: "iso8601"` renders a date string as UTC
`YYYY-MM-DD HH:MM:SS`, treating dates without a timezone as UTC. Invalid dates
render as an empty value; use `omit_if_false: true` to omit their template too.
Custom `timestamp` and `received_timestamp` strftime formats preserve fractional
seconds with `%f`; the built-in formats continue to display whole seconds.
Conditional fields suppress their complete rendered
fragment for false, zero, empty, or null values, with the `count` exception
described above. The final formatted string,
including custom-template text, renders carriage returns, newlines, and Unicode
line separators visibly as `\r`, `\n`, `\u0085`, `\u2028`, or `\u2029` and
preserves horizontal tabs while removing other terminal controls. This keeps
one physical line per record while JSONL remains lossless. Lone Unicode
surrogates in the final text are escaped so it can be written as UTF-8. These
controls support natural event notices without adding presentation-only fields
to normalized JSONL records.
Redirected stdout uses its destination encoding and visibly escapes characters
that encoding cannot represent, rather than silently discarding them. The
destination's text encoder is preserved across records, including encodings that
write an initial signature. File writers always use UTF-8.

The runtime can attach multiple output writers when `output` is a list or when
the CLI receives repeated `--output` flags. Use `.jsonl` for structured chat
output. JSON-array `.json` output is not supported.

Attached output writers initialize on the first record. A successful
zero-record run therefore does not create configured `.jsonl` or `.txt` files;
the runtime logs each uncreated path and includes `file_created: False` in its
debug writer summary.

Output paths support `{title}` and `{id}` placeholders, with `{{` and `}}` for
literal braces. Other fields, conversions, and format specifications are
rejected. The substituted values
are sanitized as single filename components. Targets are deduplicated after
placeholder expansion and canonical path resolution. A name with substituted
fields must create a new file exclusively, even when `overwrite=False`; existing
generated targets raise `FileExistsError`. Explicit fixed paths keep their
overwrite/append behavior. Writers reject symbolic links, hard links, special
files, and unverifiable identities before changing contents. Repair and writing
use the same validated descriptor; newly created POSIX files use mode `0600`.
YouTube channel discovery keeps distinct templates until the selected video's
metadata is available. Final aliases and format conflicts are checked before
any lazy output file opens; already resolved outputs retain preflight
deduplication for verification.
Append preserves the last existing LF or CRLF newline style for tail repair and
new records. Without an existing terminator it uses the host convention, except
that a trailing CR establishes CRLF. A partial trailing CR is replaced by the
selected terminator; existing mixed endings are not normalized.

File writers are crash-resilient: each record is flushed on write and the file
is synchronized to disk periodically (about every 60 seconds). JSONL accepts
JSON-compatible values, so callers must convert `datetime` objects to UTC
integer microseconds or ISO 8601 strings. A top-level naive `datetime` receives
a specific `ValueError`; other raw datetimes are rejected by the JSON encoder.
Nonfinite floats, including nested NaN and infinity values, raise `ValueError`
before the affected record is written. Earlier records in the capture remain
intact.
Kick pin records reserve the top-level `timestamp` for the pin event time.
Startup pin state has no event time and omits it; the original chat message time
is available as `metadata.original_message_created_at`, with
`metadata.pinned_message_created_at` retained as a compatibility alias.
Kick subscription-renewal celebrations remain `text_message` records and expose
their provider ID, kind, total-month count, and normalized event timestamp under
`metadata.celebration`.
Kick live poll updates and deletions use the `poll_update` and `poll_deleted`
message types in the opt-in `polls` group. Because the provider omits IDs and
timestamps, these records receive monotonic namespaced IDs and
`received_timestamp`; update metadata retains countdown, option, and vote state.

## Debugging Helpers

`chat_downloader.debugging` provides:

- `log(...)` and `debug_log(...)` for package logging

`chat_downloader.redaction` provides:

- `sanitize_for_log(...)` for redacting cookies, proxies, known secret fields,
  custom header names containing auth/token/secret/credential markers, API-key
  headers, and Basic/Bearer/OAuth/SAPISIDHASH values before logging or capture
- `capture_debug_sample(label, payload, *, sample_limit=None,
  sample_group=None, group_limit=None)` for opt-in sanitized sample capture

Project log handlers sanitize structured values, exception text, stack
information, URL credentials, sensitive query parameters, and terminal control
characters before rendering records.

Malformed YouTube continuation bodies and body-derived retry titles are excluded
from diagnostics and run errors. Semantic provider entries also redact the
`value` when either `key` or `name` identifies a known sensitive field.

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
Set `sample_group` and `group_limit` together to apply a second aggregate bound
across related labels.

Dynamic YouTube anomaly labels retain independent ten-payload per-label limits
and a fixed 50-sample aggregate limit for each family (empty action parse,
missing keys, untyped action, unknown message type, unknown action, and unknown
continuation). The aggregate reservation runs first, so exhausted families do
not accumulate new label keys. Failed writes release aggregate and per-label
claims, except after a successful publication; an identical prior sample can
still reuse its deterministic path. These anomaly quotas require the shared
capture opt-in and debug logging, and are separate from clean-response sampling.

Clean-run provider captures require a second explicit opt-in because they
contain ordinary public chat data. Set
`CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES=1` to capture the first three
structurally valid YouTube continuation responses in a retrieval run. API-error
and structurally incomplete responses are excluded.

Set `CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1` to capture the first three
valid raw Twitch IRC frames across reconnects. The shared capture flag and
debug logging must also be enabled.

Set `CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES=1` to capture one valid
raw Twitch IRC frame per known normalized message type, using a bounded,
collision-resistant raw-action fallback for unknown types, up to 12 event keys
across reconnects. Recognition uses raw `msg-id` mapping keys;
normalized-looking unknown values cannot alias genuine events. With no
`msg-id`, only known raw actions can use the parsed normalized type. Known
actions retain readable labels; unknown actions are sanitized before hashing
and use opaque `unknown-<digest>` labels so provider or credential fragments do
not enter paths or capture logs. Failed writes are retried once per key. The
backend's 12-sample group limit and one-sample-per-label limit are shared per
process and output directory, so a later run may retain fewer samples. An exact
prior payload can reuse its deterministic path; a different payload for that
event key is rejected even if group slots remain. This mode also requires the
shared capture flag and debug logging. It also has independent three-frame
quotas for emote-bearing text and replies. When both Twitch modes are enabled,
their limits are additive: at most 21 clean-traffic raw-frame samples, with one
frame potentially appearing in multiple quotas. Capture occurs before live
deduplication and output filtering; separate drift samples can add files and
overlap unknown fallback frames.

Set `CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES=1` to capture up to three successfully
parsed raw Kick WebSocket frames per normalized event type across reconnects.
It likewise requires the shared capture flag and debug logging; control,
unsupported, and malformed frames remain excluded from successful samples.

`chat_downloader.debug_sample_utils` contains naming helpers used to turn
captured samples into stable fixture names.

## Public Errors

The exported downloader-specific errors derive from `ChatDownloaderError`.
Direct API calls can also propagate configuration `ValueError`/`TypeError`,
standard I/O errors, and transport exceptions; catching the package base class
does not catch those failures. Internal testing-mode controls use a separate
`debugging.TestingException`.

Common exceptions:

- `InvalidURL`
- `URLNotProvided`
- `VideoNotFound`
- `VideoUnavailable`
- `LoginRequired`
- `CaptchaChallengeRequired`
- `KickCountryBlocked`
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
    KickCountryBlocked,
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
`KickCountryBlocked`, and `KickError` are all re-exported at the top level:

```python
from chat_downloader import KickChatDownloader, KickCountryBlocked, KickError
```

`get_all_sites()` returns every registered site class, including
`KickChatDownloader`.

Kick URLs (`kick.com/{username}` for live chat,
`kick.com/{username}/videos/{uuid}` for VOD replay, and
`kick.com/{username}/clips/{clip_id}` for clip replay) work through the
standard `get_chat()` and `get_chat_request()` entry points like any other
site.

### Replay checkpoints and automatic parity

Use `run(resume="capture.checkpoint.json", verify_output=True, ...)` with explicit
JSONL and TXT output paths to resume a completed recording. These are runtime
controls on `run()`, not fields of `ChatRequest` or `get_chat()`.
See [CLI capture recovery](cli-usage.md#replay-checkpoints-and-output-verification)
for shutdown semantics, immutable settings, and recovery limits.

For Kick live URLs, `run(verify_output=True, ...)` also inspects closed JSONL
records and reconciles provider counters with the current run without requiring
debug logging. The content-free report appears in `RunResult.provider_inspection`
and run manifests. Review findings or inspection errors fail the run even when
TXT parity passes; an original retrieval error retains priority. Kick replay
verification remains parity-only; use the offline inspector with the debug
logs from each replay run to reconcile an appended archive.

YouTube video, clip, and channel/handle captures also run provider inspection
with `verify_output=True`. Missing mobile source timestamps remain observations;
unknown types, invalid records, count mismatches, and known parser loss require
review. Resumed captures include checkpoint-verified prior records. Twitch live
inspection retains its IRC frame and record diagnostics. These reports share
the run's single deadline-accounting observation.

Kick VOD and clip messages now include `time_in_seconds` and `time_text`,
relative to the recording or clip origin even when selecting a later start.
Absolute provider timestamps remain available in `timestamp`.


`run(require_complete=True, run_manifest="run.json", ...)` opts into replay
completion enforcement and a content-free run report. Manifest names must be
new and distinct from output/checkpoint paths. Reaching a message limit, timing
out, or retaining known malformed-record loss fails completion even if output
parity passes. Checkpointed loss survives resume. See
[completion and manifests](cli-usage.md#replay-completion-and-run-manifests) for
empty-window, append, and compatibility semantics.
