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

Capture a Kick live channel, including subscription and moderation events:

```bash
chat_downloader "https://kick.com/xqc" \
  --message_groups messages subscriptions moderation \
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

Enable automatic YouTube profile fallback on incomplete continuations:

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
- `--auto_profile_fallback` — rotate YouTube request profiles when
  continuation payloads are repeatedly incomplete.
- `--twitch_client_id` — override the public Twitch Client-ID for GraphQL
  and replay requests.
- `--user-agent`, `--header "Name: Value"` (repeatable) — request headers.

Kick accepts `--start_time` and `--end_time` for VOD replay URLs. Kick live
channel URLs reject these bounds because the public live feed cannot seek.

Debug and automation:

- `--logging debug`, `--verbose` — transport and parser debugging.
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
  Cloudflare bot-protection page; installing `cloudscraper` (a dependency) lets
  the REST client clear most JS challenges, but endpoint/VPN reputation can
  still trigger one.
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
