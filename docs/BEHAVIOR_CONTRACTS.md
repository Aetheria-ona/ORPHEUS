# Shared Behavior Contracts

ORPHEUS uses small, language-neutral JSON fixtures under `fixtures/behavior/` so the **Python** server (`main`) and the **Rust** server (`rust`) stay aligned on byte-level protocol encoding, VM rules, and gameplay mechanics without sharing source files or shipping game assets.

---

## How It Works

1. Any Python fix or feature that changes wire bytes, state-machine lifecycle, or query rules should include a minimal synthetic JSON contract under `fixtures/behavior/<name>-v1.json`.
2. Both Python and Rust run their real production code paths against the same fixture file:
   - Python tests read `ORPHEUS_BEHAVIOR_FIXTURES` (defaulting to `fixtures/behavior/`).
   - `rust`'s `shared-behavior-contracts` CI job checks out `main` alongside `rust` and runs `main`'s Python consumers against `rust`'s `fixtures/behavior/` directory so neither side can drift silently.
3. Public fixtures must contain synthetic or approved inputs only—no copyrighted client assets, private keys, or full packet captures.

### Standard JSON Envelope

| Field | Meaning |
| --- | --- |
| `schema_version` | Schema integer version (currently `1`) |
| `case_id` | Stable semantic contract identifier |
| `target_build` | Overwatch build (`104319`) |
| `source` | Pinned commit/revision and code paths that generated the expected outputs |
| `evidence` | Provenance category (`synthetic-reference`, `synthetic-strict-host-reference`, etc.) |
| `client_acceptance` | Original-client observation status (`pending` or recorded session notes) |
| `input` | Explicit inputs (byte arrays encoded as hex strings) |
| `expected` | Expected output bytes, events, errors, or state snapshots |

---

## Contracts on `main` (10 Fixtures)

### Public CI Contracts (No Runtime Data Required)

| Fixture | Scope & Behavior | Python Test | Rust Status |
| --- | --- | --- | --- |
| [jam-framing-v1.json](../fixtures/behavior/jam-framing-v1.json) | JAM stream framing encryption/decryption (`00..1f` key, all split boundaries & 1-byte delivery) | `tests.public.test_behavior_contracts` | Covered in CI (`orpheus-wire`) |
| [world-queries-v1.json](../fixtures/behavior/world-queries-v1.json) | Strict VM World queries (`origin`, `position`, foreign vars, missing/null vs. unavailable host services) | `tests.public.test_world_query_contracts` | Covered in CI (`--test world_queries`) |
| [profile-settings-v1.json](../fixtures/behavior/profile-settings-v1.json) | Client settings persistence (`22200`–`22204` $\rightarrow$ `+0x130` signed 32-bit keys / 64-bit values $\rightarrow$ `20802`) | `tests.public.test_profile_settings_contracts` | Covered in CI (`--test profile_settings`) |
| [seismic-slam-wave-v1.json](../fixtures/behavior/seismic-slam-wave-v1.json) | Seismic Slam immediate wave reach sampling, target deduplication & child state delivery | `tests.public.test_behavior_contracts` | Mirrored on `rust`; Rust consumer tracked in #61 |
| [seismic-slam-placement-v1.json](../fixtures/behavior/seismic-slam-placement-v1.json) | Seismic Slam airborne selector: idle, miss retains prior point, success publishes before MoveTo | `tests.public.test_behavior_contracts` | Mirrored on `rust`; Rust consumer tracked in #61 |
| [body-short-action-retry-v1.json](../fixtures/behavior/body-short-action-retry-v1.json) | Owner & observer short frame-action encoding, retry with original stamp across loss, and ACK retirement | `tests.public.test_behavior_contracts` | Mirrored on `rust`; Rust consumer tracked in #61 |
| [server-only-movement-v1.json](../fixtures/behavior/server-only-movement-v1.json) | Channel-2 direction mod throttles (`0D06` Rocket Punch dash) & owner record window (+250 ms tail) | `tests.public.test_behavior_contracts` | Mirrored on `rust`; Rust consumer tracked in #61 |

Run all public contracts locally without runtime data:

```powershell
python -S -B -m unittest tests.public.test_behavior_contracts tests.public.test_world_query_contracts tests.public.test_profile_settings_contracts -v
```

### Runtime-Backed Contracts (Require Local 104319 Runtime Pack)

| Fixture | Scope & Behavior | Python Runtime Consumer |
| --- | --- | --- |
| [wheel-emote-presentation-v1.json](../fixtures/behavior/wheel-emote-presentation-v1.json) | Equipped emote controller slots, owner `023A` resource/camera framing, and angle-lock lifecycle | `tests/runtime/test_communication_wheel.py` |
| [wheel-emote-weapon-v1.json](../fixtures/behavior/wheel-emote-weapon-v1.json) | Mercy staff/pistol selection across Relax (intro/loop/exit), Flurry, and Caduceus | `tests/runtime/test_communication_wheel.py` |
| [player-voice-v1.json](../fixtures/behavior/player-voice-v1.json) | Equipped hero voice-line lookup and friendly vs. hostile listener delivery | `tests/runtime/test_game_voicelines.py` |
