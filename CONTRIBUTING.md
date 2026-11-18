# Contributing to ORPHEUS

Community contributions are welcome across code, protocol research, documentation, and original-client testing. Check [docs/ROADMAP.md](docs/ROADMAP.md) and [open issues](https://github.com/leafall903/ORPHEUS/issues) to find an item whose prerequisites match what you have locally.

GitHub is scheduled to open new pull requests against `rust` by default from November 19, 2026 at 12:00 UTC. When submitting Python work, choose `main` in the **base** selector. Both branches accept normal pull requests. [Default branch change](docs/BRANCH_TRANSITION.md).

- **`main`:** Python server, Windows launcher, data extraction tools, and shared behavior tests.
- **`rust`:** Native Rust server ([Rust branch](https://github.com/leafall903/ORPHEUS/tree/rust)).

---

## Development Setup

Install **Git**, **Python 3.12** (64-bit on Windows), and **Node.js 24** (for dashboard JS tests):

```powershell
git clone https://github.com/leafall903/ORPHEUS.git
cd ORPHEUS
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

On Linux or macOS, replace `py -3.12` / `.\.venv\Scripts\python.exe` with `python3.12` and `.venv/bin/python`. Installing in editable mode registers both `orpheus` and `python -m orpheus`.

---

## Running Checks

### Public Source Checks (CI Gate — Runs on Windows & Linux)

Clear `ORPHEUS_RUNTIME_DIR` so public discovery only runs synthetic tests in `tests/public/`:

```powershell
Remove-Item Env:ORPHEUS_RUNTIME_DIR -ErrorAction SilentlyContinue
$python = '.\.venv\Scripts\python.exe'
& $python -B -m ruff check . --no-cache
& $python -B -m unittest discover
& $python -B -m unittest tests/runtime/test_launcher_inject.py -v
node --test tests/public/test_dashboard_client.mjs tests/public/test_npc_console_client.mjs
git diff --check
```

`tests/public/` is for CI-safe tests using synthetic inputs. Put any test that depends on prepared 104319 game data in `tests/runtime/`.

### Prepared Runtime Tests

If you have a local `1.74.0.0.104319` runtime pack, point `ORPHEUS_RUNTIME_DIR` to it and run individual suites in `tests/runtime/` directly from the repository root:

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\orpheus-runtime'
.\scripts\START.bat --check-runtime
.\.venv\Scripts\python.exe -B -m unittest tests/runtime/test_game_projectiles.py -v
```

### Original-Client Testing

1. Build `relay/owwfd_relay.dll` once from an x64 Native Tools Command Prompt (`.\relay\build.bat`), or use an existing local build.
2. Check your pack and start the local game:
   ```powershell
   $env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\orpheus-runtime'
   .\scripts\START.bat --check-runtime
   .\scripts\START_LOCAL.cmd --game-exe 'C:\path\to\Overwatch'
   ```
3. Test one behavior at a time and report findings with commit SHA, build (`104319`), locale, exact inputs, and expected vs. actual behavior using the [gameplay verification template](https://github.com/leafall903/ORPHEUS/issues/new?template=gameplay-verification.yml).

---

## Shared Behavior Contracts

Whenever you fix or change server behavior in Python, add or update a small language-neutral JSON fixture under `fixtures/behavior/` and test it through `tests/public/` so `rust` can verify the same behavior in CI. See [docs/BEHAVIOR_CONTRACTS.md](docs/BEHAVIOR_CONTRACTS.md) for the schema and workflow.

---

## Pull Requests & Security

- Open PRs from a focused topic branch on your fork targeting `main` (for Python/tooling) or `rust` (for native Rust changes).
- Use [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) (`fix: ...`, `feat: ...`, `test: ...`) and add a brief entry to `CHANGELOG.md` for user-visible or behavioral changes.
- AI-assisted PRs (Codex, Claude Code, etc.) are welcome—disclose tool assistance in the PR description or with an `Assisted-by:` trailer, and review every diff and test output yourself before submitting.
- **Never commit** game binaries, Blizzard assets, extracted XML/dumps, signing keys, credentials, or personal paths/logs.
