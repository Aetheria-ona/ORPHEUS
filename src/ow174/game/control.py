"""The three Control submaps and their native 104319 mode and objective streams."""

import math
from dataclasses import dataclass
from enum import IntEnum

from ow174.game import assault, combat, escort
from ow174.game.content import CONTROL_MODE, ControlMap
from ow174.game.script import expr, graph, rulesets
from ow174.game.statescript import InstanceWire, StateWire, remote_delta, remote_full_frame

Phase = escort.Phase
TEAMS = (0, 1)
NEUTRAL = -1  # STU TeamIndex xBC219E96
MODE_GRAPH = 0x0EC2
MODE_HUD = 0x1FEE  # 0EC2's client-only state 162 creates 1FEE and its common HUD
ROUND_RESULT = 0x0C35
MATCH_RESULT_INTRO = 0x027F
MATCH_RESULT_INTRO_INSTANCE = 6
MATCH_RESULT_INTRO_PULSE = 9
MATCH_RESULT_HUD_OFF = 42  # 0CBB sends 0717 to the shared and hero HUDs.
CAPTURE_EFFECT = 232  # m_items index, not m_nodes or m_states
UNLOCK_EFFECT = 233  # 188C.00D, triggered by 0EC2 state 91 at three seconds
UNLOCK_EFFECT_SECONDS = 3.0
ASSEMBLY_MUSIC = 412  # 0267, consumed by Control's local 1B5C music graph
SETUP_MUSIC_EFFECT = 0x030000000000294A  # Shared preparation cue selected by 1B47
MUSIC_SECONDS = 10
SETUP_COUNTDOWN = {5: 452, 4: 451, 3: 450, 2: 449, 1: 448}  # 0FD3 to the native countdown HUD
UNLOCK_CLOCK_VISIBLE = 0x27AC  # 1FEE state 25, separate from the point's own lock
OBJECTIVE_HUD_LIVE = 84  # 0694 keeps 1FEE's point-letter presenter active.
SCORE_EFFECTS = (316, 317)  # 188A/188B.00D, keyed by the owning team's score
SCORE_SOUND_SCOPE = 176  # registers 5A2/5A3.013 for the score sounds
SCORE_SOUND_START = 75
POINT_KIND = expr.Asset(0x0D80000000002FDB)
POINT_MARKER = expr.Asset(0x00302FDB)  # 030.114 / 2FDB.01C, selects 0E8E state 65
SETUP_SECONDS = 30.0  # 0EC2 item 425
UNLOCK_SECONDS = 30.0  # 0EC2 item 117
ROUND_END_SCALE = 0.375  # 0EC2 item 66 supplies 22FC to state 59
ROUND_END_SLOW_MOTION = 59
MATCH_RESULT_MUSIC = 98  # 06E0/06E1 belongs to the match result.
ROUND_RESULT_CUE = 158  # 05E2, sent only when neither team has won the match.
INTERMISSION_MUSIC = 408  # 0D1A selects the inter-round cue in 1B5C.
ROUND_SUMMARY_DELAY = 7.0
INTERMISSION_SECONDS = 8.0
CAPTURE_RATES = (0.125, 0.1875, 0.25)  # 0EC2 state 110, 079E.025 parameters 0420/0421
DECAY_DELAY = 5.0
OVERTIME_TOUCH = 3.0
OVERTIME_GRACE = 5.0
OVERTIME_MIN_GRACE = 0.3
OVERTIME_DECAY = 15.0
OVERTIME_SCORE = 99.99
SPAWN_HEAL_PER_SECOND = 400.0  # 0EC2 sends 0724 to 0D62; 0723 enables 0C8E state 13 on the pawn.


class ControlPhase(IntEnum):
    # 1FEE displays "Activating Objective..." on entry to native phase 3.
    WAITING = 0
    ASSEMBLE = 1
    SETUP = 2
    ACTIVE = 3
    ROUND_RESULT = 4
    RESULT = 5


def _vars(values: dict) -> dict:
    return {key: (value, ()) for key, value in values.items()}


def _states(definition, indexes, active):
    return {index: StateWire(definition.state(index).cls, index in active) for index in indexes}


def _changes(current, previous):
    return {key: value for key, value in current.items() if previous.get(key) != value}


@dataclass(frozen=True)
class ModeState:
    phase: Phase
    stage: int
    round_index: int
    owner: int
    entity_vars: dict
    instance_vars: dict
    states: frozenset[int]
    receivers: frozenset[int]
    round_result: dict | None = None

    @property
    def seconds(self) -> float:
        return self.instance_vars[0x0517][0]

    def setup_music_from(self, previous: "ModeState") -> bool:
        return (self.phase == previous.phase == Phase.SETUP
                and self.round_index == previous.round_index
                and 5 < self.seconds <= MUSIC_SECONDS < previous.seconds)

    def action_from(self, previous: "ModeState") -> int | None:
        if self.round_index != previous.round_index:
            return None
        if (self.phase == previous.phase == Phase.ASSEMBLE
                and 5 < self.seconds <= MUSIC_SECONDS < previous.seconds):
            return ASSEMBLY_MUSIC
        if self.phase == previous.phase == Phase.SETUP:
            second = math.ceil(self.seconds)
            if second < math.ceil(previous.seconds):
                return SETUP_COUNTDOWN.get(second)
        if (self.phase == previous.phase == Phase.UNLOCK
                and self.seconds == UNLOCK_EFFECT_SECONDS < previous.seconds):
            return UNLOCK_EFFECT
        if self.owner not in TEAMS:
            return None
        if self.phase == previous.phase == Phase.ACTIVE and self.owner != previous.owner:
            return CAPTURE_EFFECT
        if previous.phase == Phase.ACTIVE and self.phase in (Phase.ACTIVE, Phase.ROUND_END):
            key = 0x1330 + self.owner
            score = int(self.instance_vars[key][0])
            if score >= SCORE_SOUND_START and score > int(previous.instance_vars[key][0]):
                return SCORE_EFFECTS[self.owner]
        return None

    def round_actions_from(self, previous: "ModeState") -> tuple[int, ...]:
        if (self.phase == Phase.ROUND_END and previous.phase == Phase.ACTIVE
                and self.round_index == previous.round_index):
            return ROUND_RESULT_CUE, INTERMISSION_MUSIC
        return ()

    def _match_result_intro(self, previous: "ModeState | None") -> InstanceWire | None:
        if self.instance_vars[0x1D65][0] is None:
            return None
        if previous is not None and previous.instance_vars[0x1D65][0] is not None:
            return None
        definition = graph.graph(MATCH_RESULT_INTRO)
        if definition is None:
            return None
        blue, red = self.instance_vars[0x09C3][0]
        pulse = definition.state(MATCH_RESULT_INTRO_PULSE)
        return InstanceWire(
            MATCH_RESULT_INTRO_INSTANCE, definition, descriptor=True,
            vars=_vars({
                0x0918: expr.Map(((0x1330, blue), (0x1331, red))),
                0x35D8: expr.Map(((0x1C8E, 2.5),)),  # 180A's victory/defeat phase parameters
            }),
            states={pulse.state: StateWire(pulse.cls, True, {"fresh": previous is not None, "count": 1})},
        )

    def frame(self, previous: "ModeState | None", *, callout=None, round_callouts=(),
              setup_music: InstanceWire | None = None, removed_instance: int | None = None,
              command_frame: int = 0):
        mode = graph.graph(MODE_GRAPH)
        receiver = graph.graph(escort.MODE_MESSAGES)
        hud_values = graph.graph(escort.MODE_HUD_VALUES)
        graphs = {1: mode, 2: receiver, 5: hud_values}
        instances = []
        if (intro := self._match_result_intro(previous)) is not None:
            instances.append(intro)
            graphs[intro.id] = intro.graph
        changed = self.states if previous is None else self.states ^ previous.states
        mode_states = _states(mode, changed, self.states)
        if (slow_motion := mode_states.get(ROUND_END_SLOW_MOTION)) is not None:
            slow_motion.payload = {"which": slow_motion.active, "value": command_frame}
        if (music := mode_states.get(MATCH_RESULT_MUSIC)) is not None and music.active:
            # Seed a baseline without replaying the transition for a late viewer.
            music.payload = {"fresh": previous is not None, "count": 1}
        if previous is None:
            receiver_states = _states(receiver, self.receivers, self.receivers)
            instances.extend((
                InstanceWire(1, mode, vars=self.instance_vars, states=mode_states),
                InstanceWire(2, receiver, parent=(1, 12), states=receiver_states),
                InstanceWire(5, hud_values, parent=(2, 50), states=_states(hud_values, (0,), {0})),
            ))
            if self.phase == Phase.WAITING:
                hud = combat.waiting_hud(3)
                instances.append(hud)
                graphs[3] = hud.graph
            if self.round_result is not None and (result := graph.graph(ROUND_RESULT)) is not None:
                instances.append(InstanceWire(4, result, parent=(1, 86), vars=self.round_result))
                graphs[4] = result
            return remote_full_frame(instances, self.entity_vars, graphs)
        instances.append(InstanceWire(
            1, mode, vars=_changes(self.instance_vars, previous.instance_vars),
            states=mode_states,
        ))
        if self.receivers != previous.receivers:
            instances.append(InstanceWire(
                2, receiver, states=_states(receiver, self.receivers ^ previous.receivers, self.receivers),
            ))
        if previous.phase == Phase.WAITING and self.phase != Phase.WAITING:
            instances.append(InstanceWire(3, None, gone=True))
        if setup_music is not None:
            instances.append(setup_music)
            graphs[setup_music.id] = setup_music.graph
        if removed_instance is not None:
            instances.append(InstanceWire(removed_instance, None, gone=True))
        if (self.round_result is None) != (previous.round_result is None):
            if self.round_result is None:
                instances.append(InstanceWire(4, None, gone=True))
            else:
                result = graph.graph(ROUND_RESULT)
                instances.append(InstanceWire(
                    4, result, parent=(1, 86), vars=self.round_result, descriptor=True,
                ))
                graphs[4] = result
        entity_vars = _changes(self.entity_vars, previous.entity_vars)
        return remote_delta(instances, entity_vars, graphs, callout=callout, callouts=round_callouts)


class ControlRound(escort.ObjectiveRooms):
    def __init__(self, scene: ControlMap, order: tuple[int, ...] = (0, 1, 2)) -> None:
        super().__init__(scene.room_placeables)
        self.points = scene.points
        self.entities = tuple(escort.MAP_ENTITY | point.placeable for point in self.points)
        self.outlines = {escort.MAP_ENTITY | outline: stage
                         for stage, point in enumerate(self.points) for outline in point.outlines}
        self.healing_rooms = {(stage, team): boxes for stage, team, boxes in scene.healing_rooms}
        self.phase = Phase.ASSEMBLE
        self.order = order
        self.round_index = 0
        self.stage = order[0]
        self.deadline = 0.0
        self.last_update = 0.0
        self.points_to_win = scene.points_to_win or rulesets.default(0x2295, 0, CONTROL_MODE)
        self.capture_scale = scene.capture_scale
        self.score_rate = (100 / 120) * scene.score_scale
        self.round_wins = [0, 0]
        self.winner: int | None = None
        self.round_winner: int | None = None
        self._reset_point()

    def _reset_point(self) -> None:
        self.owner = NEUTRAL
        self.capturer = NEUTRAL
        self.progress = 0.0
        self.last_capture = 0.0
        self.occupants = ((), ())
        self.scores = [0.0, 0.0]
        self.last_enemy_touch: list[float | None] = [None, None]
        self.overtime_started: float | None = None
        self.overtime_end = 0.0

    @property
    def overtime(self) -> bool:
        return self.overtime_started is not None

    @property
    def contested(self) -> bool:
        return bool(all(self.occupants))

    @property
    def movement_scale(self) -> float:
        return ROUND_END_SCALE if self.phase == Phase.ROUND_END else 1.0

    @property
    def capturing(self) -> bool:
        return self.capturer in TEAMS and bool(self.occupants[self.capturer]) and not self.contested

    def start_setup(self, now: float) -> None:
        self.phase = Phase.SETUP
        self.deadline = now + SETUP_SECONDS
        self._update_room_collision()

    def next_round(self, now: float) -> None:
        self.round_index += 1
        self.stage = self.order[self.round_index % len(self.order)]
        self.round_winner = None
        self._reset_point()
        self.start_setup(now)

    def door_open(self, entity: int) -> bool:
        stage = min(stage for stage, _ in self.doors[entity].rooms)
        opened = self.phase in (Phase.UNLOCK, Phase.ACTIVE, Phase.ROUND_END, Phase.COMPLETE)
        return opened if stage == self.stage else stage in self.order[:self.round_index]

    def in_healing_room(self, team: int, position: tuple[float, float, float]) -> bool:
        return self.phase not in (Phase.WAITING, Phase.COMPLETE) and assault.inside_boxes(
            self.healing_rooms.get((self.stage, team), ()), position,
        )

    def advance(self, now: float, participants) -> None:
        if self.phase == Phase.ROUND_END and now >= self.deadline:
            self.phase = Phase.COMPLETE
            self.deadline += INTERMISSION_SECONDS
            self._update_room_collision()
        if self.phase == Phase.SETUP and now >= self.deadline:
            self.phase = Phase.UNLOCK
            self.deadline += UNLOCK_SECONDS
            self._update_room_collision()
        if self.phase == Phase.UNLOCK and now >= self.deadline:
            self.phase = Phase.ACTIVE
            self.last_update = self.deadline
        if self.phase != Phase.ACTIVE:
            return
        elapsed = max(0.0, now - self.last_update)
        previous_update = self.last_update
        self.last_update = now
        occupants = ([], [])
        for body, team, position in participants:
            if team in TEAMS and assault.contains(self.points[self.stage], position):
                occupants[team].append(expr.Entity(body))
        self.occupants = tuple(tuple(team) for team in occupants)
        if self.owner in TEAMS:
            self._score(now, elapsed, previous_update)
        if self.phase == Phase.ACTIVE:
            self._capture(now, elapsed)

    def _score(self, now: float, elapsed: float, previous_update: float) -> None:
        owner = self.owner
        enemy_present = bool(self.occupants[1 - owner])
        last_touch = self.last_enemy_touch[owner]
        if enemy_present:
            self.last_enemy_touch[owner] = now
        if self.overtime:
            if enemy_present and (now < self.overtime_end or last_touch == previous_update):
                decay = (now - self.overtime_started) / OVERTIME_DECAY
                spread = OVERTIME_GRACE - OVERTIME_MIN_GRACE
                grace = max(OVERTIME_MIN_GRACE, OVERTIME_GRACE - decay * spread)
                self.overtime_end = now + grace
            elif now >= self.overtime_end:
                self._finish_round(owner, now)
            return
        score = self.scores[owner] + elapsed * self.score_rate
        self.scores[owner] = min(100.0, score)
        if score >= 100.0 - 1e-7:
            reached_at = now - max(0, score - 100) / self.score_rate
            if enemy_present or (last_touch is not None and reached_at - last_touch <= OVERTIME_TOUCH):
                self.scores[owner] = OVERTIME_SCORE
                self.overtime_started = now
                self.overtime_end = now + OVERTIME_GRACE
            else:
                self._finish_round(owner, now)

    def _capture(self, now: float, elapsed: float) -> None:
        if self.contested:
            self.last_capture = now
            return
        team = next((team for team in TEAMS if self.occupants[team]), NEUTRAL)
        if team == NEUTRAL or team == self.owner:
            decay = min(elapsed, max(0.0, now - self.last_capture - DECAY_DELAY))
            self.progress = max(0.0, self.progress - decay * CAPTURE_RATES[0] * self.capture_scale)
            if self.progress == 0.0:
                self.capturer = NEUTRAL
            return
        if self.capturer != team:
            self.capturer = team
            self.progress = 0.0
        self.last_capture = now
        tier = min(len(self.occupants[team]), len(CAPTURE_RATES)) - 1
        rate = CAPTURE_RATES[tier] * self.capture_scale
        self.progress += elapsed * rate
        if self.progress >= 1.0 - 1e-7:
            self.owner = team
            self.capturer = NEUTRAL
            self.progress = 0.0
            self.overtime_started = None
            self.overtime_end = 0.0
            self.last_enemy_touch[team] = None

    def _finish_round(self, team: int, now: float) -> None:
        self.round_wins[team] += 1
        self.scores[team] = 100.0
        self.round_winner = team
        self.phase = Phase.ROUND_END
        self.deadline = now + ROUND_SUMMARY_DELAY
        self.overtime_started = None
        self.overtime_end = 0.0
        self.occupants = ((), ())
        self.capturer = NEUTRAL
        self.progress = 0.0
        if self.round_wins[team] >= self.points_to_win:
            self.winner = team
            self.phase = Phase.COMPLETE
            self.deadline = now
        self._update_room_collision()

    def point_state(self, index: int) -> assault.PointState:
        point = self.points[index]
        active = index == self.stage and self.phase == Phase.ACTIVE
        marker = index == self.stage and self.phase in (Phase.SETUP, Phase.UNLOCK, Phase.ACTIVE)
        blue, red = self.occupants if active else ((), ())
        capturer = self.capturer if active else NEUTRAL
        attacking = (blue, red)[capturer] if capturer in TEAMS else ()
        defending = (red, blue)[capturer] if capturer in TEAMS else ()
        entity = {
            0x0062: capturer, 0x0063: self.owner if index == self.stage else NEUTRAL,
            0x266C: blue, 0x266D: red, 0x2688: len(attacking), 0x2689: len(defending),
            0x268A: expr.f32(self.progress) if active else 0.0,
            0x2690: active and self.contested, 0x2694: active and self.capturing,
            0x2DA4: marker, escort.OBJECTIVE_KIND: POINT_KIND, escort.CHECKPOINT_INDEX: index,
            0x1DDB: expr.Asset(point.submap_name) if point.submap_name else None,
        }
        instance = {
            0x06D9: chr(ord("A") + index), 0x1BE2: expr.Vec3(*point.position),
            0x1DDB: entity[0x1DDB], 0x2677: True, 0x2696: NEUTRAL,
            assault.POINT_COMPLETED: False, assault.POINT_LOCKED: not active, 0x2F61: 0,
            assault.POINT_WAYPOINT_TYPE: POINT_MARKER if marker else None,
        }
        return assault.PointState(_vars(entity), _vars(instance))

    def point_descriptor(self, entity: int):
        return remote_full_frame([], self.point_state(self.entities.index(entity)).entity_vars, {})

    def _banner_scores(self, viewer_team: int, previous: ModeState | None) -> tuple[int, int]:
        if previous is None:
            return tuple(self.round_wins[::-1] if viewer_team == 1 else self.round_wins)
        friendly, opposing = (previous.entity_vars[key][0] for key in (0x1846, 0x1847))
        if self.phase != Phase.ROUND_END or previous.phase == Phase.ROUND_END:
            return friendly, opposing
        lost = viewer_team in TEAMS and viewer_team != self.round_winner
        before = opposing if lost else friendly
        score = self.round_wins[self.round_winner]
        # 1FEE only shows a prompt when its watched value changes and is below the win limit.
        # These fields carry the prompt; 09C3 still carries the actual score.
        if score == before:
            score = 0 if before == -1 else -1
        return (friendly, score) if lost else (score, opposing)

    def mode_state(
        self, now: float, assembly_end: float | None, *, hero_select_ready: bool, viewer_team: int = 0,
        previous: ModeState | None = None,
    ) -> ModeState:
        phase = Phase.WAITING if self.phase == Phase.ASSEMBLE and assembly_end is None else self.phase
        cinematic = phase == Phase.ROUND_END
        match_winner = self.winner if phase == Phase.COMPLETE else None
        intermission = phase == Phase.COMPLETE and match_winner is None
        # 0EC2 state 165 copies 1332 into the 1FEE team fill selector (5AF0).
        hud_owner = NEUTRAL if cinematic or intermission else self.owner
        native = {
            Phase.WAITING: ControlPhase.WAITING, Phase.ASSEMBLE: ControlPhase.ASSEMBLE,
            Phase.SETUP: ControlPhase.SETUP, Phase.UNLOCK: ControlPhase.ACTIVE,
            Phase.ACTIVE: ControlPhase.ACTIVE, Phase.ROUND_END: ControlPhase.ROUND_RESULT,
            Phase.COMPLETE: ControlPhase.RESULT if match_winner is not None else ControlPhase.ROUND_RESULT,
        }[phase]
        clock = assembly_end if phase == Phase.ASSEMBLE else now
        if phase in (Phase.SETUP, Phase.UNLOCK):
            clock = self.deadline
        seconds = escort.hud_clock_seconds(clock - now)
        grace = math.ceil(max(0.0, self.overtime_end - now) * 4) / 4 if self.overtime else 0.0
        unlock_clock = phase == Phase.UNLOCK
        scores = tuple(expr.f32(score if score == OVERTIME_SCORE else math.floor(score * 4) / 4)
                       for score in self.scores)
        if match_winner is not None:
            # The result uses round wins. Hold the last objective HUD values until it is hidden.
            if previous is not None and previous.round_index == self.round_index:
                scores = tuple(previous.instance_vars[key][0] for key in (0x1330, 0x1331))
            else:
                scores = tuple(min(score, 99.0) for score in scores)
        progress = scores[self.owner] / 100 if self.owner in TEAMS else 0.0
        wins = tuple(self.round_wins)
        friendly, opposing = self._banner_scores(viewer_team, previous)
        point = self.points[self.stage]
        goal = expr.Entity(self.entities[self.stage])
        submap = expr.Asset(point.submap_name) if point.submap_name else None
        heading = {Phase.WAITING: 0x5D4B, Phase.ASSEMBLE: 0x5D4C, Phase.SETUP: 0x5D4D}.get(phase)
        entity = {
            59: int(native), 0x0783: expr.Asset(0x0DE0000000000000 | heading) if heading else None,
            0x09C3: wins,
            0x0D40: self.overtime, 0x1332: hud_owner, 0x13E4: wins[0], 0x13E5: wins[1],
            0x1846: friendly, 0x1847: opposing, 0x2295: self.points_to_win,
            0x1B3B: grace, 0x1D65: match_winner, 0x1EDD: sum(wins), 0x26AD: goal,
            UNLOCK_CLOCK_VISIBLE: unlock_clock,
            escort.CHECKPOINT_INDEX: self.stage, 0x349C: progress, 0x34F3: progress,
            0x4C73: scores, 0x4C49: phase == Phase.WAITING, 0x4C48: phase == Phase.ASSEMBLE,
            0x2F51: phase == Phase.SETUP,
            0x4C47: phase in (Phase.UNLOCK, Phase.ACTIVE, Phase.ROUND_END) or intermission,
            0x4C46: match_winner is not None,
        }
        if phase != Phase.WAITING:
            entity[escort.COUNTDOWN] = seconds
        instance = {
            59: int(native), 0x0517: seconds, 0x09C3: wins, 0x0D40: self.overtime,
            0x1330: scores[0], 0x1331: scores[1], 0x1332: hud_owner, 0x13E4: wins[0], 0x13E5: wins[1],
            0x1B3B: grace, 0x1D65: match_winner,
            0x1DDB: submap, 0x1E25: OVERTIME_GRACE, 0x22FC: ROUND_END_SCALE,
            0x1E4F: self.round_winner, 0x1EDD: sum(wins), 0x2295: self.points_to_win,
            0x26AD: goal, 0x27A2: self.stage, 0x27B4: tuple(map(expr.Entity, self.entities)),
            0x349C: progress,
        }
        states, phase_receivers = {
            Phase.WAITING: ((4, 5), (12, 51)),
            Phase.ASSEMBLE: ((8, 10, 11), (1, 52)) if hero_select_ready else ((), ()),
            Phase.SETUP: ((14, 15), (10, 53)),
            Phase.UNLOCK: ((OBJECTIVE_HUD_LIVE, 85, 109, 149), (2, 54)),
            Phase.ACTIVE: ((OBJECTIVE_HUD_LIVE, 85, 109, 149, SCORE_SOUND_SCOPE), (2, 54)),
            Phase.ROUND_END: ((OBJECTIVE_HUD_LIVE, 85, 109, 149, SCORE_SOUND_SCOPE), (2, 54)),
            Phase.COMPLETE: ((85, 109, SCORE_SOUND_SCOPE), ()),
        }[phase]
        if match_winner is not None:
            states += (21, MATCH_RESULT_MUSIC)
            phase_receivers = (3, 55, MATCH_RESULT_HUD_OFF)
        if intermission:
            states += (OBJECTIVE_HUD_LIVE,)
            phase_receivers = (2, 54)
        if cinematic:
            states += (ROUND_END_SLOW_MOTION,)
        if self.overtime:
            states += (80,)  # 0EC2 asserts overtime and starts its native overtime scope.
        result = None
        if intermission and graph.graph(ROUND_RESULT) is not None:
            result = _vars({
                0x0896: False, 0x1330: wins[0], 0x1331: wins[1], 0x1D65: self.round_winner,
                0x1DC3: self.points_to_win * 2 - 1, 0x1EDD: sum(wins), 0x1EB0: False, 0x1EB2: False,
                0x1F3D: int(self.round_winner == 0), 0x1F3E: int(self.round_winner == 1),
                0x1F42: True, 0x1F43: True, 0x1F80: True, 0x1F8D: submap,
            })
        return ModeState(phase, self.stage, self.round_index, self.owner, _vars(entity), _vars(instance),
                         frozenset(states), frozenset((0, 4, 5, 9, 11, 56, *phase_receivers)), result)
