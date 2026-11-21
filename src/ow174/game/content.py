"""Heroes and maps the game server knows."""

import json
import math
import re
from dataclasses import dataclass
from functools import cache
from itertools import pairwise

from ow174.paths import DATA_DIR

HEROES_PATH = DATA_DIR / "game_heroes_174.json"
MAPS_PATH = DATA_DIR / "game_maps_174.json"  # tools/extract_game_maps.py
SOLDIER = 0x02E000000000006E
PRACTICE_RANGE_MODE = 0x0230000000000018
ASSAULT_MODE = 0x0230000000000014
ESCORT_MODE = 0x0230000000000015
HYBRID_MODE = 0x0230000000000016
CONTROL_MODE = 0x0230000000000017
ATTACK_DEFEND_MODES = {ASSAULT_MODE, ESCORT_MODE, HYBRID_MODE}
# 104319 lobby team descriptions (C473/C474.07C) and 0F4A's initial team assignment.
FIRST_DEFENDING_TEAM, FIRST_ATTACKING_TEAM = 0, 1
TEAM_DEATHMATCH_MODE = 0x023000000000001D
TUTORIAL_MODE = 0x0230000000000019
TUTORIAL_MAP = 0x0800000000000661
# Modes whose loading screen waits for a state of the mode's own script (m_0FC17230 = 1 in the
# 0C5 asset; build 104319's loader gate at RVA 706BFF): the script must signal readiness first. Mei's
# Snowball Offensive, two PvE modes, Retribution and Storm Rising.
SCRIPTED_LOADING_MODES = {
    0x0230000000000008,
    0x023000000000000F,
    0x023000000000001A,
    0x0230000000000025,
    0x0230000000000043,
}


@dataclass(frozen=True)
class Hero:
    guid: int  # the 075 hero
    name: str
    role: str
    body: int  # the 003 entity the client plays as (the hero's m_gameplayEntity)


@cache
def heroes() -> dict[int, Hero]:
    table = json.loads(HEROES_PATH.read_text(encoding="utf-8"))
    return {
        int(key, 16): Hero(int(key, 16), entry["name"], entry["class"], int(entry["body"], 16))
        for key, entry in table.items()
    }


def _plain(text: str) -> str:
    return re.sub(r"[^0-9a-z]", "", text.lower().replace("ö", "o").replace("ú", "u"))


def find_hero(text: str) -> Hero | None:
    """A hero by its name ("soldier", "Torbjörn", "dva") or its GUID."""
    wanted = _plain(text)
    if not wanted:
        return None
    by_name = {_plain(hero.name): hero for hero in heroes().values()}
    if wanted in by_name:
        return by_name[wanted]
    starts = [hero for name, hero in by_name.items() if name.startswith(wanted)]
    if len(starts) == 1:
        return starts[0]
    try:
        return heroes().get(int(text, 16))
    except ValueError:
        return None


@dataclass(frozen=True)
class Spawn:
    team: int | None  # None: any team (free for all)
    position: tuple[float, float, float]
    yaw: float  # degrees
    placeable: int | None = None


@dataclass(frozen=True)
class EscortRailPoint:
    position: tuple[float, float, float]
    curve: tuple[tuple[float, float, float], ...] = ()
    checkpoint: int | None = None
    wait_seconds: float = 0.0
    auto_drive_to: int | None = None
    open_gate: int | None = None
    close_gate: int | None = None
    gate_impact: bool = False


@dataclass(frozen=True)
class EscortRoomPlacement:
    index: int
    rooms: tuple[tuple[int, int], ...]  # (checkpoint stage, room owner's team)
    door: bool
    collision: tuple = ()
    position: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 1.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    viewers: tuple[int, ...] = ()  # teams receiving the overlay, which can also carry team-filtered collision
    open_seconds: float = 2.0  # 0D32 schema default for 07F0
    close_seconds: float = 2.0  # 0D32 schema default for 07F3
    open_delay: float = 0.0  # 0D32 waits on 09CB before playing the opening clip
    has_transform: bool = True  # older local catalogs could omit the authored pose
    payload_unlock: bool = False
    placement_definition: int | None = None  # Create a fitted panel with an explicit pose.
    gate_tag: int | None = None
    close_delay: float = 0.0
    gate_debris: bool = False
    restricted_sign: bool = False
    debris_rail_node: int | None = None


@dataclass(frozen=True)
class EscortMap:
    payload: int
    rail: tuple[EscortRailPoint, ...]
    later_spawns: tuple[tuple[Spawn, ...], ...]
    stage_seconds: tuple[float, ...]
    payload_collision: tuple = ()
    payload_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    room_placeables: tuple[EscortRoomPlacement, ...] = ()
    payload_origin: tuple[float, float, float] | None = None
    payload_rotation: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class AssaultPoint:
    placeable: int
    position: tuple[float, float, float]
    boxes: tuple[tuple[float, ...], ...]  # rotation, centre, half extents
    round_seconds: float
    capture_seconds: float
    decay_seconds: float | None = None
    outline: int | None = None


@dataclass(frozen=True)
class AssaultMap:
    points: tuple[AssaultPoint, ...]
    later_spawns: tuple[tuple[Spawn, ...], ...]
    room_placeables: tuple[EscortRoomPlacement, ...]


@dataclass(frozen=True)
class HybridMap:
    point: AssaultPoint
    route: EscortMap
    unlock_delay: float
    unlock_seconds: float
    speed_multiplier: float
    auto_drive: bool = False


@dataclass(frozen=True)
class ControlPoint:
    placeable: int
    position: tuple[float, float, float]
    boxes: tuple[tuple[float, ...], ...]
    submap_name: int | None = None
    outlines: tuple[int, ...] = ()


@dataclass(frozen=True)
class ControlMap:
    points: tuple[ControlPoint, ...]
    later_spawns: tuple[tuple[Spawn, ...], ...]
    room_placeables: tuple[EscortRoomPlacement, ...] = ()
    healing_rooms: tuple[tuple[int, int, tuple[tuple[float, ...], ...]], ...] = ()  # stage, team, boxes
    capture_scale: float = 1.0
    score_scale: float = 1.0
    fixed_stage: int | None = None
    points_to_win: int | None = None


@dataclass(frozen=True)
class GameMap:
    name: str
    map_guid: int  # 002; the type bits reversed: 0x0800...
    mode_guid: int  # the 0C5 game mode it is played with
    placeables: tuple[int, ...]  # map entities the server creates, including those needed to finish loading
    spawns: tuple[Spawn, ...]
    mode_name: str = ""
    # Players per team, from the ruleset. The hero select screen's team list shows the missing ones as
    # empty slots.
    team_sizes: tuple[int, ...] = (6, 6)
    free_for_all: bool = False  # one team, everyone against everyone (its one size is the player count)
    preset_guid: int = 0  # custom rooms can use different presets for the same mode
    escort: EscortMap | None = None
    custom_start: int | None = None  # custom lobby's start rule, fixed when the match is created
    assault: AssaultMap | None = None
    hybrid: HybridMap | None = None
    control: ControlMap | None = None
    payload_speed_scale: float = 1.0  # custom mode rule 2530
    capture_speed_scale: float = 1.0  # custom mode rule 252E


START_IMMEDIATELY = 0x25C9  # identifiers used by mode variable 9672 in 0F4A/0EC2
START_WHEN_FULL = 0x25CA
START_MANUALLY = 0x25CB


FREE_FOR_ALL_TEAM = 4
SPECTATOR_TEAM = 3  # custom-lobby roster ID; in the world this uses the spectator filter bit


# Practice Range (0688.002 with 0018.0C5): its four server-owned placeables, the ones the running
# client has and the rule in tools/extract_game_maps.py gives, and the spawn point. Its
# mode has one player on the blue team and none on the red one.
PRACTICE_RANGE = GameMap(
    name="Practice Range",
    map_guid=0x0800000000000688,
    mode_guid=PRACTICE_RANGE_MODE,
    placeables=(0x88, 0x22E, 0x234, 0x289),
    spawns=(Spawn(None, (54.56507, 1.0, 42.14788), -170.3434),),
    mode_name="Practice Range",
    team_sizes=(1, 0),
)

# Build 104319: the 0C8C mission looks up this server-owned gate by tag 030E.
JUNKENSTEIN_MAP = 0x08000000000006C9
JUNKENSTEIN_MODES = (0x0230000000000003, 0x023000000000002A)
JUNKENSTEIN_GATE = 50  # flat placement index, definition 16F6
JUNKENSTEIN_THREE_THEY_WERE = 0x063000000000016E
JUNKENSTEIN_SHOCKING_SURPRISE = 0x0630000000000170

# Difficulty identifiers from each 104319 Arcade card's m_13E9E420.
_JUNKENSTEIN_CARD_DIFFICULTIES = {
    0x3C: 0x195E, 0x3D: 0x195F, 0x4A: 0x26DF, 0x3E: 0x1960,
    0x43: 0x195E, 0x44: 0x195F, 0x49: 0x26DF, 0x45: 0x1960,
    **dict.fromkeys(range(0x16B, 0x171), 0x26DF),
}

# Each challenge has its own .0C0 ruleset, 0119 through 011E.
_JUNKENSTEIN_CHALLENGE_FLAGS = dict(zip(
    range(0x16B, 0x171), (0x7D53, 0x7D54, 0x7D60, 0x7D61, 0x7D62, 0x7D63), strict=True,
))


def junkenstein_card_difficulty(card: int) -> int | None:
    """Resolve a full Arcade card GUID to its authored difficulty identifier."""
    if card >> 48 == 0x0630:
        identifier = _JUNKENSTEIN_CARD_DIFFICULTIES.get(card & 0xFFFFFFFFFFFF)
        if identifier is not None:
            return 0x0D80000000000000 | identifier
    return None


def junkenstein_card_mode_values(card: int) -> dict[int, bool | float]:
    """The challenge ruleset's mode-level overrides, separate from team settings."""
    if card >> 48 != 0x0630:
        return {}
    index = card & 0xFFFFFFFFFFFF
    flag = _JUNKENSTEIN_CHALLENGE_FLAGS.get(index)
    if flag is None:
        return {}
    values: dict[int, bool | float] = {flag: True}
    if index == 0x16F:
        values[0x7E0F] = 1.0
    return values


@cache
def _map_entries() -> dict[int, dict]:
    if not MAPS_PATH.is_file():
        return {}
    return {int(key, 16): entry for key, entry in json.loads(MAPS_PATH.read_text(encoding="utf-8")).items()}


def map_catalog() -> list[dict]:
    """Every map the data knows (plus the Practice Range), for the dashboard's map picker:
    {guid hex, name}, sorted by name."""
    maps = [{"guid": f"0x{PRACTICE_RANGE.map_guid:X}", "name": PRACTICE_RANGE.name}]
    maps += [
        {"guid": f"0x{guid:X}", "name": entry.get("name") or f"0x{guid:X}"}
        for guid, entry in _map_entries().items()
        if guid != PRACTICE_RANGE.map_guid
    ]
    maps.sort(key=lambda item: item["name"])
    return maps


def map_name(guid: int) -> str | None:
    """A map's name, or None when the data does not know it."""
    if guid == PRACTICE_RANGE.map_guid:
        return PRACTICE_RANGE.name
    entry = _map_entries().get(guid)
    return entry.get("name") if entry else None


def _first_round_spawns(entry: dict, mode: int, free_for_all: bool) -> tuple[Spawn, ...]:
    """Each team's spawn points of the first objective (team 1 attacks it, team 0 defends), or of any
    objective when the map names none; the free-for-all points in a free-for-all mode."""
    spawns = entry.get("spawns") or []
    chosen = []
    if not free_for_all:
        for team in (0, 1):
            mine = [spawn for spawn in spawns if spawn.get("team") == team and not spawn.get("ffa")]
            if mode == CONTROL_MODE:
                first = [spawn for spawn in mine if spawn.get("stage") == 0]
            else:
                first = [spawn for spawn in mine
                         if any(goal[0] == 0 for goal in spawn.get("objectives") or [])]
            chosen += first or mine
    # Dedicated deathmatch arenas can advertise Team Deathmatch with shared spawn points only.
    if free_for_all or (mode == TEAM_DEATHMATCH_MODE and not chosen):
        return tuple(
            Spawn(None, tuple(spawn["position"]), float(spawn.get("yaw", 0.0)))
            for spawn in spawns if spawn.get("ffa") or spawn.get("team") is None
        )
    return tuple(
        Spawn(spawn.get("team"), tuple(spawn["position"]), float(spawn.get("yaw", 0.0)),
              spawn.get("placeable"))
        for spawn in chosen
    )


def _objective_spawns(entry: dict, goals: list[dict]) -> tuple[tuple[Spawn, ...], ...]:
    return tuple(
        tuple(
            Spawn(s["team"], tuple(s["position"]), float(s.get("yaw", 0.0)), s.get("placeable"))
            for s in entry.get("spawns", ())
            if s.get("team") in (0, 1) and not s.get("ffa")
            and [goal["objective"], ("defense", "attack")[s["team"]]] in (s.get("objectives") or ())
        )
        for goal in goals[1:]
    )


def _objective_rooms(entry: dict, prefix: str) -> tuple[EscortRoomPlacement, ...]:
    room_models = entry.get(f"{prefix}_room_models", {})
    return tuple(
        EscortRoomPlacement(
            p["placeable"], tuple(tuple(room) for room in p["rooms"]),
            p["kind"] in ("door", "checkpoint_gate", "gate_debris"),
            tuple(room_models.get(str(p.get("model")), ())), tuple(p.get("position", (0, 0, 0))),
            tuple(p.get("rotation", (0, 0, 0, 1))), tuple(p.get("scale", (1, 1, 1))),
            tuple(p.get("viewers", ())),
            float(p.get("open_seconds", 2.0)), float(p.get("close_seconds", 2.0)),
            float(p.get("open_delay", 0.0)),
            has_transform="position" in p and "rotation" in p,
            payload_unlock=p.get("payload_unlock", False),
            placement_definition=p.get("placement_definition"),
            gate_tag=p.get("gate_tag"),
            close_delay=float(p.get("close_delay", 0.0)),
            gate_debris=p.get("gate_debris", False),
            restricted_sign=p["kind"] == "restricted_sign",
            debris_rail_node=p.get("debris_rail_node"),
        )
        for p in entry.get(f"{prefix}_room_placeables", ())
    )


def _capture_area(goal: dict) -> tuple[tuple[float, ...], tuple[tuple[float, ...], ...]] | None:
    position = tuple(goal["position"])
    if len(position) != 3 or not all(map(math.isfinite, position)):
        return None
    x, y, z = position
    # Older catalogs have objective centres. Fresh exports use authored area boxes.
    boxes = goal.get("capture_boxes", ((0, 0, 0, 1, x, y + 2, z, 5, 3, 5),))
    if not boxes or not all(len(box) == 10 and all(map(math.isfinite, box))
                            and min(box[7:]) > 0 for box in boxes):
        return None
    return position, tuple(map(tuple, boxes))


def _capture_point(goal: dict, placeables: list[int], seconds: float, capture: float) -> AssaultPoint | None:
    if goal["placeable"] not in placeables or (area := _capture_area(goal)) is None:
        return None
    timing = (
        float(goal.get("round_seconds", seconds)),
        float(goal.get("capture_seconds", capture)),
        float(goal.get("decay_seconds", goal.get("capture_seconds", capture))),
    )
    if not all(math.isfinite(value) and value > 0 for value in timing):
        return None
    outline = goal.get("outline")
    return AssaultPoint(goal["placeable"], *area, *timing,
                        outline=outline if outline in placeables else None)


def _assault_map(entry: dict, placeables: list[int]) -> AssaultMap | None:
    goals = sorted((obj for obj in entry.get("objectives") or () if obj["kind"] == "point"),
                   key=lambda obj: obj["objective"])
    if [goal["objective"] for goal in goals] != [0, 1]:
        return None
    points = []
    for goal, seconds, capture in zip(goals, (240, 180), (30, 22), strict=True):
        point = _capture_point(goal, placeables, seconds, capture)
        if point is None:
            return None
        points.append(point)
    return AssaultMap((points[0], points[1]), _objective_spawns(entry, goals),
                      _objective_rooms(entry, "assault"))


def _control_map(entry: dict, placeables: list[int]) -> ControlMap | None:
    goals = sorted((goal for goal in entry.get("objectives", ()) if goal["kind"] == "control point"
                    and goal["placeable"] in placeables), key=lambda goal: goal["stage"])
    if [goal["stage"] for goal in goals] != [0, 1, 2]:
        return None
    points = []
    for goal in goals:
        area = _capture_area(goal)
        if area is None:
            return None
        name = goal.get("submap_name")
        outlines = tuple(index for index in goal.get("outlines", ()) if index in placeables)
        points.append(ControlPoint(goal["placeable"], *area, int(name, 16) if name else None, outlines))
    later_spawns = tuple(
        tuple(Spawn(s["team"], tuple(s["position"]), float(s.get("yaw", 0.0)))
              for s in entry.get("spawns", ()) if s.get("stage") == stage
              and s.get("team") in (0, 1) and not s.get("ffa"))
        for stage in (1, 2)
    )
    healing_rooms = tuple(
        (room["stage"], room["team"], tuple(tuple(box) for box in room["boxes"]))
        for room in entry.get("control_healing_rooms", ())
    )
    return ControlMap(tuple(points), later_spawns, _objective_rooms(entry, "control"), healing_rooms)


def _escort_map(entry: dict, placeables: list[int], *, opening: dict | None = None) -> EscortMap | None:
    prefix = "hybrid" if opening is not None else "escort"
    rail = entry.get(f"{prefix}_rail")
    if not rail:
        return None
    objectives = entry.get("objectives", ())
    carts = [obj for obj in objectives if obj["kind"] == "payload"]
    goals = sorted((obj for obj in objectives if obj["kind"] == "checkpoint"),
                   key=lambda obj: obj["objective"])
    checkpoints = [point["checkpoint"] for point in rail if "checkpoint" in point]
    if (len(carts) != 1 or not goals or len(rail) < 2 or "checkpoint" in rail[0]
            or rail[-1].get("checkpoint") != goals[-1]["placeable"]
            or checkpoints != [obj["placeable"] for obj in goals]
            or not {carts[0]["placeable"], *checkpoints} <= set(placeables)):
        return None
    points = tuple(
        EscortRailPoint(tuple(point["position"]), tuple(tuple(vec) for vec in point.get("curve", ())),
                        point.get("checkpoint"), float(point.get("wait_seconds", 0.0)),
                        point.get("auto_drive_to"), point.get("open_gate"), point.get("close_gate"),
                        point.get("gate_impact", False))
        for point in rail
    )
    if any(not math.isfinite(point.wait_seconds) or point.wait_seconds < 0
           or (point.auto_drive_to is not None and (
               type(point.auto_drive_to) is not int or not i < point.auto_drive_to < len(points)
           )) for i, point in enumerate(points)):
        return None
    for point, next_point in pairwise(points):
        if (len(point.curve) != 3 or any(len(vec) != 3 for vec in (point.position, *point.curve))
                or len(next_point.position) != 3
                or not all(math.isfinite(axis) for vec in (point.position, *point.curve) for axis in vec)):
            return None
        end = tuple(map(sum, zip(point.position, *point.curve, strict=True)))
        if not 0 < math.dist(point.position, next_point.position) < math.inf:
            return None
        if math.dist(end, next_point.position) >= 0.01:
            return None
    # Older generated catalogs already have rails but predate the timing fields.
    defaults = (240.0, 150.0, 90.0)
    if opening is None:
        stage_seconds = tuple(float(goal.get("escort_seconds", defaults[min(i, len(defaults) - 1)]))
                              for i, goal in enumerate(goals))
    else:
        stage_seconds = tuple(float(goal.get("casual_seconds", 0)) for goal in goals)
    if not all(math.isfinite(seconds) and seconds > 0 for seconds in stage_seconds):
        return None
    cart = carts[0]
    spawn_goals = goals if opening is None else [opening, *goals]
    return EscortMap(cart["placeable"], points, _objective_spawns(entry, spawn_goals), stage_seconds,
                     tuple(cart.get("collision", ())), tuple(cart.get("scale", (1.0, 1.0, 1.0))),
                     _objective_rooms(entry, prefix),
                     tuple(cart["position"]) if "position" in cart else None,
                     tuple(cart["rotation"]) if "rotation" in cart else None)


def _hybrid_map(entry: dict, placeables: list[int]) -> HybridMap | None:
    objectives = entry.get("objectives", ())
    capture = [objective for objective in objectives if objective["kind"] == "point"]
    checkpoints = sorted(obj["objective"] for obj in objectives if obj["kind"] == "checkpoint")
    if len(capture) != 1 or capture[0]["objective"] != 0 or checkpoints != [1, 2]:
        return None
    opening = capture[0]
    if not opening.get("capture_boxes") or not opening.get("casual_seconds"):
        return None
    point = _capture_point({**opening, "round_seconds": opening["casual_seconds"]}, placeables, 0, 0)
    route = _escort_map(entry, placeables, opening=opening)
    if point is None or route is None:
        return None
    cart = next(objective for objective in objectives if objective["kind"] == "payload")
    delay, unlock, speed = (
        float(cart.get(key, default)) for key, default in (
            ("unlock_delay", 0), ("unlock_seconds", 0), ("speed_multiplier", 1),
        )
    )
    if not all(map(math.isfinite, (delay, unlock, speed))) or min(delay, unlock) < 0 or speed <= 0:
        return None
    return HybridMap(point, route, delay, unlock, speed, auto_drive=cart.get("auto_drive") is True)


def game_map(
    map_guid: int,
    mode_guid: int,
    mode_name: str = "",
    free_for_all: bool = False,
    team_sizes: tuple[int, ...] = (6, 6),
) -> GameMap | None:
    """The map as it is played in that mode with that many players per team, or None when the server
    can't host it: no data, a mode the map doesn't support, no spawn points, or a mode whose loading
    needs its own script."""
    if map_guid == PRACTICE_RANGE.map_guid:
        return PRACTICE_RANGE
    entry = _map_entries().get(map_guid)
    if entry is None:
        return None
    if mode_guid in SCRIPTED_LOADING_MODES:
        from ow174.game.pve import entrance

        if entrance(mode_guid, map_guid) is None:
            return None
    if mode_guid not in {int(mode, 16) for mode in entry.get("modes", [])}:
        return None
    by_mode = {int(mode, 16): indexes for mode, indexes in (entry.get("placeables_by_mode") or {}).items()}
    placeables = by_mode.get(mode_guid, entry.get("placeables", []))
    if mode_guid in (ASSAULT_MODE, ESCORT_MODE, CONTROL_MODE) or (
        mode_guid == HYBRID_MODE and entry.get("hybrid_rail")
    ):
        # The catalog includes server-managed scenery outside the loading gate.
        mirrored = set(entry.get("mirrored") or ())
        prefix, kinds = {
            ASSAULT_MODE: ("assault", ("point",)),
            ESCORT_MODE: ("escort", ("payload", "checkpoint")),
            HYBRID_MODE: ("hybrid", ("point", "payload", "checkpoint")),
            CONTROL_MODE: ("control", ("control point",)),
        }[mode_guid]
        allowed_goals = entry.get(f"{prefix}_objective_placeables")
        if allowed_goals is None and mode_guid == HYBRID_MODE:
            allowed_goals = ()
        extra_placeables = {
            objective["placeable"] for objective in (entry.get("objectives") or ())
            if (objective["kind"] in kinds and objective["placeable"] in mirrored
                and (allowed_goals is None or objective["placeable"] in allowed_goals))
        }
        extra_placeables.update(
            pack["placeable"] for pack in (entry.get("health_packs") or ())
            if pack["placeable"] in mirrored
        )
        extra_placeables.update(mirrored.intersection(entry.get(f"{prefix}_scene_placeables", ())))
        placeables = sorted(extra_placeables.union(placeables))
    if (map_guid == JUNKENSTEIN_MAP and mode_guid in JUNKENSTEIN_MODES
            and JUNKENSTEIN_GATE in entry.get("mirrored", ()) and JUNKENSTEIN_GATE not in placeables):
        placeables = sorted([*placeables, JUNKENSTEIN_GATE])
    spawns = _first_round_spawns(entry, mode_guid, free_for_all)
    if not spawns:
        return None
    sizes = tuple(team_sizes)
    return GameMap(
        entry["name"], map_guid, mode_guid, tuple(placeables), spawns, mode_name, sizes, free_for_all,
        escort=_escort_map(entry, placeables) if mode_guid == ESCORT_MODE else None,
        assault=_assault_map(entry, placeables) if mode_guid == ASSAULT_MODE and not free_for_all else None,
        hybrid=_hybrid_map(entry, placeables) if mode_guid == HYBRID_MODE and not free_for_all else None,
        control=_control_map(entry, placeables) if mode_guid == CONTROL_MODE and not free_for_all else None,
    )


SPAWN_SPACING = 1.5  # metres between players who get the same spawn point


def team_spawns(game_map: GameMap, team: int, stage: int) -> tuple[Spawn, ...]:
    scene = (game_map.hybrid.route if game_map.hybrid is not None
             else game_map.escort or game_map.assault or game_map.control)
    if stage and scene is not None and stage <= len(scene.later_spawns):
        points = tuple(spawn for spawn in scene.later_spawns[stage - 1] if spawn.team == team)
        if points:
            return points
    return tuple(spawn for spawn in game_map.spawns if spawn.team in (team, None)) or game_map.spawns


def spawn_point(
    game_map: GameMap, team: int, number: int, *, stage: int = 0,
) -> tuple[tuple[float, float, float], float]:
    """Where the number-th player of a team starts: that team's spawn points in turn, and players who
    share a point stand side by side along its right-hand axis (-cos yaw, 0, sin yaw)."""
    points = team_spawns(game_map, team, stage)
    if game_map.mode_guid == TEAM_DEATHMATCH_MODE and all(spawn.team is None for spawn in points):
        number = number * len(game_map.team_sizes) + team
    spawn = points[number % len(points)]
    row = number // len(points)
    side = (row + 1) // 2 * (1 if row % 2 else -1) * SPAWN_SPACING
    yaw = math.radians(spawn.yaw)
    x, y, z = spawn.position
    return (x - math.cos(yaw) * side, y, z + math.sin(yaw) * side), spawn.yaw
