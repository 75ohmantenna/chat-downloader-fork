# Output target selection

## Outcome

Output configuration and dispatch share the rules for expanding filenames,
selecting aliases, and reserving generated paths. Manifest and verification
code can observe a path before a lazy writer opens it.

See the [implementation](../src/chat_downloader/output/targets.py) and the
[design decision](maintenance-decisions.md#compare-output-destinations-after-expansion).
