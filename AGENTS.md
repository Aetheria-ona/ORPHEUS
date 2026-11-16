# ORPHEUS Development Guidelines

- **Target build:** Overwatch 1 retail `1.74.0.0.104319`. Prefer evidence pinned to `104319` and shared mechanisms across heroes and maps.
- **Branches:**
  - `main` holds the Python reference server, launchers, data extractors, and public/runtime suites.
  - `leafall` hosts hero-ability work and syncs to `main` via PRs.
  - `rust` holds the native Rust server (use a separate worktree for cross-language comparisons).
- **Automated work vs. manual client runs:** Code, tools, tests, and docs run headlessly. Leave game client startup and live UI/gameplay acceptance to manual sessions unless explicitly requested.
- **Behavior contracts:** Whenever server behavior changes in Python, add or update a synthetic JSON fixture under `fixtures/behavior/` and verify it through `tests/public/` so `rust` CI checks parity ([docs/BEHAVIOR_CONTRACTS.md](docs/BEHAVIOR_CONTRACTS.md)).
- **Checklists & commits:** Keep commits focused, use Conventional Commits (`fix: ...`, adding `Assisted-by:` trailers when AI-assisted), update `CHANGELOG.md` for user-visible fixes, and mark items in [docs/ROADMAP.md](docs/ROADMAP.md) complete only with repeatable evidence. See [CONTRIBUTING.md](CONTRIBUTING.md) for local lint and test commands.
- **Publication hygiene:** Never commit game installations, retail binaries, asset dumps, signing keys, credentials, profiles, raw packet captures, or local logs. Keep upstream license notices in [LICENSE](LICENSE) and attributions in [docs/CREDITS.md](docs/CREDITS.md).
- **Protected branches:** Push reviewed work to topic branches and merge into `main` or `rust` through PRs with passing CI.
