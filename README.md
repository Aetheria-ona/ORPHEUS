# ORPHEUS

[简体中文](README.zh-CN.md) · [Discord](https://discord.gg/tqukqjAk7) · [Rust branch](https://github.com/leafall903/ORPHEUS/tree/rust)

ORPHEUS is an open-source server reimplementation for **Overwatch 1 (retail build 1.74.0.0.104319)** focused on research and preservation.

**GitHub is scheduled to make `rust` the default branch on November 19, 2026 at 12:00 UTC (14:00 Helsinki).** Python development and pull requests continue on `main`. Rust development and pull requests continue on `rust`. [Default branch change](docs/BRANCH_TRANSITION.md).

- **`main` (this branch)** contains the Python reference server. Start here for Practice Range playtests, the Windows launcher, runtime preparation and Python tests. The first playable milestone is still in development.
- **`rust`** contains the experimental native dedicated server. Gameplay verified on the original client with Python is the reference for migration to Rust.

[Quick Start (Local Practice Range)](#local-practice-range-single-player-testing) · [Join a Server](#join-a-server) · [Current Status](#current-status) · [Contributing](CONTRIBUTING.md) · [Open Issues](https://github.com/leafall903/ORPHEUS/issues)

---

## Quick Start

### Local Practice Range (Single-Player Testing)

Requirements on Windows:
1. 64-bit **Python 3.12**
2. An authorized **1.74.0.0.104319** Overwatch installation
3. A prepared `runtime-data/` pack (extracted game catalogs, scripts, descriptors, and `0688.col` map collision)

Put your prepared pack under `runtime-data/` at the repo root (or set `ORPHEUS_RUNTIME_DIR`):

```text
ORPHEUS/
  runtime-data/
    manifest.json
    data/
    bnet/descriptors.pb
    collision/0688.col
```

Check that the pack is complete before launching:

```powershell
.\scripts\START.bat --check-runtime
```

Build the Windows client TLS relay DLL once (from an **x64 Native Tools Command Prompt** with MSVC Build Tools installed):

```bat
.\relay\build.bat
```

Launch the local server and original client:

```powershell
.\scripts\START_LOCAL.cmd --game-exe 'C:\path\to\Overwatch'
```

To run the backend server + web dashboard (`http://127.0.0.1:3725`) without launching a game client (also works on Linux/macOS):

```powershell
.\scripts\START_SERVER_LOCAL.cmd
# or via Python directly:
python -m orpheus --mode server
```

### Join a Server

No local `runtime-data/` or MSVC relay build is needed to join someone else's server in tournament mode:

```powershell
.\scripts\JOIN_SERVER.cmd --server 'HOST:3724' --game-exe 'C:\path\to\Overwatch'
```

For full-menu retail join (`--mode join`), see `python -m orpheus --help`.

### Running Public Tests (No Game Files Required)

The public test suite uses synthetic fixtures only and runs on Windows, Linux, and macOS (clear `ORPHEUS_RUNTIME_DIR` to match CI):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -B -m ruff check . --no-cache
.\.venv\Scripts\python.exe -B -m unittest discover
node --test tests/public/test_dashboard_client.mjs tests/public/test_npc_console_client.mjs
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for running `tests/runtime/` against a local runtime pack and submitting pull requests.

---

## Preparing Runtime Data

If you already have a prepared local research directory, assemble a clean `runtime-data/` pack with:

```powershell
py -3.12 -B tools/prepare_server_runtime.py --source 'C:\path\to\prepared-checkout' --destination .\runtime-data --map 0688
```

See [data/README.md](data/README.md) for the full file checklist, DataTool catalog generation (`tools/prepare_client_catalogs.py`), optional emote/voice tables, and static ray caches.

---

## Current Status

The client column lists observations from earlier 104319 playtests. Verification of the current Python milestone is pending.

| Area | Server Implementation | Original-Client Observations & Open Gaps |
| --- | --- | --- |
| **Login, Profiles & Gallery** | Local Battle.net/lobby emulation, account profiles, persisted keybinds/settings (`22200`–`22204`), cosmetic loadouts, Hero Gallery previews. | Login, hero select, skin switching, and saved settings load cleanly in client 104319. Broader social/competitive rules remain WIP. |
| **Soldier: 76 & Bots** | Body and weapon StateScript VM, rifle firing & reload, Sprint, Helix Rockets, Biotic Field, Practice Range bot patrol, hit detection, and respawn. | Testers damaged and killed training bots. The ten-minute Soldier playtest for v0.1, overhead bot health bars (#7), bot names in the kill feed (#8), and headshot audio (#9) remain open. |
| **Doomfist & Wrecking Ball** | Shared StateScript support for Seismic Slam (wave, placement, landing effects), Rocket Punch dash/view/charge walk, Uppercut reactions/shields, Grappling Claw, Quad Cannons, Piledriver, Adaptive Shield, Minefield. | Rocket Punch movement & charge walk verified in client; Doomfist target damage (#5, #36, #37), Meteor Strike early landing (#38), Slam retest (#6), and gauntlet view anchor (#47) remain open. |
| **Communication Wheel & Voice** | Equipped emotes, 3rd-person camera & angle locks, Mercy staff/weapon selection, equipped voice lines & sprays. | Camera framing and Mercy Relax/Flurry weapon models confirmed in Practice Range. |
| **PvE Entrances** | All 47 Archives and Junkenstein mode cards, Tutorial, and Practice Range loading. | All 47 cards enter and return to menus cleanly on client 104319. Live mission AI and objectives remain open. |

Detailed historical notes and exact test scopes live in [docs/ROADMAP.md](docs/ROADMAP.md), [docs/STATE.md](docs/STATE.md), and [CHANGELOG.md](CHANGELOG.md).

---

## Project Layout

| Path | Purpose |
| --- | --- |
| `src/ow174/bnet`, `src/ow174/lobby` | Battle.net RPC emulator, accounts, profiles, lobby handlers, matchmaking |
| `src/ow174/game`, `src/ow174/game/script` | Encrypted UDP transport, movement/collision, combat, StateScript VM & replication |
| `src/ow174/dashboard`, `src/orpheus` | Local admin dashboard and `orpheus` CLI entry point |
| `relay/` | Windows C++ client relay DLL source |
| `scripts/` | Windows launcher batch/cmd scripts |
| `tools/` | DataTool/CASC extractors and `prepare_server_runtime.py` |
| `fixtures/behavior/` | Language-neutral JSON behavior contracts shared with `rust` |
| `tests/public/`, `tests/runtime/` | CI-safe synthetic tests (`tests/public`) and runtime-backed tests (`tests/runtime`) |

## License & Legal

Code is published under the [MIT license](LICENSE); upstream acknowledgments are in [docs/CREDITS.md](docs/CREDITS.md). ORPHEUS is an unofficial research project. Never commit game binaries, Blizzard assets, signing keys, credentials, or raw packet captures.
