# Twitch Site Notes

Scope: `src/chat_downloader/sites/twitch/`.

- Keep live IRC, GraphQL, replay, badge, emote, and parser behavior inside this
  package.
- Before parser reshaping, add or promote a raw IRC or GraphQL fixture under
  `tests/fixtures/twitch/`.
- Use `utils/json_types` accessors for incoming Twitch JSON; keep `Any` only
  for transport callables, accumulators, and assembled output.
- Preserve the GraphQL operation-hash guard and badge parser purity.
- Run focused checks after edits:
  `uv run pytest -q tests/test_twitch_*`.

Canonical references:

- [`docs/twitch-integration-guide.md`](../../../../docs/twitch-integration-guide.md)
- [`docs/capability-inventory.md`](../../../../docs/capability-inventory.md)
- [`docs/maintenance-backlog.md`](../../../../docs/maintenance-backlog.md)
