# chat-downloader-fork

Maintained YouTube, Twitch, and Kick livestream-chat CLI and Python API. This is
a personal fork of
[`xenova/chat-downloader`](https://github.com/xenova/chat-downloader),
targeting Python 3.12+; CI validates Python 3.12, 3.13, and 3.14.

## Personal Fork — No Support

I maintain this fork for my own use and publish it in case it is useful to
others. Accordingly:

- **No support is offered.** Issues, pull requests, and feature requests are
  not accepted.
- **Do not file issues or PRs against
  [`xenova/chat-downloader`](https://github.com/xenova/chat-downloader)** for
  problems originating in this fork.
- **No warranty.** See [`LICENSE`](LICENSE).

## Development Notes

This fork has been developed mostly with Anthropic's Claude and OpenAI's
Codex. Hosted CI runs the canonical `make ci` quality gate on pushes and pull
requests: `ruff check`, `ruff format`, `mypy`, and `pytest` must all pass and
line coverage is enforced at 100%. These checks do not constitute a security
audit and do not replace your own review. **Use at your own risk.**

## Supported Platforms

| Platform | Current focus |
| --- | --- |
| YouTube | Live chat and replay chat (text, paid messages, memberships) |
| Twitch  | Live IRC chat with broad event coverage; VOD and clip chat replay (text messages) |
| Kick    | Live Pusher chat with full event coverage (text, subs, gifts, bans, pins, hosts) |

## Installation

```bash
uv tool install git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

Or with pipx:

```bash
pipx install git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

## Updating

Use the same tool that owns the existing installation. For an installation
managed by `uv`:

```bash
uv tool install --force git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

For an installation managed by pipx:

```bash
pipx install --force git+https://github.com/75ohmantenna/chat-downloader-fork.git
```

**Verify the installed command:**

```bash
chat_downloader --version
which chat_downloader
```

## First Run

```bash
chat_downloader "https://www.youtube.com/watch?v=QBFiiEVBWvE"
```

Full CLI examples, output-format details, options, and troubleshooting are in
[`docs/cli-usage.md`](docs/cli-usage.md).

## Documentation

- [`docs/cli-usage.md`](docs/cli-usage.md) — CLI recipes, flags, output
  formats, troubleshooting
- [`docs/python-api-reference.md`](docs/python-api-reference.md) — embeddable
  Python API and typed dataclass reference
- [`docs/capability-inventory.md`](docs/capability-inventory.md) — behavior
  preservation checklist for refactors
- [`docs/youtube-integration-guide.md`](docs/youtube-integration-guide.md) —
  YouTube capture flow and module map
- [`docs/twitch-integration-guide.md`](docs/twitch-integration-guide.md) —
  Twitch capture flow and transport map
- [`docs/kick-integration-guide.md`](docs/kick-integration-guide.md) —
  Kick Pusher live capture and VOD replay flow
- [`docs/development-workflow-guide.md`](docs/development-workflow-guide.md) —
  development workflow and validation commands

## Credit

This fork is maintained by
[`75ohmantenna`](https://github.com/75ohmantenna). Fork-specific
modifications, enhancements, documentation, and packaging changes are credited
to `75ohmantenna`.

Credit for the core idea and the original YouTube and Twitch implementations
belongs to [`xenova`](https://github.com/xenova) and the upstream
[`chat-downloader`](https://github.com/xenova/chat-downloader) contributors.
Their original MIT license notice is preserved in [`LICENSE`](LICENSE).

## License

[MIT](LICENSE)
