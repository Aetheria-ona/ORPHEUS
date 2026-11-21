"""Make data/game_maps_174.json: per map, the placeables the server must create and the spawn points.

Inputs (all from an Overwatch 1.74 install through DataTool, OWLib):

    DataTool.exe <game> extract-stu-type <dump> --xml          (003, 09F, 0C5 and strings.json)
    DataTool.exe <game> extract-stu-type <raw> 0BC             (raw map chunks)
    DataTool.exe <game> dump-map-entities <ents> <09F index>... (entity instance data as XML)

    py tools/extract_game_maps.py --stu <dump> --raw <raw> --ents <ents>

Pass --models with the local raw 00C models to add cart and spawn-room collision.

Rules, from the client (image base 0x7FF788F10000):
- Map placeables get one flat index over the placeable types whose flag is set in the type table
  0x7FF78BD36CE0 (builder 0x7FF78AA52900): light, area, entity, ... in that order. So the first
  entity has index count(light) + count(area).
- While the map loads, every entity placeable whose definition (003) has component bit 58
  (STU 0x737C5C41, spawn point) or bit 94 (STU 0xCA1FB0B8) goes into the pending list
  (functor 0x7FF78969B9A0), with the placeable's first identifier when it is not zero. The loading
  screen ends when the list is empty. The server empties it with a ch4 "from map" create of each
  index (entity id 0x80000000 | index).
- Each tick the client drops entries whose identifier is not in the game mode's m_A43573F4
  (0x7FF78969EFD0). Entries without an identifier always stay.
- Objective modes also create modeled, server-owned entities that pass the placement filter.
- Assault, Hybrid and Control link capture points to authored AREA boxes by UUID.
- Spawn points are picked by target tag. The mode graphs give team 0 the tags 0x310 / 0x30D / 0x3CE
  and team 1 the tags 0x341 / 0x3CC / 0x3CD (control stages 0 / 1 / 2). Assault, escort and hybrid
  take the pair from the current objective: its var 10549 (0x2935) is the defenders' room and var
  10550 (0x2936) the attackers' room. Team 1 attacks first; sides swap between rounds.
"""

import argparse
import json
import math
import re
import struct
import xml.etree.ElementTree as ET
from itertools import pairwise
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "game_maps_174.json"
NS = "{https://yretenai.com/dragonml/v1}"
SPAWN = 0x737C5C41  # component bit 58
REQUIRED = 0xCA1FB0B8  # component bit 94
MODEL = 0x191835F9
LIGHT, AREA, ENTITY = 7, 8, 9  # placeable types, byte 19 of each placeable
TEAM_TAGS = {0x310: 0, 0x30D: 0, 0x3CE: 0, 0x341: 1, 0x3CC: 1, 0x3CD: 1}
CONTROL_STAGES = {0x310: 0, 0x341: 0, 0x30D: 1, 0x3CC: 1, 0x3CE: 2, 0x3CD: 2}
CONTROL_POINT_TAGS = {0x41B: 0, 0x41C: 1, 0x41D: 2}
FFA_TAG = 0x413
OBJECTIVE_GRAPH = 0xF4C  # the objective graph that holds the spawn tags of assault/escort/hybrid
POINT_GRAPH = 0xE8E  # capture point graph (its var 0x1DDB is the point name)
POINT_OUTLINE_GRAPH = 0xB78
PAYLOAD_GRAPH = 0x1225  # the payload's own graph
PAYLOAD_WAYPOINT_GRAPH = 0x121E
PAYLOAD_GATE_IMPACT_GRAPH = 0x0C78
GATE_DEBRIS_GRAPH = 0x19FA
GATE_DEBRIS_PROP_GRAPH = 0x0E41
ROOM_BARRIER_GRAPH = 0x0FA8
ROOM_VISIBILITY_GRAPH = 0x0D73
ROOM_HEALING_GRAPH = 0x0D62
ROOM_RESTRICTED_GRAPH = 0x0E8B
SPAWN_DOOR_GRAPH = 0x0D32
# 01BE signs without room tags inside the third attack spawn.
EICHENWALDE_ROOM_SIGNS = {(0x068D, 651): 0x03CD, (0x07F4, 774): 0x03CD}
ROOM_BARRIER_TEAMS = {0x1C90: 0, 0x1C91: 1}  # the mover categories of models 09A0 and 099D
CONTROL_ROOM_COLLISION = {0x1831: 0, 0x1832: 1}  # 0D73 variants of the same team-filtered models
# 104319 healing rims (24DC.008). Use their dimensions to fit the 035A.008 enemy panel.
CONTROL_ROOM_RIMS = {0x1D13: (2.7946, 2.8287), 0x1D14: (3.7668, 3.3940)}
ROOM_PANEL_SIZE = 4.0  # 09A0/099D are two-sided 4 x 4 m panels before placement scaling.
CONTROL_ROOM_TEAMS = {"TeamBlue": 0, "TeamRed": 1}
ESCORT_GATE_STAGES = {0x4BB: 1, 0x4BC: 2}
PARAMETRIC_INSTANCE = 0xE42D9464
TARGET_TAG_INSTANCE = 0xB1B9C667
OBJECTIVE_AREA_TAG = 0x64100592
RAIL_POINT_DEFINITIONS = {0x0400000000000122, 0x0400000000002041, 0x0400000000002042}
RAIL_WAYPOINT_DEFINITIONS = RAIL_POINT_DEFINITIONS - {0x0400000000000122}
HEALTH_PACK_DEFINITIONS = {0x0400000000000FC2: "large", 0x0400000000000FC3: "small"}
ASSAULT_MODE, ESCORT_MODE, HYBRID_MODE, CONTROL_MODE = 0x14, 0x15, 0x16, 0x17
NAMES = {
    0x661: "Tutorial",
    0x751: "Numbani",
    0x6FE: "King's Row (Uprising)",
    0x79F: "Rialto (Retribution)",
    0xAFE: "Havana (Storm Rising)",
    0x6C9: "Junkenstein's Revenge",
}


def guid(index: int, file_type: int) -> int:
    """A GUID from its index and file type: the type sits bit-reversed at bits 48..59."""
    return int(f"{file_type - 1:012b}"[::-1], 2) << 48 | index


def hex_guid(value: int) -> str:
    return f"0x{value:016X}"


def ref_index(text: str) -> int:
    """ "000000002962.01C" -> 0x2962"""
    return int(text.split(".")[0], 16)


def f32(value: float) -> float:
    """The shortest decimal that is still the same 32-bit float."""
    for digits in range(1, 10):
        short = float(f"{value:.{digits}g}")
        if struct.pack("<f", short) == struct.pack("<f", value):
            return short
    return value


def yaw(q: tuple) -> float:
    """Rotation around +Y in degrees."""
    x, y, z, w = q
    return round(math.degrees(math.atan2(2 * (w * y + x * z), 1 - 2 * (x * x + y * y))), 3)


def read_placeables(data: bytes) -> list:
    """[(offset, type)] of one map chunk; [] for chunks in other formats."""
    count, _, offset, extra = struct.unpack_from("<4I", data, 0)
    if extra or not offset:
        return []
    out = []
    for _ in range(count):
        if offset + 24 > len(data):
            return []
        kind, length = struct.unpack_from("<BI", data, offset + 19)
        if length < 24 or offset + length > len(data):
            return []
        out.append((offset, kind))
        offset += length
    return out


def read_map(raw: Path, index: int, *, capture_areas: dict | None = None) -> tuple:
    """(first entity index, entity placeables) of map 002 <index>."""
    counts = {}
    entities = []
    for path in raw.glob(f"????{index:08X}.0BC"):
        data = path.read_bytes()
        for offset, kind in read_placeables(data):
            counts[kind] = counts.get(kind, 0) + 1
            if kind == AREA and capture_areas is not None:
                body = offset + 24
                end = offset + struct.unpack_from("<I", data, offset + 20)[0]
                if end < body + 44:
                    continue
                boxes, *other_shapes = struct.unpack_from("<5H", data, body + 12)
                start = body + struct.unpack_from("<I", data, body + 24)[0]
                # The retail 104319 box record ends at the half extents.
                if boxes and not any(other_shapes) and body + 44 <= start <= end - boxes * 40:
                    capture_areas[data[offset:offset + 16]] = [
                        [f32(v) for v in struct.unpack_from("<10f", data, start + i * 40)]
                        for i in range(boxes)
                    ]
            if kind != ENTITY:
                continue
            body = offset + 24
            definition, identifier = struct.unpack_from("<2Q", data, body)
            entity = {
                "definition": definition,
                "identifier": identifier,
                "position": struct.unpack_from("<3f", data, body + 24),
                "scale": struct.unpack_from("<3f", data, body + 36),
                "rotation": struct.unpack_from("<4f", data, body + 48),
                "client_id": struct.unpack_from("<H", data, body + 104)[0],
                "order": (int(path.name[:4], 16), offset),
                "uuid": data[offset : offset + 16],
            }
            for component in range(struct.unpack_from("<I", data, body + 64)[0]):
                entry = body + 112 + component * 8
                kind, relative = struct.unpack_from("<II", data, entry)
                if kind not in (1, 6, 11):
                    continue
                blob = entry + relative
                if data[blob : blob + 4] != b"DUTS":
                    continue
                instance = blob + struct.unpack_from("<I", data, blob + 8)[0]
                cls = struct.unpack_from("<I", data, instance)[0]
                if kind == 1 and cls == PARAMETRIC_INSTANCE:
                    entity["rail_start"] = data[instance + 8 : instance + 24]
                elif kind == 11 and cls == OBJECTIVE_AREA_TAG:
                    entity["capture_area"] = data[instance + 24 : instance + 40]
                elif kind == 6 and cls == TARGET_TAG_INSTANCE and definition in RAIL_POINT_DEFINITIONS:
                    # Three padded coefficients after the instance header, highest power first.
                    entity["rail_curve"] = [
                        struct.unpack_from("<3f", data, instance + n) for n in (16, 32, 48)
                    ]
            entities.append(entity)
    entities.sort(key=lambda e: e["order"])
    return counts.get(LIGHT, 0) + counts.get(AREA, 0), entities


def escort_rail(
    base: int, entities: list, objectives: list, waypoint_vars=None, impact_stage=None,
) -> list | None:
    """Recover the consecutive authored cubics from the cart's destination UUID to the last goal."""
    carts = [obj for obj in objectives if obj["kind"] == "payload"]
    goals = sorted((obj for obj in objectives if obj["kind"] == "checkpoint"),
                   key=lambda obj: obj["objective"])
    if len(carts) != 1 or not goals:
        return None
    cart = entities[carts[0]["placeable"] - base]
    start_id = cart.get("rail_start")
    start = next((i for i, entity in enumerate(entities) if start_id and entity.get("uuid") == start_id), -1)
    checkpoints = {goal["placeable"] - base: goal for goal in goals}
    if start < 0 or any(right <= left for left, right in zip([start, *list(checkpoints)[:-1]], checkpoints,
                                                           strict=True)):
        return None
    end = next(reversed(checkpoints))
    waypoints = [i for i in range(start, end + 1) if entities[i]["definition"] in RAIL_WAYPOINT_DEFINITIONS]
    next_waypoints = dict(pairwise(waypoints))
    rail = []
    for index in range(start, end + 1):
        node = entities[index]
        position = node["position"]
        entry = {"position": [f32(v) for v in position]}
        if index != end:
            curve = node.get("rail_curve")
            if curve is None:
                return None
            destination = tuple(map(sum, zip(position, *curve, strict=True)))
            if not math.dist(destination, entities[index + 1]["position"]) < 0.01:
                return None
            entry["curve"] = [[f32(v) for v in coefficient] for coefficient in curve]
        if (goal := checkpoints.get(index)) is not None:
            entry["checkpoint"] = base + index
            if goal["objective"] == impact_stage:
                entry["gate_impact"] = True
        values = (waypoint_vars or {}).get(base + index, {})
        if (wait := float(values.get(0x03BB) or 0)) > 0:
            entry["wait_seconds"] = wait
        if index in next_waypoints and str(values.get(0x0DF4, "")).lower() in ("1", "true"):
            entry["auto_drive_to"] = next_waypoints[index] - start
        for key, field in ((0x3120, "open_gate"), (0x3121, "close_gate")):
            if (ref := values.get(key)) and (tag := ref_index(ref)):
                entry[field] = tag
        rail.append(entry)
    return rail


def components(stu: Path) -> tuple[dict, dict]:
    """Return the components and root model reference of each entity definition."""
    table, models = {}, {}
    for path in (stu / "003").glob("*.003.xml"):
        root = ET.parse(path).getroot()
        index = int(path.name.split(".")[0], 16)
        found = set()
        for node in root.iter():
            if node.get(NS + "name") == "m_componentMap":
                found = {int(child.get(NS + "key")) & 0xFFFFFFFF for child in node if child.get(NS + "key")}
                for component in node:
                    if component.tag == "STUModelComponent":
                        model = next((child.get("GUID") for child in component
                                      if child.get(NS + "name") == "m_model"), None)
                        if model and model.endswith(".00C"):
                            models[index] = ref_index(model)
                break
        table[index] = found
    return table, models


def escort_scene_placeables(base: int, entities: list, mirrored: list, allowed, definitions: dict) -> list:
    """Include modeled, server-owned props that pass the mode's placement filter."""
    return [
        index for index in mirrored
        if (entity := entities[index - base])["client_id"] == 0
        and (not entity["identifier"] or entity["identifier"] in allowed)
        and MODEL in definitions.get(entity["definition"] & 0xFFFFFFFF, ())
    ]


def escort_payload_shapes(entity: dict, models: dict, model_path: Path) -> list:
    model = models.get(entity["definition"] & 0xFFFFFFFF)
    if not (shapes := escort_model_shapes(model, model_path)):
        raise ValueError(f"Escort cart model {model!r} has no available mover shapes")
    return shapes


def escort_model_shapes(model: int | None, model_path: Path) -> list:
    from ow174.game import props

    if model is None:
        return []
    for name in (f"{model:012X}.00C", f"{guid(model, 0xC):016X}.bin"):
        path = model_path / name
        if path.is_file():
            return props.model_shapes(path.read_bytes())
    return []


def objective_rooms(
    base, entities, candidates, objectives, *, models=None, model_path=None, hide_unassigned_overlays=False,
    gate_stages=None, payload_gate=None, route_tags=(), control=False, restricted_rooms=(),
):
    """Associate room props with the exact target tags on the authored checkpoint."""
    rooms = {}
    if control:
        rooms.update({tag: [(stage, 0), (stage, 1)] for tag, stage in CONTROL_POINT_TAGS.items()})
    for objective in objectives:
        if objective["kind"] in ("point", "checkpoint"):
            for team, field in enumerate(("defense_tag", "attack_tag")):
                if tag := objective.get(field):
                    rooms.setdefault(int(tag, 16) & 0xFFFFFFFF, []).append((objective["objective"], team))

    # 01BE can carry a shared room tag only. A co-located team barrier also
    # carries the owning checkpoint tag and identifies the room on that side.
    barriers = [(entities[index - base], set(tags)) for index, tags, graphs in candidates
                if ROOM_BARRIER_GRAPH in graphs] if not control else []
    placements, hidden, collision_models = [], set(), {}
    for index, tags, graphs in candidates:
        gate_tag = None
        pairs = sorted({room for tag in tags for room in rooms.get(tag, ())})
        entity = entities[index - base]
        definition = entity["definition"] & 0xFFFFFFFF
        viewer = ROOM_BARRIER_TEAMS.get(definition)
        if ROOM_VISIBILITY_GRAPH in graphs and (pairs or hide_unassigned_overlays):
            hidden.add(index)
            flags = graphs[ROOM_VISIBILITY_GRAPH]
            enabled = {key for key, value in flags.items() if str(value).lower() in ("1", "true")}
            if control and not pairs:
                pairs = control_overlay_rooms(entity, restricted_rooms)
            if pairs and (control or enabled & {0x877, 0x878, 0x2A12, 0x2A13}):
                # 0D73 maps its role flags to the opposing team's visibility mask.
                viewers = [team for team, flags in enumerate(({0x877, 0x2A13}, {0x878, 0x2A12}))
                           if not enabled & flags]
                if control and definition in CONTROL_ROOM_COLLISION:
                    blocked_team = CONTROL_ROOM_COLLISION[definition]
                    viewers = [blocked_team]
                    pairs = [(stage, team) for stage, team in pairs if team != blocked_team]
                if not viewers:
                    continue
                kind = "overlay"
            else:
                continue
        elif definition == 0x01BE and barriers and tags:
            if not pairs:
                pairs = sorted({(stage, team) for wall, wall_tags in barriers
                                if wall_tags.intersection(tags)
                                and math.dist(entity['position'], wall['position']) <= 2.0
                                for tag in wall_tags for stage, team in rooms.get(tag, ())
                                if ROOM_BARRIER_TEAMS.get(wall['definition'] & 0xFFFFFFFF) == 1 - team})
            owners = {team for _, team in pairs}
            if len(owners) != 1:
                continue
            hidden.add(index)
            viewers = [1 - owners.pop()]
            kind = "restricted_sign"
        elif ROOM_BARRIER_GRAPH in graphs and viewer is not None and pairs:
            hidden.add(index)
            pairs = [(stage, team) for stage, team in pairs if team != viewer]
            kind = "barrier"
        elif SPAWN_DOOR_GRAPH in graphs:
            if not control:
                pairs = [(stage, team) for stage, team in pairs if team == 1]
            kind = "door"
            if not pairs:
                stages = ESCORT_GATE_STAGES if gate_stages is None else gate_stages
                pairs = sorted({(stages[tag], 1) for tag in tags if tag in stages})
                gate_tag = next((tag for tag in tags if tag in route_tags), None)
                if gate_tag is not None and not pairs:
                    pairs = [(0, 1)]
                kind = "checkpoint_gate"
        else:
            continue
        if not pairs:
            continue
        placement = {"placeable": index, "rooms": pairs, "kind": kind}
        if kind in ("overlay", "restricted_sign"):
            placement["viewers"] = viewers
        if kind in ("door", "checkpoint_gate"):
            if gate_tag is not None:
                placement["gate_tag"] = gate_tag
            if GATE_DEBRIS_GRAPH in graphs or definition == 0x20E4:
                placement["gate_debris"] = True
            # The cart sends 0938 to this tag when its unlock animation starts.
            if payload_gate is not None and payload_gate in tags and kind == "checkpoint_gate":
                placement["payload_unlock"] = True
            placement.update(position=[f32(v) for v in entity["position"]],
                             scale=[f32(v) for v in entity["scale"]],
                             rotation=[f32(v) for v in entity["rotation"]])
            overrides = graphs[SPAWN_DOOR_GRAPH]
            for key, field in ((0x07F0, "open_seconds"), (0x07F3, "close_seconds"), (0x09CB, "open_delay"),
                               (0x09CC, "close_delay")):
                if overrides.get(key) is not None:
                    placement[field] = float(overrides[key])
        solid = (kind in ("door", "checkpoint_gate", "barrier")
                 or (control and definition in CONTROL_ROOM_COLLISION))
        if solid and model_path is not None and (model := (models or {}).get(definition)) is not None:
            if model not in collision_models:
                collision_models[model] = escort_model_shapes(model, model_path)
            if collision_models[model]:
                placement.update(model=model, position=[f32(v) for v in entity["position"]],
                                 scale=[f32(v) for v in entity["scale"]],
                                 rotation=[f32(v) for v in entity["rotation"]])
        placements.append(placement)
    if control:
        fit_control_room_panels(base, entities, placements)
    return placements, hidden, {str(model): shapes for model, shapes in collision_models.items() if shapes}


def fit_control_room_panels(base, entities, placements):
    from ow174.game import contacts

    overlays = [(p, entities[p["placeable"] - base]) for p in placements if p["kind"] == "overlay"]
    rims = [(p, entity, CONTROL_ROOM_RIMS[entity["definition"] & 0xFFFFFFFF]) for p, entity in overlays
            if entity["definition"] & 0xFFFFFFFF in CONTROL_ROOM_RIMS]
    for panel, entity in overlays:
        if entity["definition"] & 0xFFFFFFFF not in CONTROL_ROOM_COLLISION:
            continue
        normal = contacts.rotate(entity["rotation"], (0, 0, 1))
        matches = [
            (p, rim, size) for p, rim, size in rims if p["rooms"] == panel["rooms"]
            and math.dist(entity["position"], rim["position"]) <= 0.8
            and contacts.dot(normal, contacts.rotate(rim["rotation"], (0, 0, 1))) >= 0.99
        ]
        if len(matches) != 1:
            continue
        rim_placement, rim, (width, height) = matches[0]
        owner = panel["rooms"][0][1]
        if rim_placement["viewers"] != [owner]:
            continue
        normal = contacts.rotate(rim["rotation"], (0, 0, 1))
        depth = contacts.dot(normal, contacts.sub(entity["position"], rim["position"]))
        position = contacts.add(rim["position"], contacts.scale(normal, min(depth, -0.02)))
        panel.update(
            placement_definition=entity["definition"], position=[f32(v) for v in position],
            rotation=[f32(v) for v in rim["rotation"]],
            scale=[f32(width * rim["scale"][0] / ROOM_PANEL_SIZE),
                   f32(height * rim["scale"][1] / ROOM_PANEL_SIZE), f32(entity["scale"][2])],
        )


def control_room_areas(base, entities, candidates, areas):
    healing, restricted = {}, []
    for index, tags, graphs in candidates:
        stage = next((CONTROL_POINT_TAGS[tag] for tag in tags if tag in CONTROL_POINT_TAGS), None)
        entity = entities[index - base]
        area_id = entity.get("capture_area")  # The same target-tag STU links rooms and objectives to AREA.
        if stage is None or not (boxes := areas.get(area_id)):
            continue
        if ROOM_HEALING_GRAPH in graphs:
            team = CONTROL_ROOM_TEAMS.get(graphs[ROOM_HEALING_GRAPH].get(0x6F1))
            if team is not None:
                healing[area_id] = (stage, team, entity["position"], boxes)
        if ROOM_RESTRICTED_GRAPH in graphs:
            restricted.append((area_id, stage, entity["position"], boxes))
    room_bounds = []
    for area_id, stage, position, boxes in restricted:
        owner = healing.get(area_id)
        if owner is None:
            # Busan authors a larger, separate 0E8B volume at the same spawn-room anchor.
            owner = min((room for room in healing.values() if room[0] == stage),
                        key=lambda room: math.dist(position, room[2]), default=None)
        if owner is not None:
            room_bounds.append(((stage, owner[1]), boxes))
    return (
        [{"stage": stage, "team": team, "boxes": boxes} for stage, team, _, boxes in healing.values()],
        room_bounds,
    )


def control_overlay_rooms(entity, restricted_rooms):
    from ow174.game import contacts

    def box_distance(box):
        local = contacts.unrotate(box[:4], contacts.sub(entity["position"], box[4:7]))
        offsets = (max(0.0, abs(axis) - extent) for axis, extent in zip(local, box[7:], strict=True))
        return math.hypot(*offsets)

    # Untagged door layers are placed in or directly beside the 0E8B region.
    return sorted({
        room for room, boxes in restricted_rooms if any(box_distance(box) <= 0.75 for box in boxes)
    })


def modes(stu: Path) -> dict:
    """0C5 index -> m_A43573F4 identifiers."""
    table = {}
    for path in (stu / "0C5").glob("*.0C5.xml"):
        text = path.read_text(encoding="utf-8")
        match = re.search(r'dragon:name="m_A43573F4">(.*?)</dragon:array>', text, re.S)
        found = re.findall(r'GUID="([0-9A-F]+\.01C)"', match.group(1)) if match else []
        table[int(path.name.split(".")[0], 16)] = [guid(ref_index(g), 0x1C) for g in found]
    return table


def headers(stu: Path) -> list:
    out = []
    for path in sorted((stu / "09F").glob("*.09F.xml")):
        root = ET.parse(path).getroot()
        header = {"state": root.get("m_A125818B"), "modes": []}
        for child in root:
            name = child.get(NS + "name")
            if name == "m_map":
                header["map"] = ref_index(child.get("GUID"))
            elif name == "m_supportedGamemodes":
                refs = [r.get("GUID", "") for r in child]
                header["modes"] = [ref_index(r) for r in refs if r.endswith(".0C5")]
            elif name == "m_mapName":
                header["path"] = child.get("Value")
        header["index"] = int(path.name.split(".")[0], 16)
        out.append(header)
    return out


def instance_data(ents: Path, index: int) -> list:
    """Per entity placeable: the parsed instance data STUs, in placeable order."""
    path = ents / f"{index:X}_entities.xml"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    out = []
    for body in re.findall(r"<entity [^>]*>(.*?)</entity>", text, re.S):
        docs = [d.strip() for d in body.split('<?xml version="1.0" encoding="utf-8"?>') if d.strip()]
        out.append([ET.fromstring(d) for d in docs])
    return out


def target_tags(stus: list) -> list:
    for stu in stus:
        if stu.tag == "STUTargetTagInstanceData":
            return [ref_index(r.get("GUID")) for r in stu.iter() if r.get("GUID", "").endswith(".06C")]
    return []


def spawn_teams(stus: list) -> list:
    for stu in stus:
        if stu.tag == "STU_7D175935":
            return [t.text for t in stu.iter("TeamIndex")]
    return []


def graph_vars(stus: list) -> dict:
    """Graph index -> {var identifier index: value} of the statescript instance data."""
    out = {}
    for stu in stus:
        if stu.tag != "STUStatescriptComponentInstanceData":
            continue
        for graph in stu.iter("STUStatescriptGraphWithOverrides"):
            ref = next((c.get("GUID") for c in graph if c.get(NS + "name") == "m_graph"), None)
            if not ref:
                continue
            values = {}
            for entry in graph.iter("STUStatescriptSchemaEntry"):
                key = value = None
                for child in entry:
                    if child.get(NS + "name") == "m_0D09D2D9":
                        key = ref_index(child.get("GUID"))
                    elif child.get(NS + "name") == "m_value":
                        refs = [r.get("GUID") for r in child.iter() if r.get("GUID")]
                        refs = [r for r in refs if not r.startswith("000000000000")]
                        strings = [s.get("Value") for s in child.iter("teString")]
                        team = next((t.text for t in child.iter("TeamIndex") if t.text), None)
                        value = child.get("m_value") or child.get("m_6CE7A900") or team
                        value = value or next(iter(refs + strings), None)
                if key is not None:
                    values[key] = value
            out[ref_index(ref)] = values
    return out


def assault_point(entity: dict, areas: dict, objective: dict, graphs: dict) -> None:
    boxes = areas.get(entity.get("capture_area"))
    seconds = graphs[OBJECTIVE_GRAPH].get(0x2938)
    point = graphs.get(POINT_GRAPH, {})
    duration = point.get(0x306B)
    if boxes and seconds and duration:
        objective.update(capture_boxes=boxes, round_seconds=float(seconds), capture_seconds=float(duration),
                         decay_seconds=float(point.get(0x306C) or duration))


def assault_outlines(base, entities, objectives, indexes) -> None:
    """Associate eligible 0B78 outlines with their colocated capture points."""
    for objective in objectives:
        origin = objective["position"]
        nearby = [index for index in indexes if math.dist(origin, entities[index - base]["position"]) <= 5]
        if len(nearby) == 1:
            objective["outline"] = nearby[0]


def build_map(header: dict, raw: Path, ents: Path, defs: dict, mode_ids: dict, *,
              models: dict | None = None, model_path: Path | None = None) -> dict:
    index = header["map"]
    control = CONTROL_MODE in header["modes"]
    capture = bool({ASSAULT_MODE, HYBRID_MODE, CONTROL_MODE}.intersection(header["modes"]))
    capture_areas = {} if capture else None
    base, entities = read_map(raw, index, capture_areas=capture_areas)
    stus = instance_data(ents, header["index"])
    if len(stus) != len(entities):
        raise SystemExit(f"map {index:X}: {len(entities)} entities but {len(stus)} in the DataTool dump")
    placeables, identifiers, mirrored, spawns, objectives = [], {}, [], [], []
    health_packs, room_candidates, outline_candidates = [], [], set()
    debris_targets = []
    defense_tags, attack_tags = {}, {}
    payload_gate = None
    waypoint_vars, impact_stages = {}, {}
    for number, (entity, data) in enumerate(zip(entities, stus, strict=True)):
        flat = base + number
        found = defs.get(entity["definition"] & 0xFFFFFFFF, set())
        graphs = graph_vars(data)
        if (HYBRID_MODE in header["modes"] and entity["definition"] == 0x0400000000000176
                and 0x0389 in target_tags(data)):  # 0E41's map target beyond the gate
            debris_targets.append(entity["position"])
        if PAYLOAD_WAYPOINT_GRAPH in graphs:
            waypoint_vars[flat] = graphs[PAYLOAD_WAYPOINT_GRAPH]
        if capture and POINT_OUTLINE_GRAPH in graphs and entity["client_id"] == 0:
            outline_candidates.add(flat)
        if OBJECTIVE_GRAPH in graphs:
            values = graphs[OBJECTIVE_GRAPH]
            order = int(values.get(0x2934) or 0)
            defense = ref_index(values[0x2935]) if values.get(0x2935) else None
            attack = ref_index(values[0x2936]) if values.get(0x2936) else None
            objectives.append(
                {
                    "objective": order,
                    "name": graphs.get(POINT_GRAPH, {}).get(0x1DDB),
                    "kind": "checkpoint" if values.get(0x2931) else "point",
                    "placeable": flat,
                    "position": [f32(v) for v in entity["position"]],
                    "defense_tag": hex_guid(guid(defense, 0x6C)) if defense else None,
                    "attack_tag": hex_guid(guid(attack, 0x6C)) if attack else None,
                }
            )
            if values.get(0x2931) and values.get(0x2938):
                objectives[-1]["escort_seconds"] = float(values[0x2938])
            elif capture and POINT_GRAPH in graphs:
                assault_point(entity, capture_areas, objectives[-1], graphs)
            if HYBRID_MODE in header["modes"] and values.get(0x2A33):
                objectives[-1]["casual_seconds"] = float(values[0x2A33])
            if defense:
                defense_tags.setdefault(defense, []).append(order)
            if attack:
                attack_tags.setdefault(attack, []).append(order)
        elif POINT_GRAPH in graphs:
            stage = next((CONTROL_POINT_TAGS[t] for t in target_tags(data) if t in CONTROL_POINT_TAGS), None)
            if stage is not None:
                fields = graphs[POINT_GRAPH]
                goal = {
                    "stage": stage,
                    "name": fields.get(0x6D9),
                    "kind": "control point",
                    "placeable": flat,
                    "position": [f32(v) for v in entity["position"]],
                }
                if control and (boxes := capture_areas.get(entity.get("capture_area"))):
                    goal["capture_boxes"] = boxes
                if name := fields.get(0x1DDB):
                    goal["submap_name"] = hex_guid(guid(ref_index(name), 0x7C))
                objectives.append(goal)
        elif PAYLOAD_GRAPH in graphs:
            objectives.append(
                {
                    "kind": "payload",
                    "placeable": flat,
                    "position": [f32(v) for v in entity["position"]],
                    "rotation": [f32(v) for v in entity["rotation"]],
                }
            )
            if PAYLOAD_GATE_IMPACT_GRAPH in graphs:
                impact_stages[flat] = int(graphs[PAYLOAD_GATE_IMPACT_GRAPH].get(0x02B9) or 0)
            if HYBRID_MODE in header["modes"]:
                overrides = graphs[PAYLOAD_GRAPH]
                if str(overrides.get(0x0552, "")).lower() in ("1", "true"):
                    objectives[-1]["auto_drive"] = True
                for key, field in ((0x0836, "unlock_delay"), (0x0846, "unlock_seconds"),
                                   (0x1E63, "speed_multiplier")):
                    if overrides.get(key) is not None:
                        objectives[-1][field] = float(overrides[key])
                if tag := overrides.get(0x312A):
                    payload_gate = ref_index(tag)
        if not found & {SPAWN, REQUIRED}:
            if entity["client_id"] == 0:
                mirrored.append(flat)
                if (entity["definition"] & 0xFFFFFFFF == 0x01BE
                        or graphs.keys() & {ROOM_BARRIER_GRAPH, ROOM_VISIBILITY_GRAPH, SPAWN_DOOR_GRAPH,
                                            ROOM_HEALING_GRAPH, ROOM_RESTRICTED_GRAPH,
                                            GATE_DEBRIS_PROP_GRAPH}):
                    tags = target_tags(data)
                    if not tags and (tag := EICHENWALDE_ROOM_SIGNS.get((index, flat))) is not None:
                        tags = (tag,)
                    room_candidates.append((flat, tags, graphs))
                size = HEALTH_PACK_DEFINITIONS.get(entity["definition"])
                # Placements with an identifier need a separate mode-specific selection rule.
                if size is not None and not entity["identifier"]:
                    health_packs.append({"kind": size, "placeable": flat})
            continue
        placeables.append(flat)
        if entity["identifier"]:
            identifiers[str(flat)] = hex_guid(entity["identifier"])
        if SPAWN not in found:
            continue
        tags = target_tags(data)
        team = next((TEAM_TAGS[t] for t in tags if t in TEAM_TAGS), None)
        spawn = {
            "team": team,
            "position": [f32(v) for v in entity["position"]],
            "yaw": yaw(entity["rotation"]),
            "placeable": flat,
            "entity_index": number,
            "rotation": [f32(v) for v in entity["rotation"]],
            "tags": [hex_guid(guid(t, 0x6C)) for t in tags],
            "teams": spawn_teams(data),
        }
        if entity["identifier"]:
            spawn["identifier"] = hex_guid(entity["identifier"])
        if FFA_TAG in tags:
            spawn["ffa"] = True
        spawns.append(spawn)
    # How the first team mode of the map uses each room.
    for spawn in spawns:
        tags = [int(t, 16) & 0xFFFFFFFF for t in spawn["tags"]]
        if control:
            stages = sorted({CONTROL_STAGES[t] for t in tags if t in CONTROL_STAGES})
            if stages:
                spawn["stage"] = stages[0]
        else:
            use = []
            for t in tags:
                use += [[k, "defense"] for k in defense_tags.get(t, [])]
                use += [[k, "attack"] for k in attack_tags.get(t, [])]
            if use:
                spawn["objectives"] = sorted(use)
    objectives.sort(key=lambda o: (o["kind"] == "payload", o.get("stage", 0), o.get("objective", 0)))
    by_mode = {}
    for mode in header["modes"]:
        if mode not in mode_ids:
            continue  # the header names a mode that has no 0C5 asset in this build
        allowed = set(mode_ids[mode])
        by_mode[hex_guid(guid(mode, 0xC5))] = [
            p for p in placeables if str(p) not in identifiers or int(identifiers[str(p)], 16) in allowed
        ]
    entry = {
        "modes": [hex_guid(guid(m, 0xC5)) for m in header["modes"]],
        "placeables": placeables,
        "identifiers": identifiers,
        "placeables_by_mode": by_mode,
        "first_entity": base,
        "spawns": spawns,
        "objectives": objectives,
        "mirrored": mirrored,
        "health_packs": health_packs,
    }
    for mode, prefix in ((ASSAULT_MODE, "assault"), (ESCORT_MODE, "escort"), (HYBRID_MODE, "hybrid"),
                         (CONTROL_MODE, "control")):
        if mode not in header["modes"]:
            continue
        allowed = set(mode_ids.get(mode, ()))
        kinds = {
            ASSAULT_MODE: {"point"}, ESCORT_MODE: {"checkpoint"},
            HYBRID_MODE: {"point", "checkpoint", "payload"},
            CONTROL_MODE: {"control point"},
        }[mode]
        eligible = {number for number in mirrored
                    if (identifier := entities[number - base]["identifier"]) == 0 or identifier in allowed}
        goals = [objective for objective in objectives if objective["kind"] in kinds
                 and (mode not in (HYBRID_MODE, CONTROL_MODE) or objective["placeable"] in eligible)]
        if mode == HYBRID_MODE:
            entry["hybrid_objective_placeables"] = [goal["placeable"] for goal in goals]
        candidates = [row for row in room_candidates if row[0] in eligible]
        restricted_rooms = ()
        if mode == CONTROL_MODE:
            entry["control_objective_placeables"] = [goal["placeable"] for goal in goals]
            entry["control_healing_rooms"], restricted_rooms = control_room_areas(
                base, entities, candidates, capture_areas,
            )
            gate_stages = {}
        else:
            gate_stages = None
        if mode == HYBRID_MODE:
            gate_stages = {**ESCORT_GATE_STAGES, 0x4BD: 3}
            if payload_gate is not None:
                gate_stages[payload_gate] = 1
        route_objectives = goals if mode == HYBRID_MODE else objectives
        rail = None
        if mode in (ESCORT_MODE, HYBRID_MODE):
            cart_index = next(
                (obj["placeable"] for obj in route_objectives if obj["kind"] == "payload"), None,
            )
            rail = escort_rail(base, entities, route_objectives, waypoint_vars, impact_stages.get(cart_index))
        route_tags = {p[key] for p in rail or () for key in ("open_gate", "close_gate") if key in p}
        room_placements, hidden, room_models = objective_rooms(
            base, entities, candidates, goals,
            models=models, model_path=model_path,
            hide_unassigned_overlays=mode != ESCORT_MODE,
            gate_stages=gate_stages, payload_gate=payload_gate if mode == HYBRID_MODE else None,
            route_tags=route_tags, control=mode == CONTROL_MODE, restricted_rooms=restricted_rooms,
        )
        if rail and len(debris_targets) == 1:
            gate_rooms = {p["gate_tag"]: p["rooms"] for p in room_placements
                          if p.get("gate_debris") and "gate_tag" in p}
            for point in rail:
                end = point.get("auto_drive_to")
                rooms = gate_rooms.get(point.get("open_gate"))
                if rooms is None or end is None or math.dist(rail[end]["position"], debris_targets[0]) > 2.0:
                    continue
                # The rubble graph compares the cart with the 0389 anchor at this path endpoint.
                for placement, _, graphs in candidates:
                    if GATE_DEBRIS_PROP_GRAPH not in graphs:
                        continue
                    entity = entities[placement - base]
                    room_placements.append({
                        "placeable": placement, "rooms": rooms, "kind": "gate_debris",
                        "debris_rail_node": end,
                        "position": [f32(v) for v in entity["position"]],
                        "rotation": [f32(v) for v in entity["rotation"]],
                        "scale": [f32(v) for v in entity["scale"]],
                    })
        entry[f"{prefix}_scene_placeables"] = [
            index for index in escort_scene_placeables(base, entities, mirrored, allowed, defs)
            if index not in hidden
        ]
        if room_placements:
            entry[f"{prefix}_room_placeables"] = room_placements
        if room_models:
            entry[f"{prefix}_room_models"] = room_models
        outline_indexes = outline_candidates.intersection(entry[f"{prefix}_scene_placeables"])
        if mode == CONTROL_MODE:
            for goal in goals:
                nearby = sorted((math.dist(goal["position"], entities[i - base]["position"]), i)
                                for i in outline_indexes)
                goal["outlines"] = [i for distance, i in nearby if distance <= 5]
        elif mode != ESCORT_MODE:
            assault_outlines(
                base, entities, [goal for goal in goals if goal["kind"] == "point"],
                outline_indexes,
            )
        if mode in (ASSAULT_MODE, CONTROL_MODE):
            continue
        if rail is not None:
            entry[f"{prefix}_rail"] = rail
            if model_path is not None:
                cart = next(obj for obj in route_objectives if obj["kind"] == "payload")
                placement = entities[cart["placeable"] - base]
                cart["collision"] = escort_payload_shapes(placement, models or {}, model_path)
                cart["scale"] = placement["scale"]
    return entry


def map_name(header: dict, english: dict) -> str:
    if header["map"] in NAMES:
        return NAMES[header["map"]]
    path = header.get("path", "")
    name = english.get(f"{header['index']:012X}.09F", {}).get("Name") or path.split("\\")[-1]
    variant = re.search(r"\((\w[^)]*)\)", path)
    return f"{name} ({variant.group(1)})" if variant and variant.group(1) not in name else name


def dump(maps: dict) -> str:
    """JSON with one placed entity, spawn or list per line."""
    lines = ["{"]
    for number, (key, entry) in enumerate(maps.items()):
        lines.append(f" {json.dumps(key)}: {{")
        fields = list(entry.items())
        for i, (field, value) in enumerate(fields):
            end = "," if i < len(fields) - 1 else ""
            if value and (field in ("spawns", "objectives", "health_packs")
                          or field.endswith("_room_placeables")):
                items = [f"   {json.dumps(v, ensure_ascii=False)}" for v in value]
                lines.append(f"  {json.dumps(field)}: [")
                lines.append(",\n".join(items))
                lines.append(f"  ]{end}")
            else:
                lines.append(f"  {json.dumps(field)}: {json.dumps(value, ensure_ascii=False)}{end}")
        lines.append(" }" + ("," if number < len(maps) - 1 else ""))
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stu", type=Path, required=True, help="STU dump with 003, 09F and 0C5 as XML")
    parser.add_argument("--raw", type=Path, required=True, help="folder that holds the raw 0BC map chunks")
    parser.add_argument("--ents", type=Path, required=True, help="dump-map-entities output folder")
    parser.add_argument("--models", type=Path, help="raw 00C cart and room models for mover collision")
    args = parser.parse_args()
    raw = args.raw / "0BC" if (args.raw / "0BC").is_dir() else args.raw
    defs, models = components(args.stu)
    mode_ids = modes(args.stu)
    english = json.loads((ROOT / "data" / "extracted_maps.json").read_text(encoding="utf-8"))
    maps = {}
    for header in headers(args.stu):
        if not header["modes"] or "map" not in header:
            continue
        entry = {"name": map_name(header, english), "state": header["state"]}
        entry.update(build_map(header, raw, args.ents, defs, mode_ids, models=models, model_path=args.models))
        maps[hex_guid(guid(header["map"], 0x2))] = entry
    OUT.write_text(dump(maps), encoding="utf-8")
    print(f"{len(maps)} maps -> {OUT}")


if __name__ == "__main__":
    main()
