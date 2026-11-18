# Contributing to ORPHEUS

Community contributions are welcome across code, protocol research, documentation, and original-client testing. Check [docs/ROADMAP.md](docs/ROADMAP.md) and [open issues](https://github.com/leafall903/ORPHEUS/issues) to find an item whose prerequisites match what you have locally.

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

From the repository root, point the suite runner at a prepared `1.74.0.0.104319` pack. It checks the pack with `python -m orpheus --check-runtime` before discovering `tests/runtime/`.

```powershell
$env:ORPHEUS_RUNTIME_DIR = 'C:\path\to\orpheus-runtime'
.\.venv\Scripts\python.exe -B scripts/run_runtime_tests.py
.\.venv\Scripts\python.exe -B scripts/run_runtime_tests.py --pattern test_game_projectiles.py -k projectile
```

`--runtime-dir DIR` overrides the environment variable. `--pattern` selects module filenames and repeatable `-k` filters test names as in unittest. The console shows counts, elapsed time, the SHA-256 of the manifest and relevant test IDs. The complete preflight and test output stays in a new private log under `logs/`. Use `--log PATH` to specify a local file in an existing private directory. Keep this log on the machine that ran the suite.

To track regressions, run the **whole suite** with `--write-baseline candidate.txt`, review the failed and errored test IDs, and use the reviewed file with `--baseline candidate.txt` on later runs. Each line accepts a test ID, an optional platform qualifier such as `[linux-arm64]`, and an optional `# comment`. Without a qualifier the entry applies on every platform. Generating a baseline still reports new failures. Writing a baseline from a subset includes only that subset.

Exit code `0` means the selected tests finished within the baseline and skip limit. Exit code `1` indicates new failures, errors, unexpected successes, or more than `--max-skips N` skipped cases. Exit code `2` indicates a setup or runner error, including empty selection. Each failed subtest is attributed to its test method. Only a positive pass in this run qualifies a baseline entry for removal.

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
