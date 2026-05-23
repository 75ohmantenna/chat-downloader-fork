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
| `csv`   | Flattens nested fields and rewrites the file when new columns appear. |
| `txt`   | Applies the configured message formatter. |

JSON-array `.json` output is no longer supported. Use `jsonl` for structured
chat output, especially for long-running live captures.

File output is crash-resilient: every record is flushed to the OS as it is
written, and the file is `fsync`-ed periodically (about every 60 seconds), so
captures survive process crashes and power loss with minimal data loss.

## Common Flags

Run `chat_downloader --help` for the complete argument list. The CLI is
generated from metadata on `DownloaderConfig`, `ChatRequest`, and `RunConfig`
in `chat_downloader/models.py`.

Filtering and output:

- `--message_groups` or `--message_types` — filter events.
- `--format` or `--format_file` — change rendered text output.
- `--output` — write one or more files (repeatable).
- `--max_messages`, `--start_time`, `--end_time` — bound the capture.
- `--timeout`, `--inactivity_timeout` — bound long-running captures.

Request control:

- `--connect_timeout`, `--read_timeout` — HTTP timeouts.
- `--proxy`, `--cookies` — proxy and cookie jar.
- `--request_profile` — Grayjay-inspired request header presets.
- `--auto_profile_fallback` — rotate YouTube request profiles when
  continuation payloads are repeatedly incomplete.
- `--twitch_client_id` — override the public Twitch Client-ID for GraphQL
  and replay requests.
- `--user-agent`, `--header "Name: Value"` (repeatable) — request headers.

Debug and automation:

- `--logging debug`, `--verbose` — transport and parser debugging.
- `--quiet`, `--testing`, `--pause_on_debug`, `--exit_on_debug` — automation
  and parser-debug workflows.

## Troubleshooting

- `403`, `429`, or `LoginRequired` errors usually mean the platform wants
  cookies, a slower request pace, or both.
- `CaptchaChallengeRequired` means Twitch or YouTube returned an explicit
  challenge response that the library cannot solve automatically.
- Use `jsonl` for long or live captures.
- If a platform changes its private APIs, rerun with `--logging debug` and
  inspect the site-specific code under `chat_downloader/sites/`.
- On `SIGTERM` (e.g. `systemd` stopping the service, or `kill <pid>`) the CLI
  shuts down gracefully: the signal is translated into a `KeyboardInterrupt` so
  output writers flush before exit. Sending a second signal restores the
  default handler and exits immediately.
- For deeper platform behavior, see
  [`youtube-integration-guide.md`](youtube-integration-guide.md) and
  [`twitch-integration-guide.md`](twitch-integration-guide.md).
