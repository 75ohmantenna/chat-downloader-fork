# CLI Usage

End-user reference for the `chat_downloader` command-line interface: common
recipes, the supported output formats, the most useful flags, and a short
troubleshooting guide.

For the embeddable Python API see
[`python-api-reference.md`](python-api-reference.md). For the development
workflow see [`development-workflow-guide.md`](development-workflow-guide.md).

## Quick Start

Print messages to stdout:

```bash
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" --max_messages 20
```

YouTube channel, user, and handle live shortcuts are accepted directly:

```bash
chat_downloader "https://www.youtube.com/@example/live" --max_messages 20
```

Capture a Twitch VOD to JSONL:

```bash
chat_downloader "https://www.twitch.tv/videos/123456789" \
  --output vod-chat.jsonl \
  --max_messages 500
```

Write the same run to two formats at once:

```bash
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" \
  --output chat.jsonl \
  --output chat.txt
```

Capture every supported Kick live event type:

```bash
chat_downloader "https://kick.com/xqc" \
  --message_groups all \
  --output kick-chat.jsonl
```

Capture Kick VOD chat replay:

```bash
chat_downloader "https://kick.com/xqc/videos/<uuid>" \
  --output kick-vod.jsonl
```

Capture chat for a Kick clip:

```bash
chat_downloader "https://kick.com/<channel>/clips/<clip_id>" \
  --output kick-clip.jsonl
```

Use cookies and custom headers:

```bash
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" \
  --cookies cookies.txt \
  --request_profile youtube_android \
  --header "Accept-Language: en-US,en;q=0.9"
```

Enable automatic YouTube profile fallback during bootstrap and continuations:

```bash
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" \
  --request_profile youtube_web \
  --auto_profile_fallback true
```

Restrict output to a time window:

```bash
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE" \
  --start_time 00:10:00 \
  --end_time 00:12:30
```

## Output Formats

| Format | Notes |
| --- | --- |
| `jsonl` | One JSON object per line. Best for long or live captures. |
| `txt`   | Applies the configured message formatter, one physical line per record. |

Other extensions, including `.json` and `.csv`, are unsupported. Output paths
must end in `.jsonl` or `.txt`.

Formatted stdout and TXT output flatten the final rendered string, including
custom-template text. Carriage returns, newlines, and Unicode line separators
render visibly as `\r`, `\n`, `\u0085`, `\u2028`, or `\u2029`, preserving the
one-record-per-line boundary. Horizontal tabs remain intact; other terminal
control characters are removed. JSONL retains the original message characters
for lossless downstream processing.

Output writers initialize lazily on the first record. When a successful run
retrieves zero records, configured `.jsonl` and `.txt` files are not created.
The final info log names each uncreated lazy output, and the debug run summary
reports `file_created: False` with `records_written: 0`.

Twitch text output preserves system-event descriptions for subscription,
raid, and unraid messages. JSONL remains the lossless structured format when
downstream processing needs provider-specific metadata.

Custom format field definitions accept a `template`, optional
`singular_template`, and optional `omit_if_false`. The singular form is selected
only for an exact numeric value of one; zero, other numbers, booleans, and
numeric strings use the normal template. `omit_if_false: true` suppresses the
field's complete rendered fragment for false, zero, empty, or null values.

Kick's default text format labels subscription, pin, host, and moderation
events. Host notices include the host name, viewer count, and optional message,
including compact live host payloads without provider IDs or timestamps.
Events without ordinary message text render a bracketed notice instead
of a blank line; JSONL retains their structured identifiers and metadata. When
a user sends a Kick subscription-renewal celebration, it remains an ordinary
`text_message` so message-only captures preserve the chat text. JSONL also
retains its provider ID, renewal type, total-month count, and normalized event
time under `metadata.celebration`; TXT keeps the ordinary chat rendering. When
a Kick live poll is active, the opt-in `polls` message group emits each poll
state update with its title, countdown, options, and vote counts, followed by a
`poll_deleted` state event when Kick removes it. TXT labels both event types;
JSONL is the lossless representation for changing poll state. When
a Kick live event omits its provider timestamp, JSONL records a separate
`received_timestamp` in UTC microseconds and TXT uses it as a `[received]`
display fallback. AI deletion notices retain their AI-moderated marker and
violated-rule labels in TXT instead of becoming indistinguishable from ordinary
deletions.

YouTube text output renders moderation events without message text as a
bracketed notice. The notice identifies the removed message or affected author
when YouTube supplies that identifier, instead of writing a blank line.
YouTube JSONL retains both the main and ticker forms of paid events; replay
pairs share the precise provider offset even when the ticker's nested display
text is rounded to whole seconds. TXT output emits one semantic paid event.

Output names may contain `{title}` and `{id}` placeholders; use `{{` and `}}`
for literal braces. Other fields, conversions, and format specifications are
rejected when the request is validated. Metadata is sanitized before
substitution. Duplicate targets are removed after expansion,
path resolution, and existing-file identity checks, so aliases and hard links
do not receive the same message twice.

File output is crash-resilient: every record is flushed to the OS as it is
written, and the file is synchronized to disk periodically (about every 60
seconds), so captures survive process crashes and power loss with minimal data
loss. Writers also perform a final `fsync` during normal shutdown; a flush or
sync failure is reported as an output error instead of allowing the capture to
appear successful. When appending to JSONL, a crash-truncated final record is
removed before new records are written; a complete final record missing only
its newline is kept and terminated. Text append mode similarly terminates an
existing final line before writing the next record. Equivalent output paths
that resolve to the same file are deduplicated so each message is written once.

## Common Flags

Run `chat_downloader --help` for the complete argument list. The CLI is
generated from metadata on `DownloaderConfig`, `ChatRequest`, and `RunConfig`
in `src/chat_downloader/models/`.

Filtering and output:

- `--message_groups` and `--message_types` are mutually exclusive CLI filters.
  Pass multiple names as one comma-separated argument.
  Use `--message_groups all` for a provider's complete supported event surface.
  In typed API requests, an explicit `ChatRequest.message_types` value overrides
  `message_groups`, including the `all` group.
- `--format` or `--format_file` — change rendered text output.
- `--output` — write one or more files (repeatable).
- `--max_messages`, `--start_time`, `--end_time` — bound the capture.
- `--timeout`, `--inactivity_timeout` — bound long-running captures.

Request control:

- `--connect_timeout`, `--read_timeout` — HTTP timeouts.
- `--message_receive_timeout` — live socket receive polling timeout; Twitch and
  Kick enforce a one-second minimum to avoid idle CPU churn (messages are still
  delivered immediately when data arrives).
- `--proxy`, `--cookies` — proxy and cookie jar. When `--proxy` is omitted,
  standard proxy environment variables apply. Cookie authentication rejects an
  effective remote proxy and warns for a loopback proxy; pass `--proxy ""` to
  disable environment proxies explicitly.
- `--request_profile` — request-header preset: `youtube_web`,
  `youtube_android`, `youtube_ios`, or `twitch_web`. Unknown names fail during
  configuration.
- `--auto_profile_fallback` — rotate YouTube request profiles when initial
  playability is generically unavailable or continuation payloads are
  repeatedly incomplete. Explicit `--user-agent` and `--header` values remain
  authoritative during rotation.
- `--youtube_replay_poll_interval` — explicitly override completed YouTube
  replay polling with an interval from 0.5 through 8 seconds. The default
  respects the provider delay; faster polling is opt-in and can be rate-limited.
- `--twitch_client_id` — override the public Twitch Client-ID for GraphQL
  and replay requests.
- `--user-agent`, `--header "Name: Value"` (repeatable) — request headers.

Kick accepts `--start_time` and `--end_time` for VOD and clip replay URLs.
VOD bounds are relative to the recording; clip bounds are relative to the clip
and clamp to its duration. Kick live channel URLs reject these bounds because
the public live feed cannot seek.

Debug and automation:

- `--logging debug`, `--verbose` — transport and parser debugging.
- With `CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1`, Kick captures bounded,
  sanitized samples for unknown or malformed REST/Pusher payloads; see the
  Kick integration guide for storage and review guidance.
- With both that setting and
  `CHAT_DOWNLOADER_CAPTURE_YOUTUBE_RESPONSES=1`, YouTube captures the first
  three structurally valid continuation responses for clean-run schema review.
- With both that setting and
  `CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1`, Twitch captures the first
  three successfully parsed raw IRC frames for clean-run schema review.
- With both that setting and
  `CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_EVENT_FRAMES=1`, Twitch captures one
  successfully parsed raw IRC frame per known normalized message type, with an
  unknown-type raw-action fallback, up to 12 event keys across reconnects. Raw
  `msg-id` recognition prevents normalized-looking unknowns from aliasing known
  events. Unknown action labels are opaque and hash only sanitized action
  identities, keeping provider and credential fragments out of paths and
  capture-path logs.
  Backend group and per-label quotas are shared per process and output
  directory, so later runs can retain fewer or reuse only an exact prior
  payload. The event mode also has independent three-frame quotas for
  emote-bearing text and replies. If both Twitch modes are enabled, the ceiling
  is 21 clean-traffic raw-frame samples, and one frame can appear in multiple
  quotas. Capture precedes live deduplication and output filtering; drift
  samples can be additional.
- With both that setting and `CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES=1`, Kick also
  captures the first three successfully parsed raw frames per normalized event
  type for clean-run schema review.
- YouTube continuation polls report separate processed-action and
  emitted-message counts. Non-emitting actions are aggregated by bounded
  reason: known ignored controls or renderers, unparsed actions, invalid
  messages, message type/group filtering, and replay time-range filtering or
  stop.
- YouTube terminal continuation responses finish immediately without an
  unnecessary final wait. Empty replay pages continue when the provider
  supplies another continuation token.
- Debug runs, including failures, end with status, total and per-type
  retrieved-message counts, provider diagnostics when available, the number of
  semantic duplicates
  suppressed across formatted file outputs, the number of source items
  prefetched but excluded at a retrieval deadline, whether that count is final,
  and the creation state and completed-record count for each output writer. A
  false `deadline_prefetch_count_complete` means the provider is still returning
  from a bounded shutdown; treat the accompanying count as a lower bound. Kick
  live diagnostics include
  decoded, control, parsed, unsupported, unknown-message-type, malformed, and
  invalid-frame counts; reconnect and Pusher-key recovery counts; and the last
  decoded-frame timestamp. Twitch live diagnostics include recognized optional
  metadata degradations; connection attempts, successes, setup failures, and
  reconnects; received IRC frames, recognized benign control frames, and
  parsed messages; receive timeouts and idle-watchdog expirations; distinct
  sent and received `PING`/`PONG` counts; and duplicate, filtered, and
  emitted-message counts. Provider summaries
  contain no endpoints, raw frames, error paths/messages, or chat content.
  A duplicate is counted once even when multiple
  formatted writers are attached; raw-only outputs do not increase the
  suppression count. Zero-record lazy outputs are named explicitly as files
  that were not created.
- `--quiet`, `--testing`, `--pause_on_debug`, `--exit_on_debug` — automation
  and parser-debug workflows.

## Troubleshooting

- `403` or `LoginRequired` often means the platform requires cookies; `429`
  means the client is rate-limited and should retry more slowly.
- `KickCountryBlocked` means Kick returned its provider-specific HTTP 423 for
  the request's country or region. It is terminal and is not retried.
- A cookie/proxy safety error can come from `HTTP_PROXY`, `HTTPS_PROXY`, or
  `ALL_PROXY` even when `--proxy` was not supplied. Remove the remote proxy or
  pass `--proxy ""` if direct connections are intended.
- `CaptchaChallengeRequired` means a platform returned an explicit challenge
  response that the library cannot solve automatically. On Kick this is a
  Cloudflare bot-protection page. The bundled `curl-cffi` and `cloudscraper`
  fallbacks can clear some challenges, but endpoint or VPN reputation can still
  trigger one.
- Use `.jsonl` for long or live captures.
- If a platform changes its private APIs, rerun with `--logging debug` and
  inspect the site-specific code under `src/chat_downloader/sites/`.
- The CLI exits with a nonzero status on failure or when interrupted
  (`KeyboardInterrupt` / `SIGTERM`); exit status `0` means a clean run.
- On `SIGTERM` (e.g. `systemd` stopping the service, or `kill <pid>`) the CLI
  shuts down gracefully: the signal is translated into a `KeyboardInterrupt` so
  output writers flush before exit. Sending a second signal restores the
  default handler and exits immediately.
- For deeper platform behavior, see
  [`youtube-integration-guide.md`](youtube-integration-guide.md),
  [`twitch-integration-guide.md`](twitch-integration-guide.md), and
  [`kick-integration-guide.md`](kick-integration-guide.md).

## Replay checkpoints and output verification

```bash
chat_downloader "https://kick.com/examplechannel/videos/VIDEO_ID" \
  --output capture.jsonl --output capture.txt \
  --resume capture.checkpoint.json --verify_output --logging debug
```

`--resume PATH` creates a checkpoint for a completed replay. Run the same command
again to append the remaining chat. The initial output files must be absent;
use explicit filenames without `{title}` or `{id}` placeholders. The checkpoint
and each output must be distinct regular files. At most one JSONL and one TXT
output are accepted. Create the checkpoint's parent directory before running;
the CLI reports a missing directory before starting capture. The checkpoint
takes precedence over `--overwrite` and always uses append mode. Stable message
IDs and finite replay-relative offsets are required; signed offsets are accepted
for YouTube preroll items. Kick VODs and clips provide both fields.
Provider-generated terminal
markers without replay offsets are omitted from checkpointed captures.

Checkpoints advance after output writers close and synchronize on normal,
message-limited, or interrupted shutdown. A one-second overlap preserves
messages at the saved timestamp while suppressing IDs already written. The
runner discards a record interrupted during writing or checkpoint observation
before saving, including a partial write to one of two outputs. The next run
retrieves that record again. The record limit counts newly written messages.
Formatted TXT may write fewer lines
than JSONL when paid chat and ticker events share an ID; this does not prevent
checkpoint saving. Keep the URL, filters, selected
start/end, format, and filenames unchanged; message limits and timeouts may
change. Request settings, the package version, built-in format definitions,
and file hashes are checked before append. Checkpoints created before these
version and format checks were added cannot be resumed; start a new capture.
If chat shutdown fails, the checkpoint does not advance.

A process crash, power failure, or failed chat shutdown can leave output newer
than its checkpoint.
The next run rejects that mismatch without truncating files. Preserve the
artifacts for inspection and start a fresh capture to recover. A `.lock` file
prevents simultaneous checkpoint writers; after an unclean exit, remove that
lock only after confirming the previous process has stopped. Checkpoints are
shutdown recovery points, not continuous crash journals.

`--verify_output` checks exact formatting, semantic deduplication, UTF-8, and
physical line endings after a successful run. It requires one JSONL and one TXT
output; append verification requires a resume checkpoint so run boundaries are
known. A verification error makes the command fail. A zero-message success can
have no files because writers initialize lazily. The run summary separately
reports success, termination reason, parity status, and writer counts. A parity
pass does not prove complete provider history.

Kick replay TXT now shows recording-relative time, matching the accompanying
`time_in_seconds` and `time_text` JSONL fields. Absolute timestamps remain in
JSONL. Replay diagnostics report HTTP statuses and latency, pages, raw and
emitted records, selected and observed time bounds, and the termination reason.


## Replay completion and run manifests

Add `--require_complete` to fail the command unless a completed recording's
selected window is exhausted without known record loss. Timeouts, inactivity
timeouts, interrupts, and message limits do not qualify, even when file parity
passes. Reaching exactly `--max_messages` is conservatively treated as limited:
exhaustion has not been observed. Live and unknown recording states are rejected
before writing. Explicit empty windows may complete normally. Completion means
provider history traversal, not proof that the provider retained every event.
Twitch pagination stalls and known skipped Twitch or YouTube replay records
prevent a capture from qualifying as complete.

`--run_manifest run.json` writes a JSON report after shutdown with program
version, recording identity/window, success, completion, termination, parity,
current/prior run message counts, and output writer counts and SHA-256 hashes.
It contains no chat messages, request headers, cookies, or input URL. Hashes cover
whole files; writer counts cover the current run. Unopened pre-existing files
are not hashed unless a resume checkpoint verified them. The manifest must be
a new path distinct from outputs, checkpoint, and checkpoint lock. Use a new
manifest filename on every resume. Create its parent directory before running;
the CLI reports a missing directory before starting capture. Failure to write
the manifest fails the run.

```bash
chat_downloader "https://kick.com/examplechannel/videos/VIDEO_ID" \
  --output capture.jsonl --output capture.txt \
  --resume capture.checkpoint.json --verify_output \
  --require_complete --run_manifest run.json
```

A message-limited or interrupted run still saves its valid shutdown checkpoint.
Known malformed-record loss is retained in the checkpoint and prevents a later
resume from certifying the archive complete. Start a fresh capture after the
provider or parser issue is resolved. Existing checkpoints without this field
have no recorded loss history; this is not a retroactive integrity audit.

Kick replay emits collection progress at info level at most every five seconds
between pages, then announces chronological output. Metadata fallback
and material end-time/duration disagreements are also explained at info level.

For Twitch live channels (including upcoming streams), `--verify_output` also
runs the Twitch capture inspector after outputs close. `provider_inspection` in
the run result, debug summary, and run manifest contains the same content-free
JSONL findings and IRC frame accounting as the offline inspector. Its `status`
is `ok`, `review`, or `error`; a review or inspection error makes the run
unsuccessful even when `parity_status` is `passed`. Parity is still checked if
inspection fails, and its independent status remains available. Other providers
and Twitch replays retain parity-only verification. Closed partial captures are
also inspected after retrieval failures when
the output pair was validated; the original error remains authoritative and
parity stays `not_run`.

The report also records `prefetched_after_deadline_count` and
`deadline_prefetch_count_complete`. Parsed IRC messages need not equal output
records: filtering, deduplication, and deadline prefetch can exclude messages.
A deadline can leave frame accounting incomplete; a gap requests review rather
than proving lost output. Empty lazy captures are inspected without creating
files. Run manifests identify the recording through its configured provider's
name, or `null` when provider metadata is unavailable.
