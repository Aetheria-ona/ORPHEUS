"""Send the installed 104319 Athena lines for the current PvP round."""

import math
from dataclasses import dataclass

from ow174.game import escort, voicelines
from ow174.game.content import (
    ATTACK_DEFEND_MODES,
    CONTROL_MODE,
    ESCORT_MODE,
    FIRST_ATTACKING_TEAM,
    HYBRID_MODE,
    SPECTATOR_TEAM,
)
from ow174.game.script import expr, graph
from ow174.game.statescript import InstanceWire, remote_delta
from ow174.game.world import GAME_MODE_ENTITY

MODES = ATTACK_DEFEND_MODES | {CONTROL_MODE}
Phase = escort.Phase

# (078 stimulus, 06F line key) in the mode entity's voice set 001D.05F.
SELECT_HERO = (0x1B3, 0x0646)
PREPARE = ((0x168, 0x05DB), (0x167, 0x05DA))
ATTACK_STARTS = ((0x05D, 0x05F2), (0x06B, 0x05F1))
PAYLOAD = ((0x19B, 0x0620), (0x194, 0x0619))
OBJECTIVE_A = ((0x17B, 0x05F8), (0x178, 0x05F5))
OBJECTIVE_B = ((0x17C, 0x05FA), (0x179, 0x05F6))
POINT_A_TAKEN = ((0x061, 0x0613), (0x060, 0x060D))
CHECKPOINT = (0x198, 0x061D)
NEAR_FINISH = (0x199, 0x061C)
OVERTIME = (0x066, 0x0625)
RESULT = ((0x19F, 0x0628), (0x19E, 0x0627))
MATCH_COMPLETE = (0x6C2, 0x8B45)
PREPARE_FOR_BATTLE = (0x70B, 0x8D9D)
CONTROL_START = (0x4FA, 0x7B53)
CONTROL_ROUNDS = (CONTROL_START, (0x4FB, 0x7B54), (0x4FC, 0x7B55))
CONTROL_UNLOCKED = (0x464, 0x81F0)
CONTROL_ROUND_RESULT = (
    ((0x46A, 0x77BC), (0x469, 0x77BE)),
    ((0x47C, 0x7BEA), (0x47A, 0x7BEC)),
    ((0x5C4, 0x8244), (0x5C7, 0x8247)),
)
CONTROL_SCORE = (0x63B, 0x8447)
# 03F2 uses separate recordings for the leading number and the "to N" number.
CONTROL_SCORE_FIRST = ((0x61B, 0x8427), (0x5FD, 0x8408), (0x5FE, 0x840A))
CONTROL_SCORE_SECOND = ((0x61C, 0x8431), (0x61D, 0x8426), (0x61E, 0x842A))
SETUP_CLOCK = {
    60: ((0x16A, 0x05DE), (0x169, 0x05DD)),
    30: ((0x16C, 0x05E0), (0x16B, 0x05DF)),
}
ROUND_CLOCK = {60: (0x19D, 0x0622), 30: (0x067, 0x0623), 10: (0x068, 0x0624)}
NUMBERS = {
    5: (0x057, 0x05EC), 4: (0x058, 0x05EB), 3: (0x059, 0x05EA),
    2: (0x05B, 0x05E9), 1: (0x05C, 0x05E7),
}

Cue = tuple[int, int]

FOLLOW_UP_GAP = 2.0
CAPTURE_FOLLOW_UP_GAP = 3.5  # 060.078 lasts 3.016 s in zhCN.
SCORE_FOLLOW_UP_GAP = 1.5


@dataclass(frozen=True)
class Timeline:
    phase: int
    seconds: int
    stage: int = 0
    overtime: bool = False
    winner: int | None = None
    near_finish: bool = False
    control_wins: tuple[int, int] | None = None
    round_winner: int | None = None


def speech(
    previous: Timeline | None, current: Timeline, mode: int, team: int, *, single_countdown: bool,
) -> tuple[Cue, ...]:
    """Return the new cues in their playback order at this snapshot."""
    phase, seconds = current.phase, current.seconds
    attacks = team == FIRST_ATTACKING_TEAM
    spectator = team == SPECTATOR_TEAM
    if phase == Phase.ASSEMBLE and (previous is None or previous.phase == Phase.WAITING):
        if spectator or seconds <= 2:
            return ()
        prepare = PREPARE_FOR_BATTLE if mode == CONTROL_MODE else PREPARE[attacks]
        return prepare, SELECT_HERO
    if previous is None:
        return ()  # The first in-progress snapshot never replays a phase transition.
    if mode == CONTROL_MODE and not single_countdown:
        if (phase == Phase.ROUND_END and previous.phase == Phase.ACTIVE
                and current.stage == previous.stage and current.round_winner is not None):
            return (CONTROL_ROUND_RESULT[current.stage][spectator or team == current.round_winner],)
        if phase == Phase.COMPLETE and previous.phase != phase:
            if current.winner is not None:
                return (MATCH_COMPLETE if spectator else RESULT[team == current.winner],)
            if current.control_wins is not None:
                first, second = current.control_wins
                if team == FIRST_ATTACKING_TEAM:
                    first, second = second, first
                return CONTROL_SCORE, CONTROL_SCORE_FIRST[first], CONTROL_SCORE_SECOND[second]
        if current.overtime and not previous.overtime:
            return (OVERTIME,)
        if (phase == previous.phase == Phase.SETUP and current.stage == previous.stage
                and seconds < previous.seconds and seconds in NUMBERS):
            return (NUMBERS[seconds],)
        if (phase == Phase.UNLOCK and seconds > 5
                and (previous.phase != phase or previous.stage != current.stage)):
            return (CONTROL_ROUNDS[current.stage],)
        if phase == Phase.ACTIVE and previous.phase == Phase.UNLOCK and current.stage == previous.stage:
            return (CONTROL_UNLOCKED,)
        return ()
    if phase == Phase.COMPLETE and previous.phase != Phase.COMPLETE:
        return (MATCH_COMPLETE if spectator else RESULT[team == current.winner],)
    finish = (NEAR_FINISH,) if current.near_finish and not previous.near_finish else ()
    if current.overtime and (not previous.overtime or previous.stage != current.stage):
        return (OVERTIME, *finish)
    if phase == Phase.ACTIVE and previous.phase in (Phase.ASSEMBLE, Phase.SETUP):
        if spectator or current.stage:
            return ()
        if mode == CONTROL_MODE:
            return (CONTROL_START,)
        objective = PAYLOAD if mode == ESCORT_MODE else OBJECTIVE_A
        return ATTACK_STARTS[attacks], objective[attacks]
    if phase == Phase.ACTIVE and previous.phase == phase:
        if previous.stage < current.stage:
            if mode == HYBRID_MODE and previous.stage == 0:
                if not spectator:
                    return POINT_A_TAKEN[attacks], PAYLOAD[attacks]
                return ()
            if mode in (ESCORT_MODE, HYBRID_MODE):
                return (CHECKPOINT, *finish)
            if not spectator:
                return POINT_A_TAKEN[attacks], OBJECTIVE_B[attacks]
        if finish:
            return finish
        if not current.overtime and previous.stage == current.stage and seconds < previous.seconds:
            cue = ROUND_CLOCK.get(seconds)
            return (cue,) if cue is not None else ()
    if phase == previous.phase and seconds < previous.seconds:
        if phase == Phase.SETUP:
            cue = NUMBERS.get(seconds)
            if not spectator and seconds in SETUP_CLOCK:
                cue = SETUP_CLOCK[seconds][attacks]
            return (cue,) if cue is not None else ()
        if phase == Phase.ASSEMBLE and single_countdown:
            cue = NUMBERS.get(seconds)
            return (cue,) if cue is not None else ()
    return ()


@dataclass
class FollowUp:
    ordinal: int
    delay: float
    team: int
    cue: Cue
    due: float | None = None
    rest: tuple[Cue, ...] = ()


@dataclass
class Playback:
    previous: Timeline | None = None
    follow_up: FollowUp | None = None

    def update(self, viewer, now: float, current: Timeline, *, single_countdown: bool) -> None:
        previous = self.previous
        self.previous = current
        if previous is None or (previous.phase, previous.stage) != (current.phase, current.stage):
            self.follow_up = None
        cues = speech(previous, current, viewer.match.game_map.mode_guid, viewer.team,
                      single_countdown=single_countdown)
        if cues:
            ordinal = self._send(viewer, cues[0])
            self.follow_up = None
            if len(cues) > 1 and ordinal is not None:
                delay = CAPTURE_FOLLOW_UP_GAP if cues[0] in POINT_A_TAKEN else FOLLOW_UP_GAP
                if cues[0] == CONTROL_SCORE:
                    delay = SCORE_FOLLOW_UP_GAP
                self.follow_up = FollowUp(ordinal, delay, viewer.team, cues[1], rest=cues[2:])
        elif (pending := self.follow_up) is not None:
            if viewer.team != pending.team or (pending.cue == NEAR_FINISH and not current.near_finish):
                self.follow_up = None
            elif pending.due is None:
                if viewer.client.reliable_delivered(pending.ordinal):
                    pending.due = now + pending.delay
            elif now >= pending.due:
                self.follow_up = None
                ordinal = self._send(viewer, pending.cue)
                if ordinal is not None and pending.rest:
                    self.follow_up = FollowUp(
                        ordinal, pending.delay, pending.team, pending.rest[0], rest=pending.rest[1:],
                    )

    @staticmethod
    def _send(viewer, cue: Cue) -> int | None:
        stimulus, line = cue
        return voicelines.queue_voice_notice(
            viewer, GAME_MODE_ENTITY, 0x0EE0000000000000 | stimulus, 0x0760000000000000 | line,
            anchor=None, listener=viewer.body if viewer.has_body else 0xFFFFFFFF,
        )


# 0D3B plays these after Control's 05E2 round-result message.
CHECKPOINT_EFFECTS = (0x0300000000000D4C, 0x0300000000000B45)  # losing / winning team
CAPTURE_SOUND_GRAPH = 0x224E
CAPTURE_SOUND_ACTION = 127
CAPTURE_SOUND_INSTANCE = 0x100
CAPTURE_EFFECT_VAR = 0x6EDE


def sound_effect_instance(effect: int) -> InstanceWire | None:
    definition = graph.graph(CAPTURE_SOUND_GRAPH)
    if definition is None:
        return None
    return InstanceWire(CAPTURE_SOUND_INSTANCE, definition, descriptor=True,
                        vars={CAPTURE_EFFECT_VAR: (expr.Asset(effect), ())})


def capture_sound_frame(stamp: int, team: int):
    effect = CHECKPOINT_EFFECTS[team == FIRST_ATTACKING_TEAM]
    instance = sound_effect_instance(effect)
    if instance is None:
        return None
    return remote_delta([instance], {}, {instance.id: instance.graph},
                        callout=(stamp, instance.id, CAPTURE_SOUND_ACTION))


# Positions in 0F4A.m_graph.m_items, used by the native client-only music
# graph 1B47 and the 2056 countdown presenter.
ASSEMBLY_MUSIC = 469  # 0267 at 10 seconds left in assembly
SETUP_MUSIC = 470     # 0D0A at 15 seconds left in setup
OVERTIME_MUSIC = 471  # 0D0F
FINAL_MUSIC = 472     # 0D10 at 30 seconds left in live play
END_OVERTIME_MUSIC = 473  # 0D11
COUNTDOWN_ACTIONS = {5: 529, 4: 530, 3: 531, 2: 532, 1: 533}


def mode_action(previous: escort.ModeState, current: escort.ModeState) -> int | None:
    """Select the callout for the delta from the last sent mode snapshot."""
    phase = current.phase
    if escort.OVERTIME_ACTIVE_STATE in previous.states and escort.OVERTIME_ACTIVE_STATE not in current.states:
        return END_OVERTIME_MUSIC
    if escort.OVERTIME_ACTIVE_STATE in current.states and escort.OVERTIME_ACTIVE_STATE not in previous.states:
        return OVERTIME_MUSIC
    if previous.phase != phase or previous.stage != current.stage:
        return None
    before, remaining = previous.seconds, current.seconds
    if not 0 < remaining < before:
        return None
    if phase == Phase.ASSEMBLE and remaining <= 10 < before:
        return ASSEMBLY_MUSIC
    if phase == Phase.SETUP:
        second = math.ceil(remaining)
        if second in COUNTDOWN_ACTIONS and second < math.ceil(before):
            return COUNTDOWN_ACTIONS[second]
        if 5 < remaining <= 15 < before:
            return SETUP_MUSIC
    if phase == Phase.ACTIVE and remaining <= 30 < before:
        return FINAL_MUSIC
    return None
