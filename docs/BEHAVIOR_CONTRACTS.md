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

## Contracts on `main`

The tables list selected fixtures. The [fixture directory](../fixtures/behavior/) contains the full set. Python also checks shared StateScript behavior in the [VM](../tests/public/test_statescript_vm_contracts.py), [entity](../tests/public/test_statescript_entity_contracts.py) and [combat](../tests/public/test_statescript_combat_contracts.py) contract suites.

### Public CI Contracts (No Runtime Data Required)

| Fixture | Scope & Behavior | Python Test | Rust Status |
| --- | --- | --- | --- |
| [jam-framing-v1.json](../fixtures/behavior/jam-framing-v1.json) | JAM stream framing encryption/decryption (`00..1f` key, all split boundaries & 1-byte delivery) | `tests.public.test_behavior_contracts` | Covered in CI (`orpheus-wire`) |
| [world-queries-v1.json](../fixtures/behavior/world-queries-v1.json) | Strict VM World queries (`origin`, `position`, foreign vars, missing/null vs. unavailable host services) | `tests.public.test_world_query_contracts` | Covered in CI (`--test world_queries`) |
| [profile-settings-v1.json](../fixtures/behavior/profile-settings-v1.json) | Client settings persistence (`22200`–`22204` $\rightarrow$ `+0x130` signed 32-bit keys / 64-bit values $\rightarrow$ `20802`) | `tests.public.test_profile_settings_contracts` | Covered in CI (`--test profile_settings`) |
| [seismic-slam-wave-v1.json](../fixtures/behavior/seismic-slam-wave-v1.json) | Seismic Slam immediate wave reach sampling, target deduplication & child state delivery | `tests.public.test_behavior_contracts` | Covered in `rust/tests/seismic_slam.rs` |
| [seismic-slam-placement-v1.json](../fixtures/behavior/seismic-slam-placement-v1.json) | Seismic Slam airborne selector: idle, miss retains prior point, success publishes before MoveTo | `tests.public.test_behavior_contracts` | Covered in `rust/tests/seismic_slam.rs` |
| [body-short-action-retry-v1.json](../fixtures/behavior/body-short-action-retry-v1.json) | Owner & observer short frame-action encoding, retry with original stamp across loss, and ACK retirement | `tests.public.test_behavior_contracts` | Covered in `rust/tests/body_short_action_retry.rs` |
| [server-only-movement-v1.json](../fixtures/behavior/server-only-movement-v1.json) | Channel-2 direction mod throttles (`0D06` Rocket Punch dash) & owner record window (+250 ms tail) | `tests.public.test_behavior_contracts` | Covered in `rust/tests/server_only_movement.rs` |
| [emote-weapon-selection-v1.json](../fixtures/behavior/emote-weapon-selection-v1.json) | Weapon manager selects an available slot for emotes and keeps the equipped slot on normal branch re-entry | `tests.public.test_behavior_contracts` | Pending Rust coverage |
| [wheel-spray-pose-v1.json](../fixtures/behavior/wheel-spray-pose-v1.json) | Confirmed spray command pose and eye spring offset across retries and fallback | `tests.public.test_wheel_spray_pose_contract` | Pending Rust coverage |
| [spawn-sector-v1.json](../fixtures/behavior/spawn-sector-v1.json) | Reconstructed horizontal spawn sector, projected center, zero-axis fallback and independent wall/floor rejection | `tests.public.test_spawn_sector_contract` | Pending Rust coverage |
| [patrol-victim-velocity-v1.json](../fixtures/behavior/patrol-victim-velocity-v1.json) | Floor-following patrol stops for the next tick on a live victim PlayScript channel-0 velocity reset | `tests.public.test_patrol_victim_velocity` | Pending Rust coverage |

The spawn sampler starts at the floor below the requested center and interprets its half-angle in the horizontal request frame. A direction with no horizontal component uses +X. Its first angular probe is at the negative edge, which the reconstruction includes when the path is supported and clear. The fixture exercises this geometry directly. The authored script path normalizes the direction and rounds the full degree angle to a float32 half-angle in radians, so an authored 90-degree request and a direct binary64 `pi / 4` request have different numeric boundaries. Retail candidate coordinates remain unverified.

Run the public contract test modules locally without runtime data:

```powershell
python -B -m unittest discover -s tests/public -p "*contract*.py" -v
```

### Runtime-Backed Contracts (Require Local 104319 Runtime Pack)

| Fixture | Scope & Behavior | Python Runtime Consumer |
| --- | --- | --- |
| [wheel-emote-presentation-v1.json](../fixtures/behavior/wheel-emote-presentation-v1.json) | Equipped emote controller slots, owner `023A` resource/camera framing, and angle-lock lifecycle | `tests/runtime/test_communication_wheel.py` |
| [wheel-emote-weapon-v1.json](../fixtures/behavior/wheel-emote-weapon-v1.json) | Mercy keeps her staff during and after Relax, Flurry and Caduceus in Practice Range and solo Hybrid, followed by a manual switch | `tests/runtime/test_communication_wheel.py` |
| [player-voice-v1.json](../fixtures/behavior/player-voice-v1.json) | Equipped hero voice-line lookup and friendly vs. hostile listener delivery | `tests/runtime/test_game_voicelines.py` |
| [wheel-spray-possession-v1.json](../fixtures/behavior/wheel-spray-possession-v1.json) | Equipped spray slots and empty slots on hero switch | `tests/runtime/test_communication_wheel.py` |
