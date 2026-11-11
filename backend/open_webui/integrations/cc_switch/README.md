# CC Switch data hooks 0.1.0

This is the WebUI integration package for a pinned CC Switch data contract. It
does not embed the Rust/Tauri desktop application or claim to be an upstream SDK.

`integration.lock.json` pins CC Switch **v4.0.21**, commit
`c4db63c60d2a1ddcb4bd9f81152b9c6126413659`, SQLite schema **23**. The installed
desktop app was v4.0.21 when this integration was built. Upstream source snapshots
and the upstream MIT license are preserved in `vendor/`. Each source is verified
using its Git blob SHA; the derived usage filter has a SHA-256 checksum. Run:

```sh
backend/.venv/bin/python scripts/integrations/check_cc_switch_pin.py
```

Source provenance relative to the pinned repository:

| Local file in vendor/      | Upstream file                                     |
| -------------------------- | ------------------------------------------------- |
| database-schema.rs         | src-tauri/src/database/schema.rs                  |
| database-module.rs         | src-tauri/src/database/mod.rs                     |
| session-model.rs           | src-tauri/src/session_manager/model.rs            |
| session-provider-module.rs | src-tauri/src/session_manager/providers/mod.rs    |
| claude-sessions.rs         | src-tauri/src/session_manager/providers/claude.rs |
| codex-sessions.rs          | src-tauri/src/session_manager/providers/codex.rs  |
| usage-rollup.rs            | src-tauri/src/database/dao/usage_rollup.rs        |
| usage-statistics.rs        | src-tauri/src/services/usage_stats.rs             |
| sql-helpers.rs             | src-tauri/src/services/sql_helpers.rs             |
| LICENSE                    | LICENSE                                           |

The Python adapter reads the existing `~/.cc-switch/cc-switch.db` through SQLite
`mode=ro`. It never creates/migrates that database, changes the active Provider,
or writes native configuration. Unsupported schema versions disable this source
and report the incompatibility. Project bindings, role Profiles, board stages,
topology presets and runs live in the existing WebUI Folder/Chat records.

Hooks are exposed through the project data router and the execution bridge:
`catalog.read`, `project.bind`, `session.list`, `session.read`, `usage.read`,
`turn.prepare`, `turn.observed`. They are internal contracts, not a public
arbitrary-code plugin loader. See `docs/cc-switch-project-hooks.md` for APIs and
the supported execution boundaries.

The effective usage SQL is derived from the pinned `effective_usage_log_filter`
and `sql_helpers` definitions: successful matching proxy requests suppress
duplicate native-log rows. Input token semantics are normalized to fresh input
plus separate cache read/write counters. Historical daily rollups lack Project
identity and are deliberately excluded from Project totals. Unknown cost is
reported as unknown. No secret-bearing MCP configuration is returned.

An upgrade requires updating the version, commit, schema and source checksums
together, reviewing the adapters and SQL, then running the compatibility tests.
Do not merely relax the schema check.
