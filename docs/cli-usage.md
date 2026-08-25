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
| `txt`   | Applies the configured message formatter. |

Other extensions, including `.json` and `.csv`, are unsupported. Output paths
must end in `.jsonl` or `.txt`.

Output writers initialize lazily on the first record. When a successful run
retrieves zero records, configured `.jsonl` and `.txt` files are not created.
The final info log names each uncreated lazy output, and the debug run summary
reports `file_created: False` with `records_written: 0`.

Twitch text output preserves system-event descriptions for subscription,
raid, and unraid messages. JSONL remains the lossless structured format when
downstream processing needs provider-specific metadata.

Custom format field definitions accept a `template` and an optional
`singular_template`. The singular form is selected only for an exact numeric
value of one; zero, other numbers, booleans, and numeric strings use the normal
template.

Kick text output labels subscription, pin, host, and moderation events. Events
without ordinary message text render a bracketed notice instead of a blank
line; JSONL retains their structured identifiers and metadata.

YouTube text output renders moderation events without message text as a
bracketed notice. The notice identifies the removed message or affected author
when YouTube supplies that identifier, instead of writing a blank line.
YouTube JSONL retains both the main and ticker forms of paid events; replay
pairs share the precise provider offset even when the ticker's nested display
text is rounded to whole seconds. TXT output emits one semantic paid event.

Output names may contain `{title}` and `{id}` placeholders. Metadata is
sanitized before substitution. Duplicate targets are removed after expansion,
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

Kick accepts `--start_time` and `--end_time` for VOD replay URLs. Kick live
channel URLs reject these bounds because the public live feed cannot seek.

Debug and automation:

- `--logging debug`, `--verbose` — transport and parser debugging.
- With `CHAT_DOWNLOADER_CAPTURE_DEBUG_SAMPLES=1`, Kick captures bounded,
  sanitized samples for unknown or malformed REST/Pusher payloads; see the
  Kick integration guide for storage and review guidance.
- With both that setting and
  `CHAT_DOWNLOADER_CAPTURE_TWITCH_IRC_FRAMES=1`, Twitch captures the first
  three successfully parsed raw IRC frames for clean-run schema review.
- With both that setting and `CHAT_DOWNLOADER_CAPTURE_KICK_FRAMES=1`, Kick also
  captures the first three successfully parsed raw event frames for clean-run
  schema review.
- YouTube continuation polls report separate processed-action and
  emitted-message counts. Non-emitting actions are aggregated by bounded
  reason: known ignored controls or renderers, unparsed actions, invalid
  messages, message type/group filtering, and replay time-range filtering or
  stop.
- YouTube terminal continuation responses finish immediately without an
  unnecessary final wait. Empty replay pages continue when the provider
  supplies another continuation token.
- Successful debug runs end with total and per-type retrieved-message counts,
  provider diagnostics when available, the number of semantic duplicates
  suppressed across formatted file outputs, and the creation state and
  completed-record count for each output writer. Kick live diagnostics include
  decoded, control, parsed, unsupported, unknown-message-type, malformed, and
  invalid-frame counts; reconnect and Pusher-key recovery counts; and the last
  decoded-frame timestamp. A duplicate is counted once even when multiple
  formatted writers are attached; raw-only outputs do not increase the
  suppression count. Zero-record lazy outputs are named explicitly as files
  that were not created.
- `--quiet`, `--testing`, `--pause_on_debug`, `--exit_on_debug` — automation
  and parser-debug workflows.

## Troubleshooting

- `403` or `LoginRequired` often means the platform requires cookies; `429`
  means the client is rate-limited and should retry more slowly.
- A cookie/proxy safety error can come from `HTTP_PROXY`, `HTTPS_PROXY`, or
  `ALL_PROXY` even when `--proxy` was not supplied. Remove the remote proxy or
  pass `--proxy ""` if direct connections are intended.
- `CaptchaChallengeRequired` means a platform returned an explicit challenge
  response that the library cannot solve automatically. On Kick this is a
  Cloudflare bot-protection page. The bundled `curl-cffi` and `cloudscraper`
  fallbacks can clear some challenges, but endpoint or VPN reputation can still
  trigger one.
- Use `jsonl` for long or live captures.
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
