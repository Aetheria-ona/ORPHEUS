"""Server-owned progress for a single custom Escort round."""

import bisect
import math
from copy import copy
from dataclasses import dataclass
from enum import IntEnum

from ow174.game import collision, combat, contacts
from ow174.game.content import FIRST_ATTACKING_TEAM, FIRST_DEFENDING_TEAM, EscortMap
from ow174.game.script import expr, graph
from ow174.game.statescript import InstanceWire, StateWire, owner_delta, remote_delta, remote_full_frame
from ow174.game.world import ANGLE_UNITS, Movement

DEFENDERS, ATTACKERS = FIRST_DEFENDING_TEAM, FIRST_ATTACKING_TEAM
SETUP_SECONDS = 45.0  # 0F4A action 294
OVERTIME_SECONDS = 5.0  # 0F4A action 430
PUSH_RADIUS = 7.5  # approximate horizontal reach, measured from the cart pivot
PUSH_HEIGHT = 4.0  # feet on the tall cart or the uphill side can escort it
PUSH_SPEED = (0.0, 1.4, 1.65, 1.9)
AUTO_DRIVE_SPEED = 2.0  # 1225 state 13 at the default ruleset speed
ROLLBACK_DELAY = 10.0
ROLLBACK_SPEED = 0.3  # 1225 state 6 with the default payload speed multiplier
FINISH_WARNING_DISTANCE = 15.0  # 1225 assigns 1ED5 and checks it in state 74
SAMPLES_PER_SEGMENT = 32
RAIL_LOOKAHEAD = 0.75
COUNTDOWN = 1303
HUD_CLOCK = 0x0517  # 0F4A state 168 copies the countdown to this member of 59DC
OBJECTIVE_KIND = 10545
CHECKPOINT_INDEX = 0x2934
PAYLOAD_OBJECTIVE = expr.Asset(0x0D80000000002933)
MODE_GRAPH = combat.PVP_PHASE_GRAPH
MODE_MESSAGES = combat.MODE_PHASE_RECEIVER
MODE_HUD_VALUES = 0x1B4A  # 0CBB state 50 registers the native HUD expressions
ACTIVE_SPAWNS = 0x8006  # 0C8F state 9, sampled by the 1B4A mode presentation provider
PAYLOAD_GRAPH = 0x1225
PAYLOAD_FORWARD_ANIM = 17
PAYLOAD_REVERSE_ANIM = 18
PAYLOAD_UNLOCK_ANIM = 21
PAYLOAD_AUTO_DRIVE_ANIM = 22
PAYLOAD_LOCKED_ANIM = 23
PAYLOAD_RESULT_ANIM = 29
PAYLOAD_GATE_IMPACT_GRAPH = 0x0C78
PAYLOAD_GATE_IMPACT_INSTANCE = 2
PAYLOAD_GATE_IMPACT_SECONDS = 10.0  # 0C78 state 0 plays the 0480 animation alias
PAYLOAD_WAYPOINT = 137  # native scope that owns 1225's client-only world presenter 116
PAYLOAD_FINISH_MUSIC = 75  # asserts 0665 for the mode's 1B47 music graph
PAYLOAD_UNLOCK_PLAYED = 0x0854  # state 24 skips the pre-unlock idle once the startup clip has begun
PAYLOAD_DIRECTION = 0x01B3  # entity: forward 1, idle 0, rollback -1
PAYLOAD_DISTANCE = 0x01DF
PAYLOAD_ENABLED = 0x0270  # selects Completed instead of Locked when the cart is inactive
PAYLOAD_ACTIVE = 0x02BB  # 1225 state 119 selects the route bar when this entity flag is set
PAYLOAD_PUSHERS = 0x1A9F  # instance: attacker bodies used for PusherCount and MovementSpeed
PAYLOAD_BLOCKERS = 0x1AA0  # instance: defender bodies used with the attacker list for IsContested
PAYLOAD_CHECKPOINT_FRACTIONS = 0x1D9E  # instance array read by 1225 state 119
PAYLOAD_CHECKPOINT_OFFSET = 0x30A6  # state 119 subtracts this from the mode's 2934
PAYLOAD_ROUTE_LENGTH = 0x1D9F
PAYLOAD_FINISH_FRACTION = 0x1EC7  # the full route in a single round
DOOR_GRAPH = 0x0D32
DOOR_DEBRIS_GRAPH = 0x19FA
GATE_DEBRIS_DELAY = 3.0  # 19FA waits before broadcasting 0AA0 to nearby breakables
GATE_DEBRIS_PROP_GRAPH = 0x0E41
GATE_DEBRIS_ANIM, GATE_DEBRIS_ALIAS = 0, 6
GATE_DEBRIS_SECONDS = 3.0  # 0E41 state 0
RESTRICTED_SIGN_GRAPH = 0x00F6
BARRIER_GRAPH = 0x0FA8
ROOM_OVERLAY_GRAPH = 0x0D73
MAP_ENTITY = 0x80000000
ACTIVE_HEADING = (0x0DE00000000000F7, 0x0DE00000000000F5)  # 1FEB: stop / escort the payload
MATCH_LIVE = 18  # 0F4A asserts 0694, replacing the preparation assertion (0701)
FINAL_MUSIC_STATE = 147  # 0F4A asserts 0D18 while the live clock is below 30 seconds
OVERTIME_ACTIVE_STATE = 131  # 0F4A publishes 0D40
OVERTIME_MUSIC_SCOPE = 177  # 0F4A's 075E sender for 1B47
ESCORT_HUD = 111  # 0F4A's 08D7 objective message, also consumed by the scoreboard
HUD_PROGRESS = 112  # transfers the cart's 009A into the mode's 349C ForCode value
HUD_PAYLOAD = 160  # native cart-reference stack that registers the objective bindings
HUD_CART_REFERENCE = 0x4F21  # instance value exported as ForCode key 3267 by state 95
HUD_CART_CHANGED = 508  # 0F4A action fired when the cart reference becomes valid
HUD_ROUND_DATA = 0x59DC  # 0F4A state 168 supplies the round context read by 1FEB
HUD_OBJECTIVE_KIND = 171  # publishes the current checkpoint type for 1B4A's progress reader
HUD_PROGRESS_BINDINGS = (105, 110)  # 0F4A ForCode exports 349C (section) and 34F3 (total)

DOOR_OPEN_ANIM, DOOR_CLOSE_ANIM = 0, 1
DOOR_OPEN_ALIAS, DOOR_CLOSE_ALIAS = 5, 8


class Phase(IntEnum):
    # 0F4A branches on mode phase variable 59.
    WAITING = 0
    ASSEMBLE = 1
    SETUP = 2
    ACTIVE = 3
    UNLOCK = 4  # Control's open-door countdown, between setup and active play.
    COMPLETE = 5
    ROUND_END = 6  # Control's slow-motion transition before the score summary.


class DoorPose(IntEnum):
    CLOSED = 0
    OPENING = 1
    OPEN = 2
    CLOSING = 3


@dataclass(frozen=True)
class DoorState:
    pose: DoorPose
    states: frozenset[int] = frozenset()
    starts_at: float = 0.0
    ends_at: float = 0.0
    timed: bool = False
    debris_played: bool = False


@dataclass(frozen=True)
class RouteGateCommand:
    opened: bool
    starts_at: float
    ends_at: float
    collision_at: float


class EscortCollision:
    """The cart and the active spawn-room collision in one match's broadphase."""

    def __init__(self, base: contacts.StaticWorld, geometries: tuple) -> None:
        self.base = base
        self.geometries = geometries
        self.pose = None
        self.frame = None
        self.shapes = ()
        self.cart = contacts.StaticWorld([])
        self.room_shapes = ()
        self.door_support: collision.RayWorld | None = None

    def place(self, position: tuple[float, float, float], yaw: int, pitch: int) -> None:
        pose = position, yaw, pitch
        if self.pose == pose:
            return
        self.pose = pose
        y, p = yaw / ANGLE_UNITS / 2, pitch / ANGLE_UNITS / 2
        rotation = contacts.quaternion_product(
            (0.0, math.sin(y), 0.0, math.cos(y)), (math.sin(p), 0.0, 0.0, math.cos(p)),
        )
        self.frame = position, rotation
        self.shapes = tuple(contacts.StaticShape(shape, self.frame, len(self.base.shapes) + index)
                            for index, shape in enumerate(self.geometries))
        self.cart = contacts.StaticWorld(self.shapes)

    def ground(self, x, z, top, bottom, category):
        cart = self._cart_ground(x, z, top, bottom, category)
        if self.door_support is None:
            return cart
        door = self.door_support.ground(x, z, top, bottom, category)
        return door if door is not None and (cart is None or door.y > cart.y) else cart

    def _cart_ground(self, x, z, top, bottom, category):
        if not self.shapes or top <= bottom:
            return None
        hit = contacts.scene_sweep(self.cart, (x, top, z), (x, bottom, z), ((0, 0, 0),), 0.0, category)
        if hit is None:
            return None
        _, point, normal, shape, triangle = hit
        surface = shape.geometry.surfaces[triangle] if triangle >= 0 else 0
        if normal[1] <= 0.0:
            return None
        y = point[1] + (normal[0] * (point[0] - x) + normal[2] * (point[2] - z)) / normal[1]
        return collision.Ground.at((x, y, z), normal, surface) if bottom <= y <= top else None

    def rider(self, mover):
        if mover.airborne:
            return None
        x, y, z = mover.position
        support = self._cart_ground(x, z, y + 0.12, y - 0.16, mover.category)
        if support is None or support.normal[1] <= mover.slope_cos or support.surface & collision.NOT_GROUND:
            return None
        origin, rotation = self.frame
        return contacts.unrotate(rotation, contacts.sub(mover.position, origin))

    def carry(self, mover, local, dt: float) -> None:
        origin, rotation = self.frame
        target = contacts.add(origin, contacts.rotate(rotation, local))
        mover.carry(contacts.sub(target, mover.position), dt)

    def query(self, low, high) -> list:
        return self.base.query(low, high) + [
            shape for shape in self.shapes + self.room_shapes
            if contacts.overlaps(low, high, shape.fat_low, shape.fat_high)
        ]


@dataclass(frozen=True)
class ModeState:
    entity_vars: dict
    instance_vars: dict
    states: frozenset[int]

    @property
    def phase(self) -> Phase:
        return Phase(self.instance_vars[59][0])

    @property
    def seconds(self) -> float:
        return self.entity_vars.get(COUNTDOWN, (0.0, ()))[0]

    @property
    def stage(self) -> int:
        return self.entity_vars[CHECKPOINT_INDEX][0]

    @property
    def phase_states(self) -> tuple[int, ...]:
        # 0CBB registers ForCode value 2123 and its accompanying phase flag.
        phase = self.phase
        if phase == Phase.ASSEMBLE and combat.ASSEMBLE_HEROES_STATE not in self.states:
            return ()
        return {
            Phase.WAITING: (12, 51),
            Phase.ASSEMBLE: (1, 52),
            Phase.SETUP: (10, 53),
            Phase.ACTIVE: (2, 54),
            Phase.COMPLETE: (3, 55),
        }[phase]

    def frame(
        self, previous: "ModeState | None", *, callout: tuple[int, int, int] | None = None,
        progress_cmfd: int | None = None, progress_bindings: tuple[int, ...] = HUD_PROGRESS_BINDINGS,
        removed_instance: int | None = None,
    ):
        mode, messages = graph.graph(MODE_GRAPH), graph.graph(MODE_MESSAGES)
        waiting = self.phase == Phase.WAITING
        graphs = {1: mode, 2: messages}
        if previous is None:
            states = {state: StateWire(mode.state(state).cls, True) for state in self.states}
            # 0CBB translates the mode's persistent phase messages for the client.
            receivers = {state: StateWire(messages.state(state).cls, True)
                         for state in (0, 4, 5, 9, 11, 56, *self.phase_states)}
            instances = [InstanceWire(1, mode, vars=self.instance_vars, states=states),
                         InstanceWire(2, messages, parent=(1, 12), states=receivers)]
            hud_values = graph.graph(MODE_HUD_VALUES)
            instances.append(InstanceWire(5, hud_values, parent=(2, 50), states={
                0: StateWire(hud_values.state(0).cls, True),
            }))
            graphs[5] = hud_values
            # Client-only 0F4A state 11 creates 1FEB and its common HUD descendants. Listing
            # them here would create a second HUD and replace the payload's parent handle.
            if waiting:
                hud = combat.waiting_hud(3)
                instances.append(hud)
                graphs[hud.id] = hud.graph
            return remote_full_frame(instances, self.entity_vars, graphs)
        states = {state: StateWire(mode.state(state).cls, state in self.states)
                  for state in self.states ^ previous.states}
        if progress_cmfd is not None:
            # These authored bindings have m_states entries and no remote-sync indexes.
            states.update({state: StateWire(mode.state(state).cls, True) for state in progress_bindings})
        entity_vars = {key: value for key, value in self.entity_vars.items()
                       if previous.entity_vars.get(key) != value}
        instance_vars = {key: value for key, value in self.instance_vars.items()
                         if previous.instance_vars.get(key) != value}
        instances = [InstanceWire(1, mode, vars=instance_vars, states=states)]
        old_phase, new_phase = set(previous.phase_states), set(self.phase_states)
        if old_phase != new_phase:
            receivers = {state: StateWire(messages.state(state).cls, state in new_phase)
                         for state in old_phase ^ new_phase}
            instances.append(InstanceWire(2, messages, states=receivers))
        if previous.phase == Phase.WAITING and not waiting:
            instances.append(InstanceWire(3, None, gone=True))
        if removed_instance is not None:
            instances.append(InstanceWire(removed_instance, None, gone=True))
        if progress_cmfd is not None:
            return owner_delta(progress_cmfd, False, instances, entity_vars, graphs, callout=callout)
        return remote_delta(instances, entity_vars, graphs, callout=callout)


def round_phase_states(phase: Phase, hero_select_ready: bool, seconds: float, overtime: bool) -> set[int]:
    states = set({
        Phase.WAITING: (combat.WAITING_FOR_PLAYERS_STATE,),
        Phase.ASSEMBLE: (combat.ASSEMBLE_HEROES_STATE, 10) if hero_select_ready else (),
        Phase.SETUP: (14, 93),
        Phase.ACTIVE: (MATCH_LIVE, ESCORT_HUD, HUD_PROGRESS),
        Phase.COMPLETE: (19, 20, 44, ESCORT_HUD, HUD_PROGRESS),
    }[phase])
    if phase == Phase.ACTIVE:
        if 0 < seconds < 30:
            states.add(FINAL_MUSIC_STATE)
        if overtime:
            states.update((OVERTIME_ACTIVE_STATE, OVERTIME_MUSIC_SCOPE))
    return states


def hud_clock_seconds(remaining: float) -> float:
    """Use the tenths shown by the live HUD during the final ten seconds."""
    remaining = max(0.0, remaining)
    precision = 10 if remaining <= 10 else 4
    return math.ceil(remaining * precision - 1e-7) / precision


@dataclass(frozen=True)
class PayloadState:
    animation: int | None
    stage: int
    progress: float
    distance: float
    route_length: float
    checkpoint_fractions: tuple[float, ...]
    active: bool
    enabled: bool
    direction: int
    pushers: tuple[expr.Entity, ...]
    blockers: tuple[expr.Entity, ...]
    unlock_seconds: float | None = None
    gate_impact_started: float | None = None
    unlock_played: bool = False
    checkpoint_offset: int = 0

    @property
    def near_finish(self) -> bool:
        remaining = self.route_length - self.distance
        return self.active and self.distance > 0 and 0.0 < remaining < FINISH_WARNING_DISTANCE

    def frame(self, previous: "PayloadState | None", *, animation_time: float = 0.0, now: float = 0.0):
        definition = graph.graph(PAYLOAD_GRAPH)
        if definition is None:
            return None
        states = {}
        old_animation = previous.animation if previous is not None else None
        if self.animation != old_animation:
            if old_animation is not None:
                states[old_animation] = StateWire(definition.state(old_animation).cls, False)
            if self.animation is not None:
                sample = {"counter": 1}
                if self.animation == PAYLOAD_UNLOCK_ANIM:
                    sample["time"] = round(animation_time * 1000)
                states[self.animation] = StateWire(definition.state(self.animation).cls, True, sample)
        if self.active != (previous.active if previous is not None else False):
            marker = definition.state(PAYLOAD_WAYPOINT)
            states[PAYLOAD_WAYPOINT] = StateWire(marker.cls, self.active)
        if self.near_finish != (previous.near_finish if previous is not None else False):
            music = definition.state(PAYLOAD_FINISH_MUSIC)
            if music is not None:
                states[PAYLOAD_FINISH_MUSIC] = StateWire(music.cls, self.near_finish)
        values = {0x009A: (self.progress, ()), PAYLOAD_DISTANCE: (self.distance, ()),
                  CHECKPOINT_INDEX: (self.stage, ())}
        if previous is None or self.active != previous.active:
            values[PAYLOAD_ACTIVE] = (self.active, ())
        if previous is None or self.direction != previous.direction:
            values[PAYLOAD_DIRECTION] = (self.direction, ())
        # 1225 state 34 clears 0270 while 02BB selects the Active route bar.
        # The world marker's state 7 also takes this false branch.
        instance_vars = {PAYLOAD_ENABLED: (self.enabled and not self.active, ()),
                         CHECKPOINT_INDEX: (self.stage, ())}
        if self.unlock_seconds is not None:
            instance_vars[PAYLOAD_UNLOCK_PLAYED] = (self.unlock_played, ())
        if previous is None or self.pushers != previous.pushers:
            instance_vars[PAYLOAD_PUSHERS] = (self.pushers, ())
        if previous is None or self.blockers != previous.blockers:
            instance_vars[PAYLOAD_BLOCKERS] = (self.blockers, ())
        if previous is None:
            # The cart's presenter converts each fraction to a distance using the route length.
            values[PAYLOAD_ROUTE_LENGTH] = (self.route_length, ())
            values[PAYLOAD_FINISH_FRACTION] = (1.0, ())
            instance_vars[PAYLOAD_CHECKPOINT_OFFSET] = (self.checkpoint_offset, ())
            if self.unlock_seconds is not None:
                instance_vars.update({0x0846: (self.unlock_seconds, ()), 0x30A7: (2, ())})
        if previous is None or self.checkpoint_fractions != previous.checkpoint_fractions:
            instance_vars[PAYLOAD_CHECKPOINT_FRACTIONS] = (self.checkpoint_fractions, ())
        instances = [InstanceWire(1, definition, vars=instance_vars, states=states)]
        definitions = {1: definition}
        previous_impact = previous.gate_impact_started if previous is not None else None
        if self.gate_impact_started != previous_impact and (impact := graph.graph(PAYLOAD_GATE_IMPACT_GRAPH)):
            instance_id = PAYLOAD_GATE_IMPACT_INSTANCE
            definitions[instance_id] = impact
            if self.gate_impact_started is None:
                instances.append(InstanceWire(instance_id, impact, gone=True))
            else:
                sample = round(max(0.0, now - self.gate_impact_started) * 1000)
                instances.append(InstanceWire(instance_id, impact, descriptor=True, states={
                    0: StateWire(impact.state(0).cls, True, {"counter": 1, "time": sample}),
                }))
        write = remote_full_frame if previous is None else remote_delta
        return write(instances, values, definitions)


class ObjectiveRooms:
    """Spawn-room placement, collision and script presentation for objective modes."""

    def __init__(self, placements) -> None:
        self.payload_collision: EscortCollision | None = None
        self.doors = {MAP_ENTITY | p.index: p for p in placements if p.door}
        self.barriers = {
            MAP_ENTITY | p.index: p for p in placements if not p.door and not p.viewers
        }
        self.overlays = {MAP_ENTITY | p.index: p for p in placements if p.viewers}
        self.room_collision = {}
        self.waiting_for_players = False

    @property
    def room_stage(self) -> int:
        return self.stage

    @staticmethod
    def _angles(direction) -> tuple[int, int]:
        dx, dy, dz = direction
        yaw = round(math.atan2(dx, dz) * ANGLE_UNITS)
        pitch = round(-math.atan2(dy, math.hypot(dx, dz)) * ANGLE_UNITS)
        return yaw, pitch

    def collision_world(self, world: collision.World, cart_shapes=()) -> collision.World:
        solid_overlays = {entity: p for entity, p in self.overlays.items() if p.collision}
        if cart_shapes or self.doors or self.barriers or solid_overlays:
            world = copy(world)
            first_room_shape = len(world.engine.shapes) + len(cart_shapes)
            for entity, placement in (self.doors | self.barriers | solid_overlays).items():
                frame = placement.position, placement.rotation
                shapes = tuple(
                    contacts.StaticShape(
                        collision.engine_shape(shape, placement.scale), frame, first_room_shape + i,
                    )
                    for i, shape in enumerate(placement.collision)
                )
                self.room_collision[entity] = shapes
                first_room_shape += len(shapes)
            self.payload_collision = EscortCollision(world.engine, cart_shapes)
            self._update_room_collision()
            world.engine = self.payload_collision
            world.moving_ground = self.payload_collision.ground
        return world

    def room_barriers(self, viewer_team: int) -> frozenset[int]:
        if viewer_team not in (DEFENDERS, ATTACKERS) or self.phase == Phase.COMPLETE:
            return frozenset()
        room = (self.room_stage, ATTACKERS if viewer_team == DEFENDERS else DEFENDERS)
        return frozenset(entity for entity, p in self.barriers.items() if room in p.rooms)

    def room_visuals(self, viewer_team: int) -> frozenset[int]:
        if viewer_team not in (DEFENDERS, ATTACKERS) or self.phase == Phase.COMPLETE:
            return frozenset()
        return self.room_barriers(viewer_team) | frozenset(
            entity for entity, p in self.overlays.items()
            if viewer_team in p.viewers and any(stage == self.room_stage for stage, _ in p.rooms)
        )

    def room_visual_frame(self, entity: int, viewer_team: int):
        is_barrier = entity in self.barriers
        is_sign = not is_barrier and self.overlays[entity].restricted_sign
        index = BARRIER_GRAPH if is_barrier else RESTRICTED_SIGN_GRAPH if is_sign else ROOM_OVERLAY_GRAPH
        definition = graph.graph(index)
        if definition is None:
            return None
        values = {0x06F1: (viewer_team, ())} if is_barrier else {0x0877: (False, ()), 0x0878: (False, ())}
        control = definition.state(3)  # native branch that enables the wall, overlay or sign model
        instance = InstanceWire(1, definition, vars=values, states={3: StateWire(control.cls, True)})
        return remote_full_frame([instance], {}, {1: definition})

    def door_open(self, entity: int) -> bool:
        placement = self.doors[entity]
        if (self.waiting_for_players and (0, ATTACKERS) in placement.rooms
                and placement.gate_tag is None and not placement.payload_unlock):
            return True
        first_stage = min(stage for stage, _ in placement.rooms)
        stage = self.room_stage
        return stage > first_stage or (stage == first_stage and self.phase >= Phase.ACTIVE)

    def door_passable(self, entity: int) -> bool:
        return self.door_open(entity)

    def door_movement(self, entity: int) -> Movement | None:
        # Doors and the cart both have the native 496257B5 mirrored component.
        # Prime the door's actor pose with the authored transform before animating its bones.
        placement = self.doors[entity]
        if not placement.has_transform:
            return None
        yaw, pitch = self._angles(contacts.rotate(placement.rotation, (0.0, 0.0, 1.0)))
        yaw = (yaw + 32768) % 65536 - 32768
        return Movement(placement.position, yaw, body_pitch=pitch)

    def _update_room_collision(self) -> None:
        if self.payload_collision is None:
            return
        closed = {entity for entity in self.doors if not self.door_passable(entity)}
        barriers = (self.room_visuals(ATTACKERS) | self.room_visuals(DEFENDERS)) & self.room_collision.keys()
        self.payload_collision.room_shapes = tuple(
            shape for entity in sorted(closed | barriers) for shape in self.room_collision[entity]
        )
        # Use the same placed geometry for the feet ray and capsule contacts.
        # Triangulated hull faces give the ray a stable height and surface normal.
        doors = [(entity, self.doors[entity]) for entity in sorted(closed) if self.doors[entity].collision]
        placements = [(entity, 0, p.position, p.scale, p.rotation) for entity, p in doors]
        models = {entity: p.collision for entity, p in doors}
        self.payload_collision.door_support = collision.RayWorld(
            *collision.probe_triangles((), (), models, placements, prop_surfaces=True),
        ) if doors else None

    def door_state(self, entity: int, now: float, previous: DoorState | None) -> DoorState:
        opened = self.door_open(entity)
        if previous is None:
            return DoorState(DoorPose.OPEN if opened else DoorPose.CLOSED)
        if not opened and previous.pose != DoorPose.CLOSED:
            return DoorState(DoorPose.CLOSED, frozenset({DOOR_CLOSE_ANIM, DOOR_CLOSE_ALIAS}))
        if previous.pose == DoorPose.CLOSED and opened:
            return DoorState(DoorPose.OPENING, previous.states, starts_at=now + self.doors[entity].open_delay)
        if previous.pose == DoorPose.OPENING:
            if DOOR_OPEN_ANIM not in previous.states:
                if now < previous.starts_at:
                    return previous
                return DoorState(DoorPose.OPENING, frozenset({DOOR_OPEN_ANIM}),
                                 ends_at=now + self.doors[entity].open_seconds)
            if now >= previous.ends_at:
                return DoorState(DoorPose.OPEN, frozenset({DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS}))
            return previous
        clip, alias = ((DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS) if opened else (DOOR_CLOSE_ANIM, DOOR_CLOSE_ALIAS))
        if not previous.states:
            return DoorState(previous.pose, frozenset({clip}))
        if alias not in previous.states:
            return DoorState(previous.pose, previous.states | {alias})
        return previous

    def door_frame(self, entity: int, state: DoorState, previous: DoorState | None, *, now: float = 0.0):
        placement = self.doors[entity]
        if placement.debris_rail_node is not None:
            return self.gate_debris_frame(state, previous, now)
        definition = graph.graph(DOOR_GRAPH)
        if definition is None:
            return None
        opened = state.pose in (DoorPose.OPENING, DoorPose.OPEN)
        opening = state.pose == DoorPose.OPENING and DOOR_OPEN_ANIM in state.states
        closing = state.pose == DoorPose.CLOSING
        settled_open = state.pose == DoorPose.OPEN
        values = {
            0x22A5: (opened, ()),
            0x02BB: (opening or closing, ()),
            0x3DF1: (settled_open, ()),
            0x3DF2: (not opening and not closing and not settled_open, ()),
            0x475B: (settled_open, ()),
        }
        if previous is None:
            # The source/instance store is initialized before the first sampled
            # animation. 0D32 then installs the corresponding persistent alias.
            values.update({
                0x07F0: (placement.open_seconds, ()), 0x07F3: (placement.close_seconds, ()),
                0x09CB: (placement.open_delay, ()), 0x1B7F: (1, ()),
                0x22A4: (opened, ()),
            })
            if placement.close_delay:
                values[0x09CC] = (placement.close_delay, ())
        old_states = previous.states if previous is not None else frozenset()
        states = {index: StateWire(definition.state(index).cls, index in state.states, {"counter": 1})
                  for index in old_states ^ state.states}
        if settled_open and DOOR_OPEN_ANIM in state.states and (
            DOOR_OPEN_ANIM not in old_states or previous.pose == DoorPose.OPENING
        ):
            # Re-sample and retain the terminal pose when the live clip ends. Some
            # placements have no component to retain an alias request on its own.
            counter = 2 if DOOR_OPEN_ANIM in old_states else 1
            states[DOOR_OPEN_ANIM] = StateWire(definition.state(DOOR_OPEN_ANIM).cls, True, {
                "counter": counter, "time": round(placement.open_seconds * 1000),
            })
        if (not opened and not closing and previous is not None and previous.pose != DoorPose.CLOSED
                and previous.pose != DoorPose.CLOSING):
            states[DOOR_CLOSE_ANIM] = StateWire(definition.state(DOOR_CLOSE_ANIM).cls, True, {
                "counter": 1, "time": round(placement.close_seconds * 1000),
            })
        if state.timed:
            clip = DOOR_OPEN_ANIM if opened else DOOR_CLOSE_ANIM
            if clip in states or (previous is not None and previous.pose == DoorPose.CLOSING and not closing):
                sample = max(0.0, min(now, state.ends_at) - state.starts_at)
                counter = (1 if opened else 2) + int(previous is not None and clip in old_states)
                states[clip] = StateWire(definition.state(clip).cls, True, {
                    "counter": counter, "time": round(sample * 1000),
                })
        instance = InstanceWire(1, definition, vars=values, states=states)
        write = remote_full_frame if previous is None else remote_delta
        # 0D32 distinguishes the requested direction from the open pose.
        # The open alias's begin action publishes 475B once the clip finishes.
        entity_vars = {0x22A5: (opened, ()), 0x475B: (settled_open, ())}
        if previous is None:
            entity_vars[0x0804] = (True, ())  # 0D32 entry publishes this before starting its pose branch
        instances = [instance]
        definitions = {1: definition}
        if (state.debris_played and previous is not None and not previous.debris_played
                and (debris := graph.graph(DOOR_DEBRIS_GRAPH)) is not None):
            definitions[2] = debris
            # 19FA's 0AA0 broadcast is separate from the 0E41 rubble animation.
            instances.append(InstanceWire(2, debris, descriptor=True, states={
                1: StateWire(debris.state(1).cls, True),
            }))
        return write(instances, entity_vars, definitions)

    @staticmethod
    def gate_debris_frame(state: DoorState, previous: DoorState | None, now: float):
        definition = graph.graph(GATE_DEBRIS_PROP_GRAPH)
        if definition is None:
            return None
        states = {}
        if state.pose != DoorPose.CLOSED:
            states[GATE_DEBRIS_ANIM] = StateWire(definition.state(GATE_DEBRIS_ANIM).cls, True, {
                "counter": 2 if previous is not None and previous.pose == DoorPose.OPENING else 1,
                "time": round((min(now, state.ends_at) - state.starts_at) * 1000),
            })
            if previous is None or previous.pose == DoorPose.CLOSED:
                states[GATE_DEBRIS_ALIAS] = StateWire(definition.state(GATE_DEBRIS_ALIAS).cls, True)
        write = remote_full_frame if previous is None else remote_delta
        return write([InstanceWire(1, definition, states=states)], {}, {1: definition})


class EscortRound(ObjectiveRooms):
    stage_offset = 0
    speed_multiplier = 1.0

    def __init__(self, scene: EscortMap, *, auto_drive: bool = False, speed_scale: float = 1.0) -> None:
        super().__init__(scene.room_placeables)
        self.payload = MAP_ENTITY | scene.payload
        self.speed_scale = speed_scale
        self.stage_seconds = scene.stage_seconds
        self.checkpoints = []
        self.positions = []
        self.distances = []
        self.auto_drive_end = 0.0
        rail_distances = []
        for index, point in enumerate(scene.rail):
            distance = self._append(point.position)
            rail_distances.append(distance)
            if index == 1 and auto_drive:
                self.auto_drive_end = distance
            if point.checkpoint is not None:
                self.checkpoints.append((distance, MAP_ENTITY | point.checkpoint))
            if point.curve:
                a, b, c = point.curve
                for i in range(1, SAMPLES_PER_SEGMENT):
                    t = i / SAMPLES_PER_SEGMENT
                    self._append(tuple(
                        p + t * (linear + t * (quadratic + t * cubic))
                        for p, cubic, quadratic, linear in zip(point.position, a, b, c, strict=True)
                    ))
        self.length = self.distances[-1]
        self.checkpoint_fractions = tuple(distance / self.length for distance, _ in self.checkpoints)
        self.start_offset = contacts.sub(scene.payload_origin or self.positions[0], self.positions[0])
        self.start_angles = None
        if scene.payload_rotation is not None:
            self.start_angles = self._angles(contacts.rotate(scene.payload_rotation, (0.0, 0.0, 1.0)))
        self.phase = Phase.ASSEMBLE
        self.stage = 0
        self.distance = 0.0
        self.deadline = 0.0
        self.last_update = 0.0
        self.last_attacker = 0.0
        self.overtime = False
        self.pushers: tuple[expr.Entity, ...] = ()
        self.blockers: tuple[expr.Entity, ...] = ()
        self.animation: int | None = PAYLOAD_LOCKED_ANIM
        self.speed = 0.0
        self.winner: int | None = None
        self.route_events = tuple(
            (distance, point) for distance, point in zip(rail_distances, scene.rail, strict=True)
            if point.wait_seconds or point.auto_drive_to is not None or point.open_gate is not None
            or point.close_gate is not None or point.gate_impact
        )
        self.route_distances = tuple(rail_distances)
        self.debris_distances = {
            entity: rail_distances[p.debris_rail_node]
            for entity, p in self.doors.items()
            if p.debris_rail_node is not None and 0 <= p.debris_rail_node < len(rail_distances)
        }
        self.debris_started: dict[int, float] = {}
        self.next_route_event = 0
        self.route_wait_until = 0.0
        self.route_drive_end = 0.0
        self.route_rollback_floor = 0.0
        self.gate_impact_started: float | None = None
        self.route_open_tags = {
            point.open_gate for _, point in self.route_events if point.open_gate is not None
        }
        self.route_gate_commands: dict[int, RouteGateCommand] = {}
        self.payload_shapes = tuple(collision.engine_shape(shape, scene.payload_scale)
                                    for shape in scene.payload_collision)

    def _append(self, position: tuple[float, float, float]) -> float:
        distance = self.distances[-1] + math.dist(position, self.positions[-1]) if self.positions else 0.0
        if not self.distances or distance > self.distances[-1]:
            self.positions.append(position)
            self.distances.append(distance)
        return distance

    def start_setup(self, now: float) -> None:
        self.phase = Phase.SETUP
        self.deadline = now + SETUP_SECONDS

    def _point(self, distance: float) -> tuple[float, float, float]:
        end = min(bisect.bisect_right(self.distances, distance), len(self.distances) - 1)
        start = end - 1
        origin, destination = self.positions[start], self.positions[end]
        step = tuple(b - a for a, b in zip(origin, destination, strict=True))
        t = (distance - self.distances[start]) / (self.distances[end] - self.distances[start])
        return tuple(p + t * dx for p, dx in zip(origin, step, strict=True))

    def _pose(self) -> tuple[tuple[float, float, float], int, int]:
        before = self._point(max(0.0, self.distance - RAIL_LOOKAHEAD))
        after = self._point(min(self.length, self.distance + RAIL_LOOKAHEAD))
        yaw, pitch = self._angles(contacts.sub(after, before))
        position = self._point(self.distance)
        # Start at the map entity's pose so the first snapshot agrees with its
        # creation. Pick up the track's tangent as the wheels move onto it.
        blend = max(0.0, 1.0 - self.distance / RAIL_LOOKAHEAD)
        if blend:
            position = contacts.add(position, contacts.scale(self.start_offset, blend))
            if self.start_angles is not None:
                start_yaw, start_pitch = self.start_angles
                yaw += round(((start_yaw - yaw + 32768) % 65536 - 32768) * blend)
                pitch += round((start_pitch - pitch) * blend)
        return position, (yaw + 32768) % 65536 - 32768, pitch

    def movement(self) -> Movement:
        position, yaw, pitch = self._pose()
        heading, incline = yaw / ANGLE_UNITS, -pitch / ANGLE_UNITS
        horizontal = self.speed * math.cos(incline)
        velocity = (
            horizontal * math.sin(heading), self.speed * math.sin(incline), horizontal * math.cos(heading),
        )
        return Movement(position, yaw, velocity=velocity, body_pitch=pitch)

    def collision_world(self, world: collision.World) -> collision.World:
        world = super().collision_world(world, self.payload_shapes)
        if self.payload_collision is not None:
            self.payload_collision.place(*self._pose())
        return world

    def door_open(self, entity: int) -> bool:
        if self.doors[entity].debris_rail_node is not None:
            return entity in self.debris_started
        if (command := self.route_gate_commands.get(entity)) is not None:
            return command.opened
        if self.doors[entity].gate_tag in self.route_open_tags:
            return False
        return super().door_open(entity)

    def door_passable(self, entity: int) -> bool:
        if (command := self.route_gate_commands.get(entity)) is not None:
            return command.opened == (self.last_update >= command.collision_at)
        return super().door_passable(entity)

    def door_state(self, entity: int, now: float, previous: DoorState | None) -> DoorState:
        if self.doors[entity].debris_rail_node is not None:
            start = self.debris_started.get(entity)
            if start is None:
                return DoorState(DoorPose.CLOSED)
            end = start + GATE_DEBRIS_SECONDS
            return DoorState(DoorPose.OPENING if now < end else DoorPose.OPEN, starts_at=start, ends_at=end)
        command = self.route_gate_commands.get(entity)
        if command is None:
            return super().door_state(entity, now, previous)
        if now < command.starts_at:
            # Keep the previous side's pose during the configured delay.
            if command.opened:
                return DoorState(DoorPose.CLOSED, frozenset({DOOR_CLOSE_ANIM, DOOR_CLOSE_ALIAS}))
            return DoorState(DoorPose.OPEN, frozenset({DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS}))
        playing = now < command.ends_at
        if command.opened:
            pose = DoorPose.OPENING if playing else DoorPose.OPEN
            clip, alias = DOOR_OPEN_ANIM, DOOR_OPEN_ALIAS
        else:
            pose = DoorPose.CLOSING if playing else DoorPose.CLOSED
            clip, alias = DOOR_CLOSE_ANIM, DOOR_CLOSE_ALIAS
        states = frozenset({clip} if playing else {clip, alias})
        debris_played = bool(previous and previous.debris_played) or (
            self.doors[entity].gate_debris and command.opened and now >= command.ends_at + GATE_DEBRIS_DELAY
        )
        return DoorState(pose, states, command.starts_at, command.ends_at, timed=True,
                         debris_played=debris_played)

    def _request_route_gate(self, tag: int, opened: bool, now: float, wait: float) -> bool:
        changed = False
        for entity, door in self.doors.items():
            if door.gate_tag != tag or self.door_open(entity) == opened:
                continue
            start = now + (door.open_delay if opened else door.close_delay)
            end = start + (door.open_seconds if opened else door.close_seconds)
            # Map waypoints can release the cart before the full clip settles.
            clear = min(end, now + wait) if opened and wait else end
            self.route_gate_commands[entity] = RouteGateCommand(opened, start, end, clear)
            changed = True
        return changed

    def checkpoint_frame(self, stage: int):
        # 0F4A reads the placed objective's 0F4C variables through the entity reference.
        return remote_full_frame([], {
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            10548: (stage + self.stage_offset, ()),
            10552: (self.stage_seconds[stage], ()),
            2190: (expr.Entity(self.payload), ()),
        }, {})

    @property
    def contested(self) -> bool:
        return bool(self.pushers and self.blockers) and not self.scripted_movement

    @property
    def scripted_movement(self) -> bool:
        return self.last_update < self.route_wait_until or self.distance < self.route_drive_end

    def advance(self, now: float, participants) -> None:
        """Consume elapsed match time and living players as (body, team, position)."""
        previous_room = self.phase, self.stage
        if self.phase == Phase.SETUP and now >= self.deadline:
            self.phase = Phase.ACTIVE
            self.last_attacker = self.last_update = self.deadline
            self.deadline += self.stage_seconds[0]
        if self.phase != Phase.ACTIVE:
            return
        x, y, z = self._pose()[0]
        previous_distance = self.distance
        previous_update, previous_event = self.last_update, self.next_route_event
        nearby = {ATTACKERS: [], DEFENDERS: []}
        for body, team, (px, py, pz) in participants:
            if team in nearby and abs(py - y) <= PUSH_HEIGHT and math.hypot(px - x, pz - z) <= PUSH_RADIUS:
                nearby[team].append(expr.Entity(body))
        was_escorted = bool(self.pushers)
        self.pushers = tuple(nearby[ATTACKERS])
        self.blockers = tuple(nearby[DEFENDERS])
        end = self.deadline
        if self.overtime:
            if self.pushers and (was_escorted or now < self.last_attacker + OVERTIME_SECONDS):
                self.last_attacker = now
            end = self.last_attacker + OVERTIME_SECONDS
        elif now >= end and self.pushers:
            self.overtime = True
            self.last_attacker = now
            end = now + OVERTIME_SECONDS
        elapsed = max(0.0, min(now, end) - max(self.last_update, self.route_wait_until))
        self.last_update = now
        route_driving = previous_distance < self.route_drive_end
        auto_end = max(self.auto_drive_end, self.route_drive_end)
        auto_driving = previous_distance < auto_end
        if auto_driving:
            if elapsed > 0:
                speed = AUTO_DRIVE_SPEED * self.speed_scale
                step = min(auto_end - previous_distance, elapsed * speed)
                self._push(step, now)
                self.last_attacker = now
                if route_driving and self.distance >= self.route_drive_end:
                    self.route_rollback_floor = max(self.route_rollback_floor, self.route_drive_end)
        elif now < self.route_wait_until:
            self.last_attacker = now
        elif self.pushers and now < end:
            self.last_attacker = now
            if not self.contested:
                speed_tier = min(len(self.pushers), len(PUSH_SPEED) - 1)
                self._push(elapsed * PUSH_SPEED[speed_tier] * self.speed_multiplier * self.speed_scale, now)
        elif now > self.last_attacker + ROLLBACK_DELAY:
            rollback = min(elapsed, now - self.last_attacker - ROLLBACK_DELAY)
            floor = max(self.route_rollback_floor,
                        self.checkpoints[self.stage - 1][0] if self.stage else self.auto_drive_end)
            self.distance = max(floor, self.distance - rollback * ROLLBACK_SPEED * self.speed_scale)
        if self.phase == Phase.ACTIVE and now >= end and self.deadline <= end:
            self._finish(DEFENDERS)
        # State 23 is the pre-unlock pose. On a live stop the native blend tree
        # resumes its base pose as the movement animation ends.
        self.animation = PAYLOAD_RESULT_ANIM if self.phase == Phase.COMPLETE else None
        self.speed = 0.0
        holding = now < self.route_wait_until
        finished_auto = route_driving and self.distance >= self.route_drive_end
        if (self.phase == Phase.ACTIVE and self.distance != previous_distance
                and not holding and not finished_auto):
            if auto_driving:
                self.animation = PAYLOAD_AUTO_DRIVE_ANIM
            else:
                forward = self.distance > previous_distance
                self.animation = PAYLOAD_FORWARD_ANIM if forward else PAYLOAD_REVERSE_ANIM
            self.speed = (self.distance - previous_distance) / elapsed if elapsed else 0.0
        if self.payload_collision is not None and self.distance != previous_distance:
            self.payload_collision.place(*self._pose())
        gate_collision_changed = any(
            previous_update < gate.collision_at <= now for gate in self.route_gate_commands.values()
        )
        if (previous_room != (self.phase, self.stage) or previous_event != self.next_route_event
                or gate_collision_changed):
            self._update_room_collision()

    def _push(self, distance: float, now: float) -> None:
        if distance <= 0:
            return
        destination = min(self.length, self.distance + distance)
        while self.next_route_event < len(self.route_events):
            marker, point = self.route_events[self.next_route_event]
            if destination < marker:
                break
            self.next_route_event += 1
            if point.open_gate is not None:
                self._request_route_gate(point.open_gate, True, now, point.wait_seconds)
            if point.close_gate is not None and self._request_route_gate(point.close_gate, False, now, 0.0):
                self.route_rollback_floor = max(self.route_rollback_floor, marker)
            if point.gate_impact:
                self.gate_impact_started = now
            if point.auto_drive_to is not None:
                self.route_drive_end = self.route_distances[point.auto_drive_to]
            if point.wait_seconds or point.auto_drive_to is not None:
                self.route_wait_until = now + point.wait_seconds
                destination = marker
                break
        for entity, trigger in self.debris_distances.items():
            if self.distance < trigger <= destination:
                self.debris_started.setdefault(entity, now)
        self.distance = destination
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
        self.pushers = self.blockers = ()

    def mode_state(
        self, now: float, assembly_end: float | None, *, hero_select_ready: bool = True,
        viewer_team: int = DEFENDERS,
    ) -> ModeState:
        phase = Phase.WAITING if self.phase == Phase.ASSEMBLE and assembly_end is None else self.phase
        clock = assembly_end if phase == Phase.ASSEMBLE else self.deadline
        current_goal, progress = self.progress()
        goal = expr.Entity(self.checkpoints[current_goal][1])
        grace = max(0.0, self.last_attacker + OVERTIME_SECONDS - now) if self.overtime else 0.0
        heading = {
            Phase.WAITING: 0x0DE0000000006114,
            Phase.ASSEMBLE: 0x0DE0000000006115,
            Phase.SETUP: 0x0DE0000000006116,
            Phase.ACTIVE: ACTIVE_HEADING[viewer_team == ATTACKERS],
        }.get(phase)
        seconds = 0.0 if phase in (Phase.WAITING, Phase.COMPLETE) else hud_clock_seconds(clock - now)
        phase_flags = {
            Phase.WAITING: 0x4C49, Phase.ASSEMBLE: 0x4C48, Phase.SETUP: 0x2F51,
            Phase.ACTIVE: 0x4C47, Phase.COMPLETE: 0x4C46,
        }
        entity_vars = {
            # 1B4A queries these on the mode entity. 4C21 reads the objective
            # kind and section progress, 4C22 reads the checkpoint/cart refs.
            59: (int(phase), ()),
            2190: (expr.Entity(self.payload), ()),
            0x1EDD: (0, ()),  # first and only round
            1923: (expr.Asset(heading) if heading else None, ()),
            6971: (math.ceil(grace * 4) / 4, ()),
            9544: (False, ()),  # single round, no competitive time bank
            combat.WAITING_FOR_PLAYERS: (phase == Phase.WAITING, ()),
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            CHECKPOINT_INDEX: (current_goal + self.stage_offset, ()),
            combat.ATTACKING_TEAM_VAR: (ATTACKERS, ()),
            combat.DEFENDING_TEAM_VAR: (DEFENDERS, ()),
            12376: (tuple(expr.Entity(entity) for _, entity in self.checkpoints), ()),
            13468: (round(progress, 3), ()),
            HUD_ROUND_DATA: (expr.Map((
                (combat.ATTACKING_TEAM_VAR, ATTACKERS),
                (HUD_CLOCK, seconds),
                (CHECKPOINT_INDEX, current_goal + self.stage_offset),
                (0x1EDD, 0),
                (0x2259, True),  # current objective is a payload
                (0x59DA, grace / OVERTIME_SECONDS),
            )), ()),
            **{key: (flag_phase == phase, ()) for flag_phase, key in phase_flags.items()},
        }
        if phase != Phase.WAITING:
            entity_vars[COUNTDOWN] = (seconds, ())
        instance_vars = {
            59: (int(phase), ()),
            2190: (expr.Entity(self.payload), ()),
            2499: (tuple(int(team == self.winner) for team in (DEFENDERS, ATTACKERS)), ()),
            7717: (OVERTIME_SECONDS, ()),
            9901: (goal, ()),
            OBJECTIVE_KIND: (PAYLOAD_OBJECTIVE, ()),
            10551: (goal, ()),
            7901: (0, ()),  # first and only round
            HUD_CART_REFERENCE: (expr.Entity(self.payload), ()),
            13555: (round(self.distance / self.length, 3), ()),
            7525: (self.winner, ()),
        }
        states = round_phase_states(phase, hero_select_ready, seconds, self.overtime)
        states.update((4, 95, HUD_OBJECTIVE_KIND))
        if phase in (Phase.ACTIVE, Phase.COMPLETE):
            states.add(HUD_PAYLOAD)
        return ModeState(entity_vars, instance_vars, frozenset(states))

    def progress(self) -> tuple[int, float]:
        goal = min(self.stage, len(self.checkpoints) - 1)
        start = self.checkpoints[goal - 1][0] if goal else 0.0
        return goal, (self.distance - start) / (self.checkpoints[goal][0] - start)

    def payload_state(self) -> PayloadState:
        goal, progress = self.progress()
        direction = int(self.speed > 0) - int(self.speed < 0)
        scripted = self.scripted_movement
        waiting = self.last_update < self.route_wait_until
        impact = self.gate_impact_started
        if impact is not None and (self.phase != Phase.ACTIVE
                                   or self.last_update >= impact + PAYLOAD_GATE_IMPACT_SECONDS):
            impact = None
        return PayloadState(
            self.animation, goal + self.stage_offset, round(progress, 3), self.distance, self.length,
            self.checkpoint_fractions, self.phase == Phase.ACTIVE, self.phase >= Phase.ACTIVE,
            0 if scripted else direction, () if waiting else self.pushers, () if scripted else self.blockers,
            gate_impact_started=impact, checkpoint_offset=self.stage_offset,
        )
