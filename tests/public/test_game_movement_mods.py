"""Movement mods the owner's client cannot predict: channel 2 direction vectors and server-only states."""

import math
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from ow174.game import correction, movelog
from ow174.game.commands import Command
from ow174.game.mover import (
    ADD,
    MOVE_DIRECTION,
    SET,
    SPEED,
    THROTTLE_FORWARD,
    Mod,
    Mover,
    MoverData,
    directed_throttles,
    f32,
    server_only_mods,
    statescript_mods,
)
from ow174.game.script import runtime
from tests.script_fixtures import graph, node

TICK = 0.016
YAW_EAST = 16384  # a quarter turn: forward (1, 0, 0)
NORTH = (0.0, 1.0)  # the axes (sin yaw, cos yaw) of yaw 0: forward (0, 0, 1)
SPEED_ROW = {"$": "STU_56DEE1BB", "m_A9561CA0": 0, "m_9B7A63EA": {"$": "STUConfigVarFloat", "m_value": 2.0}}


def vec3(x, y, z):
    return {"$": "STUConfigVarVec3", "m_value": {"X": x, "Y": y, "Z": z}}


def direction(value, channel=2):
    return {"$": "STU_09F9B1FE", "m_CFEC5F76": channel, "m_9B7A63EA": value, "m_priority": None}


def movement_mod(rows, *, server_only=False):
    """A body component with one MovementMod state on."""
    record = node(0, "STU_316CFEF2", state=0, m_BC5E91CF=rows)
    record["server_only"] = server_only
    component = runtime.Component(0xA0000101)
    instance = component.create(graph([record]), init=False)
    instance.initialized = True
    instance.state(0).request(runtime.BEGIN)
    component.run_now()
    return component, instance


class DirectedThrottleTests(unittest.TestCase):
    def test_no_direction_or_the_facing_itself_leaves_the_throttles(self):
        self.assertEqual(directed_throttles([], 0.25, 1.0, NORTH), (0.25, 1.0))
        same = Mod(MOVE_DIRECTION, SET, (0.0, 0.0, 1.0), 0)
        self.assertEqual(directed_throttles([same], 0.25, 1.0, NORTH), (0.25, 1.0))
        speed = Mod(SPEED, SET, 8.0, 0)
        self.assertEqual(directed_throttles([speed], 0.25, 1.0, NORTH), (0.25, 1.0))
        # Any other value is turned through the angles, as the client compares before normalising.
        longer = Mod(MOVE_DIRECTION, SET, (0.0, 0.0, 3.0), 0)
        right, forward = directed_throttles([longer], 0.25, 1.0, NORTH)
        self.assertNotEqual((right, forward), (0.25, 1.0))
        self.assertAlmostEqual(right, 0.25, places=6)
        self.assertAlmostEqual(forward, 1.0, places=6)

    def test_forward_throttle_follows_the_set_direction_and_keeps_its_length(self):
        east = Mod(MOVE_DIRECTION, SET, (4.0, 0.0, 0.0), 0)
        right, forward = directed_throttles([east], 0.0, 1.0, NORTH)
        # Right is (-cos yaw, 0, sin yaw): -1 right at yaw 0 is +x.
        self.assertEqual(right, -1.0)
        self.assertAlmostEqual(forward, 0.0, places=6)
        right, forward = directed_throttles([east], 0.5, 0.5, NORTH)
        self.assertAlmostEqual(math.hypot(right, forward), math.hypot(0.5, 0.5), places=6)
        # The throttles keep their angle to the new forward: right of east is south.
        self.assertAlmostEqual(math.atan2(right, forward), f32(math.atan2(0.5, 0.5) - math.pi / 2), places=6)

    def test_the_direction_is_made_flat_and_unit_with_north_for_none(self):
        steep = Mod(MOVE_DIRECTION, SET, (3.0, 50.0, 0.0), 0)
        self.assertEqual(directed_throttles([steep], 0.0, 1.0, NORTH)[0], -1.0)
        east_axes = (1.0, 0.0)
        none = Mod(MOVE_DIRECTION, SET, (0.0, 7.0, 0.0), 0)
        right, forward = directed_throttles([none], 0.0, 1.0, east_axes)
        self.assertEqual(right, 1.0)  # facing east, north is to the left: right of yaw 90 is (0, 0, -1)
        self.assertAlmostEqual(forward, 0.0, places=6)

    def test_add_and_multiply_act_on_the_unit_facing_in_order(self):
        add = Mod(MOVE_DIRECTION, ADD, (1.0, 0.0, 0.0), 0)
        right, forward = directed_throttles([add], 0.0, 1.0, NORTH)  # (1, 0, 1): north-east
        self.assertAlmostEqual(math.atan2(-right, forward), math.pi / 4, places=6)


class DirectedMoverTests(unittest.TestCase):
    def test_a_direction_mod_moves_the_body_along_it_whatever_the_yaw(self):
        mover = Mover((0.0, 1.0, 0.0), data=MoverData())
        mover.track_mods({
            (1, 0, 0): (THROTTLE_FORWARD, SET, 1.0, 0.0),
            (1, 0, 1): (MOVE_DIRECTION, SET, (0.0, 0.0, 1.0), 0.0),
        }, 10)
        for frame in range(11, 31):
            mover.step(Command(frame, yaw=YAW_EAST), TICK, frame)
        x, _, z = mover.position
        self.assertGreater(z, 1.5)
        self.assertAlmostEqual(x, 0.0, places=2)
        mover.track_mods({}, 30)
        mover.step(Command(31, yaw=YAW_EAST, forward=127), TICK, 31)
        self.assertGreater(mover.velocity[0], 0.0)  # the mod is off: forward is the facing again


class StatescriptModTests(unittest.TestCase):
    def test_a_channel_2_vector_row_is_a_direction_mod(self):
        rows = [
            SPEED_ROW,
            direction(vec3(3.0, 0.0, 4.0)),
            direction(vec3(1.0, 0.0, 0.0), channel=0),
            direction({"$": "STUConfigVarFloat", "m_value": 1.0}),
        ]
        component, instance = movement_mod(rows)
        self.assertEqual(statescript_mods(component), {
            (instance.id, 0, 0): (SPEED, SET, 2.0, 0.0),
            (instance.id, 0, 1): (MOVE_DIRECTION, SET, (3.0, 0.0, 4.0), 0.0),
        })

    def test_only_mods_of_a_server_only_state_are_server_only(self):
        rows = [SPEED_ROW]
        component, _ = movement_mod(rows)
        self.assertFalse(server_only_mods(component, statescript_mods(component)))
        component, _ = movement_mod(rows, server_only=True)
        self.assertTrue(server_only_mods(component, statescript_mods(component)))
        self.assertFalse(server_only_mods(component, {}))


class ServerMotionRecordTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(movelog, "ENABLED", False))
        self.player = SimpleNamespace(
            has_body=True, body=0x1234, quanta=16000, impulse_correction=None,
            body_script=SimpleNamespace(create_arrived=True), hero=SimpleNamespace(guid=0x02E000000000012F),
            mover=Mover((0.0, 1.0, 0.0), data=MoverData()), commands=SimpleNamespace(last_frame=None),
        )

    def step(self, frame):
        self.player.mover.step(Command(frame, forward=127), TICK, frame)
        self.player.commands.last_frame = frame

    def test_the_window_runs_from_the_next_frame_through_a_quarter_second_tail(self):
        correction.server_motion(self.player, 100)
        self.assertEqual(self.player.impulse_correction, (101, 116))
        correction.server_motion(self.player, 104)
        self.assertEqual(self.player.impulse_correction, (101, 120))
        with patch.object(correction, "CORRECTIONS", False):
            other = SimpleNamespace(**vars(self.player))
            other.impulse_correction = None
            correction.server_motion(other, 104)
        self.assertIsNone(other.impulse_correction)

    def test_the_ack_keeps_the_commands_a_replay_needs_until_the_window_closes(self):
        for frame in range(95, 101):
            self.step(frame)
        self.assertIsNone(correction.own_record(self.player, 100))  # not a corrected hero, no window
        self.assertEqual(correction.command_ack(self.player, 100), 100)
        correction.server_motion(self.player, 100)
        for frame in range(101, 105):
            self.step(frame)
        # No record has gone yet: the client keeps the commands from the window's first frame.
        self.assertEqual(correction.command_ack(self.player, 104), 100 + correction.ACK_LEAD)
        for frame in range(105, 130):
            self.step(frame)
            update = correction.own_record(self.player, frame)
            if update is not None:
                correction.sent(self.player, update)
        self.assertIsNone(self.player.impulse_correction)
        self.assertEqual(correction.command_ack(self.player, 129), 129)


def punch_component(index=0x0D06):
    """Rocket Punch's charge (#23) and the dash's registration (#138), both off."""
    definition = graph([
        node(23, "STUStatescriptStateChaseVar", state=0),
        node(138, "STU_4773DA56", state=1),
        node(140, "STU_TEST_OTHER", state=2),
    ])
    definition.index = index
    component = runtime.Component(0xA0000101)
    instance = component.create(definition, init=False)
    instance.initialized = True
    return component, instance


class RegistrationViewTests(unittest.TestCase):
    """While a STU_4773DA56 registration may be on, the owner gets its body's records."""

    def test_the_charge_or_a_registration_opens_the_window(self):
        for index in (0, 1):
            component, instance = punch_component()
            self.assertFalse(correction.registration_view(component))
            instance.state(index).active = True
            self.assertTrue(correction.registration_view(component))

    def test_another_state_or_the_same_position_in_another_graph_does_not(self):
        component, instance = punch_component()
        instance.state(2).active = True
        self.assertFalse(correction.registration_view(component))
        component, instance = punch_component(0x01C9)
        instance.state(0).active = True
        self.assertFalse(correction.registration_view(component))
        instance.state(1).active = True  # Roadhog's hook victim registration, say
        self.assertTrue(correction.registration_view(component))


if __name__ == "__main__":
    unittest.main()
