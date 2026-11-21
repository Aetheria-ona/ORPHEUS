"""Server-owned capture objectives for a single custom Assault round."""

import math
from dataclasses import dataclass

from ow174.game import combat, contacts, escort
from ow174.game.content import AssaultMap, AssaultPoint, ControlPoint
from ow174.game.script import expr, graph
from ow174.game.statescript import InstanceWire, StateWire, remote_delta, remote_full_frame

ATTACKERS, DEFENDERS = escort.ATTACKERS, escort.DEFENDERS
Phase = escort.Phase
POINT_GRAPH = 0x0E8E
POINT_PRESENTER = 47
POINT_LOCAL_UI = 89  # client-only subtree for the world waypoint and on-point progress meter
POINT_OBJECTIVE = expr.Asset(0x0D80000000002932)
POINT_WAYPOINT_TYPE = 0x3F75
ASSAULT_WAYPOINT = expr.Asset(0x0030603F)  # CCC3F0 packs 030.114 and 603F.01C into a type-4 value.
OUTLINE_GRAPH = 0x0B78
OUTLINE_APPEARANCE = 28
POINT_LOCKED = 0x27AC
POINT_COMPLETED = 0x268B
CAPTURING_BODIES = 0x28A0  # 0F4A reads this array from the completed objective.
PROGRESS_BINDINGS = (105, 106, 107, 109, 110)
CAPTURE_SEGMENTS = 3
CAPTURE_SPEED = (0, 1, 1.5, 2)  # 0F4A's 079E capture-rate table
DECAY_DELAY = 5.0  # 079E parameter 0422


def contains(point: AssaultPoint | ControlPoint, position: tuple[float, float, float]) -> bool:
    return inside_boxes(point.boxes, position)


def inside_boxes(boxes, position) -> bool:
    for box in boxes:
        local = contacts.unrotate(box[:4], contacts.sub(position, box[4:7]))
        if all(abs(axis) <= extent for axis, extent in zip(local, box[7:], strict=True)):
            return True
    return False


@dataclass(frozen=True)
class PointState:
    entity_vars: dict
    instance_vars: dict

    @property
    def presentation(self) -> tuple[bool, bool]:
        return self.instance_vars[POINT_LOCKED][0], self.instance_vars[POINT_COMPLETED][0]

    @property
    def owner(self) -> int:
        return self.entity_vars[0x0063][0]

    @property
    def waypoint_visible(self) -> bool:
        return self.instance_vars[POINT_WAYPOINT_TYPE][0] is not None

    def frame(self, previous: "PointState | None"):
        definition = graph.graph(POINT_GRAPH)
        if definition is None:
            # The mode still publishes objective progress with pre-Assault graph catalogs.
            fields = self.entity_vars
            if previous is None:
                return remote_full_frame([], fields, {})
            return remote_delta([], {key: val for key, val in fields.items()
                                     if previous.entity_vars.get(key) != val}, {})
        waypoint = self.waypoint_visible
        previous_waypoint = previous is not None and previous.waypoint_visible
        states = {}
        if previous is None:
            states[POINT_PRESENTER] = StateWire(definition.state(POINT_PRESENTER).cls, True)
        # Begin the local subtree with its selector already set. The client
        # suppresses timers posted by the selector while applying a frame.
        if waypoint != previous_waypoint:
            states[POINT_LOCAL_UI] = StateWire(definition.state(POINT_LOCAL_UI).cls, waypoint)
        if previous is None:
            instance = InstanceWire(1, definition, vars=self.instance_vars, states=states)
            return remote_full_frame([instance], self.entity_vars, {1: definition})
        instance = InstanceWire(1, definition, vars={
            key: val for key, val in self.instance_vars.items() if previous.instance_vars.get(key) != val
        }, states=states)
        return remote_delta([instance], {key: val for key, val in self.entity_vars.items()
                                         if previous.entity_vars.get(key) != val}, {1: definition})


def outline_frame(locked: bool, *, full: bool, owner: int = DEFENDERS):
    definition = graph.graph(OUTLINE_GRAPH)
    if definition is None:
        return None
    # The boundary's native data-flow branches resolve the defending team's color
    # relative to each viewer.
    instance = InstanceWire(1, definition, vars={
        0x2EC4: (owner, ()),
        POINT_LOCKED: (locked, ()),
    })
    graphs = {1: definition}
    if full:
        instance.states[OUTLINE_APPEARANCE] = StateWire(definition.state(OUTLINE_APPEARANCE).cls, True)
        return remote_full_frame([instance], {}, graphs)
    return remote_delta([instance], {}, graphs)


class AssaultRound(escort.ObjectiveRooms):
    def __init__(self, scene: AssaultMap, *, capture_scale: float = 1.0) -> None:
        super().__init__(scene.room_placeables)
        self.points = scene.points
        self.capture_scale = capture_scale
        self.entities = tuple(escort.MAP_ENTITY | point.placeable for point in self.points)
        self.outlines = {
            escort.MAP_ENTITY | point.outline: index for index, point in enumerate(self.points)
            if point.outline is not None
        }
        self.phase = Phase.ASSEMBLE
        self.stage = 0
        self.deadline = 0.0
        self.last_update = 0.0
        self.last_attacker = 0.0
        self.progress = 0.0
        self.peak_progress = 0.0
        self.captured = 0
        self.attackers: tuple[expr.Entity, ...] = ()
        self.defenders: tuple[expr.Entity, ...] = ()
        self.first_capture_attackers: tuple[expr.Entity, ...] = ()
        self.overtime = False
        self.winner: int | None = None

    def start_setup(self, now: float) -> None:
        self.phase = Phase.SETUP
        self.deadline = now + escort.SETUP_SECONDS

    def outline_visible(self, index: int) -> bool:
        return index == self.stage and (
            self.phase == Phase.ACTIVE or (index == 0 and self.phase == Phase.SETUP)
        )

    @property
    def contested(self) -> bool:
        return bool(self.attackers and self.defenders)

    @property
    def secured(self) -> float:
        return math.floor(self.progress * CAPTURE_SEGMENTS + 1e-7) / CAPTURE_SEGMENTS

    def advance(self, now: float, participants) -> None:
        """Update the current point from living bodies and monotonic match time."""
        previous = self.phase, self.stage
        if self.phase == Phase.SETUP and now >= self.deadline:
            self.phase = Phase.ACTIVE
            self.last_attacker = self.last_update = self.deadline
            self.deadline += self.points[0].round_seconds
        if self.phase != Phase.ACTIVE:
            return
        point = self.points[self.stage]
        present = {ATTACKERS: [], DEFENDERS: []}
        for body, team, position in participants:
            if team in present and contains(point, position):
                present[team].append(expr.Entity(body))
        was_occupied = bool(self.attackers)
        self.attackers = tuple(present[ATTACKERS])
        self.defenders = tuple(present[DEFENDERS])
        end = self.deadline
        if self.overtime:
            end = self.last_attacker + escort.OVERTIME_SECONDS
            if self.attackers and (was_occupied or now < end):
                end = now + escort.OVERTIME_SECONDS
        elif now >= end and self.attackers:
            self.overtime = True
            end = now + escort.OVERTIME_SECONDS
        elapsed = max(0.0, min(now, end) - self.last_update)
        self.last_update = now
        if self.attackers and now < end:
            self.last_attacker = now
            if not self.contested:
                speed = CAPTURE_SPEED[min(len(self.attackers), len(CAPTURE_SPEED) - 1)]
                rate = speed * self.capture_scale / point.capture_seconds
                self.progress = min(1.0, self.progress + elapsed * rate)
                self.progress = max(self.secured, self.progress)
                self.peak_progress = max(self.peak_progress, self.progress)
                if self.progress >= 1.0 - 1e-7:
                    self._capture(now)
        elif not self.attackers:
            decay = min(elapsed, max(0.0, now - self.last_attacker - DECAY_DELAY))
            duration = point.decay_seconds or point.capture_seconds
            self.progress = max(self.secured, self.progress - decay / duration)
        if self.phase == Phase.ACTIVE and now >= end and self.deadline <= end:
            self._finish(DEFENDERS)
        if previous != (self.phase, self.stage):
            self._update_room_collision()

    def _capture(self, now: float) -> None:
        if self.stage == 0:
            self.first_capture_attackers = self.attackers
        self.captured += 1
        if self.captured == len(self.points):
            self.progress = 1.0
            self._finish(ATTACKERS)
            return
        self.stage = self.captured
        self.deadline = max(now, self.deadline) + self.points[self.stage].round_seconds
        self.progress = self.peak_progress = 0.0
        self.last_attacker = now
        self.overtime = False
        self.attackers = self.defenders = ()

    def _finish(self, team: int) -> None:
        self.phase = Phase.COMPLETE
        self.winner = team
        self.overtime = False
        self.attackers = self.defenders = ()

    def point_state(self, index: int) -> PointState:
        completed = index < self.captured
        active = self.phase == Phase.ACTIVE and index == self.stage
        waypoint = self.phase in (Phase.SETUP, Phase.ACTIVE) and index == self.stage
        progress = peak_progress = 0.0
        if completed:
            progress = peak_progress = 1.0
        elif index == self.stage:
            progress = expr.f32(self.progress)
            peak_progress = expr.f32(self.peak_progress)
        attackers, defenders = (self.attackers, self.defenders) if active else ((), ())
        point = self.points[index]
        label = chr(ord("A") + index)
        owner = ATTACKERS if completed else DEFENDERS
        # 0E8E presenter 71 reads lock, completion and label from its instance,
        # but reads its enabled flag, progress and counts from the entity.
        entity = {
            0x0062: ATTACKERS,
            0x0063: owner,
            0x266C: defenders,  # Blue. The local player's body in either array enables 153.116.
            0x266D: attackers,  # Red
            0x2688: len(attackers),
            0x2689: len(defenders),
            0x268A: progress,
            0x2690: active and self.contested,
            0x2694: bool(attackers) and not self.contested,
            0x26AC: peak_progress,
            0x2DA4: True,
            10545: POINT_OBJECTIVE,
            10548: index,
            10552: point.round_seconds,
        }
        if index == 0 and completed:
            entity[CAPTURING_BODIES] = self.first_capture_attackers
        instance = {
            0x06D9: label,
            0x1BE2: expr.Vec3(*point.position),
            0x2677: False,
            0x2696: DEFENDERS,
            POINT_COMPLETED: completed,
            POINT_LOCKED: not active,
            0x2F61: CAPTURE_SEGMENTS,
            0x2FAC: False,  # The first round has no previous team's target progress.
            # 079E's parameter 063A selects 0E8E's client-only world presenter (state 66).
            POINT_WAYPOINT_TYPE: ASSAULT_WAYPOINT if waypoint else None,
            0x3060: expr.f32(1 / CAPTURE_SEGMENTS),
            0x3067: 0.0,
        }
        return PointState({key: (val, ()) for key, val in entity.items()},
                          {key: (val, ()) for key, val in instance.items()})

    def point_descriptor(self, entity: int):
        point = self.point_state(self.entities.index(entity))
        return remote_full_frame([], point.entity_vars, {})

    def mode_state(
        self, now: float, assembly_end: float | None, *, hero_select_ready: bool, viewer_team: int,
    ) -> escort.ModeState:
        phase = Phase.WAITING if self.phase == Phase.ASSEMBLE and assembly_end is None else self.phase
        clock = assembly_end if phase == Phase.ASSEMBLE else self.deadline
        seconds = 0.0 if phase in (Phase.WAITING, Phase.COMPLETE) else escort.hud_clock_seconds(clock - now)
        grace = max(0.0, self.last_attacker + escort.OVERTIME_SECONDS - now) if self.overtime else 0.0
        grace = math.ceil(grace * 4) / 4
        heading = {
            Phase.WAITING: 0x6114,
            Phase.ASSEMBLE: 0x6115,
            Phase.SETUP: 0x10C4 if viewer_team == ATTACKERS else 0x10C5,
            Phase.ACTIVE: (0x10BE if viewer_team == ATTACKERS else 0x10C0) + self.stage,
        }.get(phase)
        goal = expr.Entity(self.entities[self.stage])
        goals = tuple(map(expr.Entity, self.entities))
        progress = round(self.progress, 4)
        total = round((self.stage + self.progress) / len(self.points), 4)
        points_won = (0, self.captured)
        entity = {
            59: int(phase),
            0x0783: expr.Asset(0x0DE0000000000000 | heading) if heading else None,
            0x09C3: points_won,
            0x1B3B: grace,
            0x1D65: self.winner,
            0x1EDD: 0,
            0x2548: False,
            0x26AD: goal,
            0x2931: POINT_OBJECTIVE,
            0x2934: self.stage,
            0x2937: goal,
            0x3058: goals,
            0x3101: points_won,
            0x349C: progress,
            0x34F3: total,
            0x59DC: expr.Map(((0x2A2B, ATTACKERS), (0x0517, seconds), (0x2934, self.stage),
                              (0x1EDD, 0), (0x2259, False), (0x59DA, grace / escort.OVERTIME_SECONDS))),
            combat.ATTACKING_TEAM_VAR: ATTACKERS,
            combat.DEFENDING_TEAM_VAR: DEFENDERS,
            combat.WAITING_FOR_PLAYERS: phase == Phase.WAITING,
            0x4C49: phase == Phase.WAITING,
            0x4C48: phase == Phase.ASSEMBLE,
            0x2F51: phase == Phase.SETUP,
            0x4C47: phase == Phase.ACTIVE,
            0x4C46: phase == Phase.COMPLETE,
        }
        if phase != Phase.WAITING:
            entity[escort.COUNTDOWN] = seconds
        instance = {
            59: int(phase),
            0x0517: seconds,
            0x09C3: points_won,
            0x0D40: self.overtime,
            0x1B3B: grace,
            0x1D65: self.winner,
            0x1EDD: 0,
            0x26AD: goal,
            0x2931: POINT_OBJECTIVE,
            0x2934: self.stage,
            0x2937: goal,
            0x2A2B: ATTACKERS,
            0x2A36: self.stage,
            0x3058: goals,
            0x349C: progress,
        }
        states = {4, 95, 171} | escort.round_phase_states(phase, hero_select_ready, seconds, self.overtime)
        if phase == Phase.ACTIVE:
            states.add(17)  # 0694's scoreboard permission (06DF)
        return escort.ModeState({key: (val, ()) for key, val in entity.items()},
                                {key: (val, ()) for key, val in instance.items()}, frozenset(states))
