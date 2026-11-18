# Runtime Data Guide

ORPHEUS public tests run from synthetic fixtures with no game files. Starting the game server for real clients or running `tests/runtime/` requires a local **Overwatch 1.74.0.0.104319** runtime pack (`runtime-data/` or `ORPHEUS_RUNTIME_DIR`).

Keep all extracted game files, XML dumps, keys, and generated caches out of Git.

---

## Layout & Preflight

Place `data/`, `bnet/`, and `collision/` directly inside `runtime-data/` at the repository root (or point `ORPHEUS_RUNTIME_DIR` to the pack folder):

```powershell
.\scripts\START.bat --check-runtime
```

This checks all required startup files and validates the 64-byte `lobby_state_blob.key`.

---

## Preparing a Pack

### 1. Generate Client Catalogs (from your local 104319 install)

Build DataTool from the pinned [OWLib overwatch/1.74](https://github.com/overtools/OWLib/tree/7afe5f52706e7002ed8c2717299cd60846f3fa3a) revision using the x64 .NET 6 SDK:

```powershell
git clone --branch overwatch/1.74 https://github.com/overtools/OWLib.git owlib-174
git -C owlib-174 switch --detach 7afe5f52706e7002ed8c2717299cd60846f3fa3a
git -C owlib-174 submodule update --init --recursive
dotnet build .\owlib-174\DataTool\DataTool.csproj -c Release
```

Export the four base catalogs (`extracted_heroes.json`, `extracted_maps.json`, `extracted_items.json`, `extracted_general_unlocks.json`):

```powershell
$game = 'C:\path\to\Overwatch'
$dataTool = 'C:\path\to\owlib-174\DataTool\bin\Release\net6.0\DataTool.exe'
$catalogs = 'C:\path\to\local\orpheus-catalogs'
py -3.12 -B tools\prepare_client_catalogs.py --game $game --data-tool $dataTool --destination $catalogs
```

### 2. Assemble `runtime-data/`

From an existing prepared checkout:

```powershell
py -3.12 -B tools\prepare_server_runtime.py --source 'C:\path\to\prepared-checkout' --destination .\runtime-data --map 0688
```

To fill missing generated files from your local Overwatch install and OWLib XML/CASC while packing (keeping outputs outside the repo until packed):

```powershell
py -3.12 -B tools\prepare_server_runtime.py --source 'C:\path\to\prepared-source' --destination .\runtime-data --map 0688 `
    --game 'C:\path\to\Overwatch' --data-tool 'C:\path\to\owlib-174\DataTool\bin\Release\net6.0\DataTool.exe' --owlib 'C:\path\to\owlib-174'
```

Add `--catalogs $catalogs` to inject the catalogs generated in step 1, or `--client-xml <dir>` to reuse existing XML dumps.

---

## Optional & Extended Runtime Tables

### StateScript Queries and Event Tests

Place `statescript_names_174.json` in the selected pack's `data/` directory to run `tools/ssq.py` and `tests/runtime/test_ssq.py`. The query tool also reads `statescript_codecs_174.json` there when available. Its index cache stays under the checkout's `cache/`.

The remix-selector test in `tests/runtime/test_events.py` reads `data/extracted_events_174.json` from the same pack. The names and event tables are optional for server startup. Add them locally when running the corresponding tools or tests.

### Emote Aliases, Blend Trees & Animation Durations

To enable equipped emotes in the communication wheel, add `emote_aliases_174.json`, `emote_blend_trees_174.json`, `emote_tagged_174.json`, and `emote_durations_174.json` (plus optional `emote_clips_174.json` and `emote_scalar_tracks_174.json`) to `data/`:
- `tools/extract_emote_aliases.py` reads DataTool `.0A5` XML (pass `0407.003` and `040A.003` via `--body-definitions` for D.Va).
- `tools/extract_emote_durations.py` reads `.021` XML with `--categories` pointing to `.015` XML (`--clips-output` and `--scalar-output` write the clip/track tables).

### Equipped Player Voice Lines

To resolve equipped voice lines and localized callouts, generate `player_voice_174.json` and `voice_effects_174.json` using [tools/extract_player_voice.cs](../tools/extract_player_voice.cs) as a .NET 10 console program referencing DataTool, TankLib, and TACTLib (`extract_player_voice.exe <OverwatchDir> <runtime/data>`).

### Expanded Static Ray Geometry (`*.rays.col`)

For sightlines, hitscan props, and spray surfaces on specific maps (e.g., Practice Range `0688`, Junkenstein `0CD3`), build `.rays.col` caches next to the `.col` movement caches:

```powershell
.\.venv\Scripts\python.exe tools\build_collision.py 0688 0CD3 --ray-geometry
```

For unindexed client-made map props, export root model CASC records via `tools/export_casc_records.ps1` and pass `--prop-records <json>` to `build_collision.py`.

### Custom Entity & Mode Bundles (`--entity`, `--mode-xml`)

When step 2 generates a graph bundle from XML, the packer reads the selected maps' spawner table and includes the bodies and scripts used by their NPCs. An existing source bundle is copied as supplied. To refresh it, generate a replacement outside the repository and pass it to the packer with `--game-graphs`. The following example includes NPCs from a prepared spawner table and mode scripts. Use `--entity <index>` for additional entities absent from those spawns.

```powershell
.\.venv\Scripts\python.exe -X utf8 tools\extract_game_graphs.py --xml 'C:\path\to\xml' --owlib 'C:\path\to\owlib' `
    --ai-spawners 'C:\path\to\runtime\data\ai_spawners_174.json' `
    --mode-xml 'C:\path\to\xml\0C5' --output 'C:\path\to\entity-graphs.json.gz'

.\.venv\Scripts\python.exe tools\prepare_server_runtime.py --source 'C:\path\to\source' --destination 'C:\path\to\runtime' `
    --map 0688 --game-graphs 'C:\path\to\entity-graphs.json.gz'
```

Additional local-only extractors:
- `tools/extract_raycast_metadata.py` (`097`/`098` XML $\rightarrow$ `raycast-metadata.json`)
- `tools/extract_game_stats.py --score-events` (`003F.054` XML $\rightarrow$ score event metadata)

---

## File Reference

| File | Role & Source |
| --- | --- |
| `extracted_heroes.json`, `extracted_maps.json`, `extracted_items.json`, `extracted_general_unlocks.json` | Game catalogs from `tools/prepare_client_catalogs.py` |
| `arcade_cards_174.json`, `game_heroes_174.json`, `game_movers_174.json`, `game_stats_174.json` | Gameplay tables extracted from XML (`tools/extract_*.py`) |
| `game_graphs_174.json.gz`, `game_maps_174.json`, `game_queues_174.json`, `ai_spawners_174.json` | StateScript graph bundle, map spawns, queue cards, bot routes (`tools/extract_*.py`) |
| `collision_keys_174.json`, `collision_props_174.json`, `collision/0688.col` | Collision chunk indexes (`export_casc_records.ps1`) and map cache (`tools/build_collision.py`) |
| `schemas_174.json`, `game_schemas_174.json`, `statescript_codecs_174.json` | Protocol and StateScript codec layouts (`tools/dump_schemas.py`, `tools/extract_statescript_codecs.py`) |
| `retail_templates.json`, `bnet/descriptors.pb`, `resource_keys_174.json`, `lobby_state_blob.key` | Prepared retail lobby templates, Battle.net protobuf descriptors, content keys, and 64-byte lobby signing key (must be supplied from an authorized prepared source) |
| `game_ult_charge_174.json` | Optional override for built-in community ultimate charge estimates (`src/ow174/game/ult_charge.py`) |
