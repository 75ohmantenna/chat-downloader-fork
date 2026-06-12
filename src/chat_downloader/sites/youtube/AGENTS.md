# YouTube Site Notes

Scope: `src/chat_downloader/sites/youtube/`.

- Keep YouTube bootstrap, InnerTube request, continuation, discovery, and
  parser behavior inside this package.
- Before parser reshaping, add or promote a raw fixture under
  `tests/fixtures/youtube/`.
- Use `utils/json_types` accessors for incoming YouTube JSON; keep `Any` only
  for assembled heterogeneous output.
- Preserve request-profile fallback and continuation recovery behavior unless a
  focused regression test documents the change.
- Run focused checks after edits:
  `uv run pytest -q tests/test_youtube_* tests/test_offline_error_fixtures_unit.py`.

Canonical references:

- [`docs/youtube-integration-guide.md`](../../../../docs/youtube-integration-guide.md)
- [`docs/capability-inventory.md`](../../../../docs/capability-inventory.md)
- [`docs/maintenance-backlog.md`](../../../../docs/maintenance-backlog.md)
