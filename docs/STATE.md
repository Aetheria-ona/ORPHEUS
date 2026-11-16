# Verification & Implementation State (`main`)

This summarizes what is verified on the Python server (`main`) against **Overwatch 1.74.0.0.104319**, separating automated headless tests from live original-client results. See [CHANGELOG.md](../CHANGELOG.md) for commit-by-commit details and [ROADMAP.md](ROADMAP.md) for open acceptance goals.

---

## Live Original-Client Observations (1.74.0.0.104319)

| Area | What Works in Live Client | Open / Failing in Live Client |
| --- | --- | --- |
| **Launcher & Login** | `_retail_\Overwatch.exe` logs in through the local Battle.net/TLS relay without official Battle.net, loads Practice Range (`0688`), switches heroes, and loads/saves settings (`+0x130`). | Broader multi-player internet latency and full competitive flow unverified. |
| **PvE & Tutorial Entrances** | All 47 Archives/Junkenstein queue cards (Uprising, Retribution, Storm Rising, Junkenstein, Challenges), Tutorial, and Practice Range enter hero select/world and return cleanly to menus (verified on 104319). | Live mission objectives, enemy AI, and challenge modifiers remain open. |
| **Practice Range Combat** | Shooting kills training bots; damage (`25001`), eliminations (`25005`), bot death, and respawn events are sent and observed. | Overhead bot health bars (#7), bot names in kill feed/banner (#8), headshot SFX (#9), multikill audio (#10), and Practice Range boundary death (#13) remain open. |
| **Doomfist (Practice Range)** | Rocket Punch charges, dashes (`~6.5 m` min / `~20 m` full), stops at walls, keeps direction while turning view, and walks while charging (`2.7–2.8 m/s`); view stays anchored above floor. | Target damage/knockback (#36), Hand Cannon bot damage & shots 2–4 audio (#5), Uppercut bot knockup (#37), Meteor Strike early landing (#38), Slam live retest (#6), and 1P gauntlet offset (#47). |
| **Communication Wheel** | Mercy「放松」and「权舞风起」display correct weapon models and 3P camera framing; equipped voice lines (`Hello`, `Thanks`, `Need Healing`, `7E05`) play. | Displacement-driven emotes (Doomfist「有胆就来」, Bastion「行尸走械」, Mei「僵尸跳」). |

---

## Automated Server & Protocol Coverage

- **Canonical runtime pipeline (`src/ow174/game/`):** `script/driver.py`, `script/runtime.py`, `script/nodes.py`, `script/expr.py`, `mover.py`, `combat.py`, and `collision.py` handle collision-backed movement, GJK/contact sweeps, hitscan/projectile lifetimes, Vitals health pools, StateScript execution, and snapshot delta encoding.
  *(Older prototype harnesses under `script/research_runtime.py`, `script/body.py`, `script/replication.py`, `game/simulation.py`, `game/flat_model.py`, `game/range_model.py` are kept only for standalone isolated model tests.)*
- **Collision & map caches:** Format-5 `.col` caches generated and checksum-verified across all 95 catalog maps (`0688` Practice Range, King's Row, Kanezaka, Rialto, etc.); optional `.rays.col` caches support hitscan props and spray surfaces.
- **Fault safety & packet budget:** StateScript event/plug cycles raise explicit `ScriptBudgetError` and isolate failing body scripts without stalling other players; packet-budget selection commits ACK tokens only after send (`1264`-byte limit; see [PACKET_BUDGET.txt](PACKET_BUDGET.txt)); uint32 command rollover handling is covered.
- **Determinism, journals & replays:**
  - `CONTROLLED_SERVER_JOURNAL_V2` ([CONTROLLED_SERVER_JOURNAL_V2.txt](CONTROLLED_SERVER_JOURNAL_V2.txt)) & `SERVER_STORED_STATE_V1` ([SERVER_STORED_STATE_V1.txt](SERVER_STORED_STATE_V1.txt)): replay encrypted ingress through `GameServer` and hash VM, mover, GJK, combat, and transport state across ticks.
  - `NATIVE_BODY_REPLAY_V2` ([NATIVE_BODY_REPLAY_V2.txt](NATIVE_BODY_REPLAY_V2.txt)): records all 11 command fields, state allocation order, and `movelog` windows.
  - Loopback UDP lifecycle suites cover 10x Soldier/Reaper swaps, 10x leave/rejoins, packet loss, and ACK retries.
- **Public & cross-platform CI (`tests/public/`):** Runs on both Windows (`source-tests`) and Ubuntu Linux (`source-tests-linux`) with `ORPHEUS_RUNTIME_DIR=""`, covering Ruff lint, 954 synthetic Python unit tests, dashboard/console Node tests, and `tests/runtime/test_launcher_inject.py`.

---

## Remaining Architecture / Transport Gaps

- **Server-owned bug ([#12](https://github.com/leafall903/ORPHEUS/issues/12)):** Reliable combat notices already in the resend queue can still target a numeric body ID recycled after 255 hero switches before the notice is ACKed.
- **Draft PR [#32](https://github.com/leafall903/ORPHEUS/pull/32):** Full 32-hero Python integration candidate (`codex/python-hero-upstream-20261113`) needs conflict reconciliation and CI cleanup before any merge to `main`.
- **Cross-language test parity ([#61](https://github.com/leafall903/ORPHEUS/issues/61)):** 19 StateScript behavior contracts currently living on `rust` remain to be ported back into `main`'s `tests/public/`.
