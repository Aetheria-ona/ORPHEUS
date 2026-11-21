"""One custom Hybrid round, from point A to the end of the payload route."""

from dataclasses import replace

from ow174.game import assault, escort
from ow174.game.content import AssaultMap, HybridMap
from ow174.game.script import expr


class HybridRound(escort.EscortRound):
    stage_offset = 1

    def __init__(self, scene: HybridMap, *, speed_scale: float = 1.0, capture_scale: float = 1.0) -> None:
        super().__init__(scene.route, auto_drive=scene.auto_drive, speed_scale=speed_scale)
        self.capture = assault.AssaultRound(AssaultMap((scene.point,), (), ()), capture_scale=capture_scale)
        objectives = (*self.capture.entities, *(entity for _, entity in self.checkpoints))
        self.goals = tuple(map(expr.Entity, objectives))
        self.unlock_delay = scene.unlock_delay
        self.unlock_seconds = scene.unlock_seconds
        self.speed_multiplier = scene.speed_multiplier
        self.unlock_at = 0.0
        self.move_at = 0.0

    @property
    def room_stage(self) -> int:
        return self.capture.captured + self.stage

    def start_setup(self, now: float) -> None:
        super().start_setup(now)
        self.capture.start_setup(now)

    def door_open(self, entity: int) -> bool:
        if self.doors[entity].payload_unlock and entity not in self.route_gate_commands:
            return bool(self.capture.captured and self.last_update >= self.unlock_at)
        return super().door_open(entity)

    def advance(self, now: float, participants) -> None:
        if self.capture.captured:
            previous = self.last_update
            self.last_update = max(previous, min(now, self.move_at))
            super().advance(now, participants if now >= self.move_at else ())
            if previous < self.unlock_at <= now:
                self._update_room_collision()
            return

        previous = self.phase, self.room_stage
        self.capture.advance(now, participants)
        if self.capture.captured:
            # 0F4A advances the objective and adds the next objective's time to
            # the round clock. Time spent on A never moves the cart.
            self.deadline = max(now, self.capture.deadline) + self.stage_seconds[0]
            self.phase = escort.Phase.ACTIVE
            self.last_update = self.last_attacker = now
            self.overtime = False
            self.unlock_at = now + self.unlock_delay
            self.move_at = self.unlock_at + self.unlock_seconds
        else:
            self.phase = self.capture.phase
            self.deadline = self.capture.deadline
            self.overtime = self.capture.overtime
            self.winner = self.capture.winner
        if previous != (self.phase, self.room_stage):
            self._update_room_collision()

    def mode_state(
        self, now: float, assembly_end: float | None, *, hero_select_ready: bool = True,
        viewer_team: int = escort.DEFENDERS,
    ) -> escort.ModeState:
        point = self.capture.mode_state(now, assembly_end, hero_select_ready=hero_select_ready,
                                        viewer_team=viewer_team)
        if not self.capture.captured:
            return escort.ModeState(
                {**point.entity_vars, 0x3058: (self.goals, ())},
                {**point.instance_vars, 0x3058: (self.goals, ())},
                point.states,
            )

        cart = super().mode_state(now, assembly_end, hero_select_ready=hero_select_ready,
                                  viewer_team=viewer_team)
        score = (0, self.room_stage)
        goal = self.goals[cart.stage]
        shared = {
            0x09C3: (score, ()),
            0x26AD: (None, ()),
            0x2931: (escort.PAYLOAD_OBJECTIVE, ()),
            0x2934: (cart.stage, ()),
            0x2937: (goal, ()),
            0x3058: (self.goals, ()),
        }
        entity = {
            **point.entity_vars, **cart.entity_vars, **shared,
            0x1D65: (self.winner, ()),
            0x3101: (score, ()),
            0x34F3: (round((cart.stage + cart.entity_vars[0x349C][0]) / len(self.goals), 4), ()),
        }
        instance = {
            **point.instance_vars, **cart.instance_vars, **shared,
            0x0517: (cart.seconds, ()),
            0x0D40: (self.overtime, ()),
            0x1B3B: cart.entity_vars[0x1B3B],
            0x2A36: (cart.stage, ()),
            0x349C: cart.entity_vars[0x349C],
        }
        states = cart.states | ({17} if self.phase == escort.Phase.ACTIVE else set())
        return escort.ModeState(entity, instance, states)

    def payload_state(self) -> escort.PayloadState:
        cart = super().payload_state()
        moving_allowed = bool(self.capture.captured and self.last_update >= self.move_at)
        unlocking = self.capture.captured and self.unlock_at <= self.last_update < self.move_at
        animation = cart.animation
        if unlocking:
            animation = escort.PAYLOAD_UNLOCK_ANIM
        elif not moving_allowed:
            animation = escort.PAYLOAD_LOCKED_ANIM
        captured = bool(self.capture.captured)
        return replace(cart, active=cart.active and captured, enabled=cart.enabled and captured,
                       animation=animation, unlock_seconds=self.unlock_seconds,
                       unlock_played=moving_allowed or unlocking)
