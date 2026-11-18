"""Server-owned progress for a single custom Escort round."""

import bisect
import math
from copy import copy
from dataclasses import dataclass
from enum import IntEnum

from ow174.game import collision, combat, contacts
from ow174.game.content import EscortMap
from ow174.game.script import expr, graph
from ow174.game.statescript import InstanceWire, StateWire, remote_delta, remote_full_frame
from ow174.game.world import ANGLE_UNITS, Movement

DEFENDERS, ATTACKERS = 0, 1
SETUP_SECONDS = 45.0  # 0F4A action 294
OVERTIME_SECONDS = 5.0  # 0F4A action 430
PUSH_RADIUS = 5.0
PUSH_SPEED = (0.0, 1.4, 1.65, 1.9)
ROLLBACK_DELAY = 10.0
ROLLBACK_SPEED = 0.3  # 1225 state 6 with the default payload speed multiplier
SAMPLES_PER_SEGMENT = 32
COUNTDOWN = 1303
OBJECTIVE_KIND = 10545
PAYLOAD_OBJECTIVE = expr.Asset(0x0D80000000002933)
MODE_GRAPH = 0x0F4A
MODE_MESSAGES = 0x0CBB
PAYLOAD_GRAPH = 0x1225
MAP_ENTITY = 0x80000000


class Phase(IntEnum):
    # 0F4A branches on mode phase variable 59.
    WAITING = 0
    ASSEMBLE = 1
    SETUP = 2
    ACTIVE = 3
    COMPLETE = 5


class PayloadCollision:
    """Add the placed cart's moving model collision to one match's broadphase."""

    def __init__(self, base: contacts.StaticWorld, geometries: tuple) -> None:
        self.base = base
        self.geometries = geometries
        self.pose = None
        self.shapes = ()

    def place(self, position: tuple[float, float, float], yaw: int) -> None:
        pose = position, yaw
        if self.pose == pose:
            return
        self.pose = pose
        radians = yaw / ANGLE_UNITS / 2
        frame = position, (0.0, math.sin(radians), 0.0, math.cos(radians))
        self.shapes = tuple(contacts.StaticShape(shape, frame, len(self.base.shapes) + index)
                            for index, shape in enumerate(self.geometries))

    def query(self, low, high) -> list:
        return self.base.query(low, high) + [
            shape for shape in self.shapes if contacts.overlaps(low, high, shape.fat_low, shape.fat_high)
        ]


@dataclass(frozen=True)
class ModeState:
    entity_vars: dict
    instance_vars: dict
    states: frozenset[int]

    def frame(self, previous: "ModeState | None"):
        mode, messages = graph.graph(MODE_GRAPH), graph.graph(MODE_MESSAGES)
        waiting = self.instance_vars[59][0] == Phase.WAITING
        graphs = {1: mode, 2: messages}
        if previous is None:
            states = {state: StateWire(mode.state(state).cls, True) for state in self.states}
            # 0F4A's client Entry creates its 1FEB presentation and common UI. Its
            # separate 0CBB child listens for round-end and match phase messages.
            receivers = {state: StateWire(messages.state(state).cls, True)
                         for state in (0, 4, 5, 9, 11, 56)}
            instances = [InstanceWire(1, mode, vars=self.instance_vars, states=states),
                         InstanceWire(2, messages, states=receivers)]
            if waiting:
                hud = combat.waiting_hud(3)
                instances.append(hud)
                graphs[hud.id] = hud.graph
            return remote_full_frame(instances, self.entity_vars, graphs)
        states = {state: StateWire(mode.state(state).cls, state in self.states)
                  for state in self.states ^ previous.states}
        entity_vars = {key: value for key, value in self.entity_vars.items()
                       if previous.entity_vars.get(key) != value}
        instance_vars = {key: value for key, value in self.instance_vars.items()
                         if previous.instance_vars.get(key) != value}
        instances = [InstanceWire(1, mode, vars=instance_vars, states=states)]
        if previous.instance_vars[59][0] == Phase.WAITING and not waiting:
            instances.append(InstanceWire(3, None, gone=True))
        return remote_delta(instances, entity_vars, graphs)


class EscortRound:
    def __init__(self, scene: EscortMap) -> None:
        self.payload = MAP_ENTITY | scene.payload
        self.stage_seconds = scene.stage_seconds
        self.checkpoints = []
        self.positions = []
        self.distances = []
        for point in scene.rail:
            if point.checkpoint is not None:
                self.checkpoints.append((self._append(point.position), MAP_ENTITY | point.checkpoint))
            else:
                self._append(point.position)
            if point.curve:
                a, b, c = point.curve
                for i in range(1, SAMPLES_PER_SEGMENT):
                    t = i / SAMPLES_PER_SEGMENT
                    self._append(tuple(
                        p + t * (linear + t * (quadratic + t * cubic))
                        for p, cubic, quadratic, linear in zip(point.position, a, b, c, strict=True)
                    ))
        self.length = self.distances[-1]
        self.phase = Phase.ASSEMBLE
        self.stage = 0
        self.distance = 0.0
        self.deadline = 0.0
        self.last_update = 0.0
        self.last_attacker = 0.0
        self.overtime = False
        self.contested = False
        self.attackers = 0
        self.animation = 23  # 1225 state 23: looping idle, from node 37's false branch
        self.winner: int | None = None
        self.payload_shapes = tuple(collision.engine_shape(shape, scene.payload_scale)
                                    for shape in scene.payload_collision)
        self.payload_collision: PayloadCollision | None = None

    def _append(self, position: tuple[float, float, float]) -> float:
        distance = self.distances[-1] + math.dist(position, self.positions[-1]) if self.positions else 0.0
        if not self.distances or distance > self.distances[-1]:
            self.positions.append(position)
            self.distances.append(distance)
        return distance

    def start_setup(self, now: float) -> None:
        self.phase = Phase.SETUP
        self.deadline = now + SETUP_SECONDS

    def _pose(self) -> tuple[tuple[float, float, float], int]:
        end = min(bisect.bisect_right(self.distances, self.distance), len(self.distances) - 1)
        start = end - 1
        origin, destination = self.positions[start], self.positions[end]
        step = tuple(b - a for a, b in zip(origin, destination, strict=True))
        t = (self.distance - self.distances[start]) / (self.distances[end] - self.distances[start])
        position = tuple(p + t * dx for p, dx in zip(origin, step, strict=True))
        yaw = round(math.atan2(step[0], step[2]) * ANGLE_UNITS)
        return position, (yaw + 32768) % 65536 - 32768

    def movement(self) -> Movement:
        position, yaw = self._pose()
        return Movement(position, yaw, 0)

    def collision_world(self, world: collision.World) -> collision.World:
        if self.payload_shapes:
            world = copy(world)
            self.payload_collision = PayloadCollision(world.engine, self.payload_shapes)
            self.payload_collision.place(*self._pose())
            world.engine = self.payload_collision
        return world

    def checkpoint_frame(self, stage: int):
        # 0F4A reads the placed objective's 0F4C variables through the entity reference.
        return remote_full_frame([], {
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            10548: (stage, ()),
            10552: (self.stage_seconds[stage], ()),
            2190: (expr.Entity(self.payload), ()),
        }, {})

    def advance(self, now: float, participants) -> None:
        """Consume elapsed match time and the current positions of living players."""
        if self.phase == Phase.SETUP and now >= self.deadline:
            self.phase = Phase.ACTIVE
            self.last_attacker = self.last_update = self.deadline
            self.deadline += self.stage_seconds[0]
        if self.phase != Phase.ACTIVE:
            return
        position, _ = self._pose()
        previous_distance = self.distance
        nearby = [team for team, feet in participants
                  if team in (ATTACKERS, DEFENDERS) and math.dist(position, feet) <= PUSH_RADIUS]
        was_escorted = self.attackers > 0
        self.attackers = min(nearby.count(ATTACKERS), len(PUSH_SPEED) - 1)
        self.contested = self.attackers > 0 and DEFENDERS in nearby
        end = self.deadline
        if self.overtime:
            if self.attackers and (was_escorted or now < self.last_attacker + OVERTIME_SECONDS):
                self.last_attacker = now
            end = self.last_attacker + OVERTIME_SECONDS
        elif now >= end and self.attackers:
            self.overtime = True
            self.last_attacker = now
            end = now + OVERTIME_SECONDS
        elapsed = max(0.0, min(now, end) - self.last_update)
        self.last_update = now
        if self.attackers and now < end:
            self.last_attacker = now
            if not self.contested:
                self._push(elapsed * PUSH_SPEED[self.attackers], now)
        elif now > self.last_attacker + ROLLBACK_DELAY:
            rollback = min(elapsed, now - self.last_attacker - ROLLBACK_DELAY)
            floor = self.checkpoints[self.stage - 1][0] if self.stage else 0.0
            self.distance = max(floor, self.distance - rollback * ROLLBACK_SPEED)
        if self.phase == Phase.ACTIVE and now >= end and self.deadline <= end:
            self._finish(DEFENDERS)
        # 1225 states 17 and 18 are launched by the forward and rollback branches.
        self.animation = 23
        if self.phase == Phase.ACTIVE and self.distance != previous_distance:
            self.animation = 17 if self.distance > previous_distance else 18
        if self.payload_collision is not None and self.distance != previous_distance:
            self.payload_collision.place(*self._pose())

    def _push(self, distance: float, now: float) -> None:
        self.distance = min(self.length, self.distance + distance)
        while self.stage < len(self.checkpoints) and self.distance >= self.checkpoints[self.stage][0]:
            self.stage += 1
            if self.stage == len(self.checkpoints):
                self._finish(ATTACKERS)
                return
            self.deadline = max(self.deadline, now) + self.stage_seconds[self.stage]
            self.overtime = False

    def _finish(self, team: int) -> None:
        self.phase = Phase.COMPLETE
        self.winner = team
        self.overtime = False
        self.attackers = 0
        self.contested = False

    def mode_state(
        self, now: float, assembly_end: float | None, *, hero_select_ready: bool = True,
    ) -> ModeState:
        phase = Phase.WAITING if self.phase == Phase.ASSEMBLE and assembly_end is None else self.phase
        clock = assembly_end if phase == Phase.ASSEMBLE else self.deadline
        seconds = 0.0 if phase in (Phase.WAITING, Phase.COMPLETE) else max(0.0, clock - now)
        current_goal = min(self.stage, len(self.checkpoints) - 1)
        goal = expr.Entity(self.checkpoints[current_goal][1])
        start = self.checkpoints[current_goal - 1][0] if current_goal else 0.0
        progress = (self.distance - start) / (self.checkpoints[current_goal][0] - start)
        grace = max(0.0, self.last_attacker + OVERTIME_SECONDS - now) if self.overtime else 0.0
        heading = {Phase.WAITING: 0x6114, Phase.ASSEMBLE: 0x6115, Phase.SETUP: 0x6116}.get(phase)
        entity_vars = {
            COUNTDOWN: (math.ceil(seconds * 4) / 4, ()),
            1923: (expr.Asset(0x0DE0000000000000 | heading) if heading else None, ()),
            6971: (math.ceil(grace * 4) / 4, ()),
            9544: (False, ()),  # single round, no competitive time bank
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            10548: (current_goal, ()),
            10795: (ATTACKERS, ()),
            10796: (DEFENDERS, ()),
            12376: (tuple(expr.Entity(entity) for _, entity in self.checkpoints), ()),
            13468: (round(progress, 3), ()),
        }
        instance_vars = {
            59: (int(phase), ()),
            2190: (expr.Entity(self.payload), ()),
            2499: (tuple(int(team == self.winner) for team in (DEFENDERS, ATTACKERS)), ()),
            7717: (OVERTIME_SECONDS, ()),
            9901: (goal, ()),
            10551: (goal, ()),
            20257: (expr.Entity(self.payload), ()),
            7525: (self.winner, ()),
        }
        # State indexes in 0F4A's m_states: assembly, setup, game clock, and
        # 0695 with the winner. Keep the scoreboard assertion throughout.
        states = {4, 95, 111}  # objective parameters for the HUD and round progress
        states.update({
            Phase.WAITING: (),
            Phase.ASSEMBLE: (3, 5) if hero_select_ready else (),
            Phase.SETUP: (9, 10),
            Phase.ACTIVE: (14, 93),
            Phase.COMPLETE: (19, 20, 44),
        }[phase])
        if self.overtime:
            states.add(57)
        return ModeState(entity_vars, instance_vars, frozenset(states))

    def payload_frame(self):
        """Start the placed cart's own authored loop using its normal graph and animation alias."""
        definition = graph.graph(PAYLOAD_GRAPH)
        if definition is None:
            return None
        animation = definition.state(self.animation)
        state = StateWire(animation.cls, True, {"counter": 1})
        payload = InstanceWire(1, definition, states={self.animation: state})
        return remote_full_frame([payload], {}, {1: definition})
